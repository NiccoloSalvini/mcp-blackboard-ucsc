# App-token rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the MCP server run on the app's own OAuth2 token, fix the tools that were wrong under the old constraints, and add the tools the new access unlocks.

**Architecture:** Split `server.py` into `client.py` (auth, HTTP, paging, BBML), `export.py` (upload-questions file) and `server.py` (tools). Tools call `client._request` through the module so tests replace it with a fake. No new dependencies.

**Tech Stack:** Python 3.12, `mcp` 2.x (`MCPServer`), `httpx`, `pytest` (dev). Run everything with `PYTHONPATH=src .venv/bin/python -m pytest` — never `uv run` (it re-hides the venv `.pth` files).

Spec: `docs/superpowers/specs/2026-09-18-app-token-rewrite-design.md`.

---

### Task 1: `client.py` — auth with one retry on 401, request, paging, BBML

**Files:**
- Create: `src/bb_mcp/client.py`
- Test: `tests/test_client.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_client.py
import asyncio
import httpx
import pytest

from bb_mcp import client


def run(coro):
    return asyncio.run(coro)


class FakeClient:
    """Stands in for httpx.AsyncClient: scripted responses, records calls."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return self.responses.pop(0)

    async def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.responses.pop(0)


def resp(status, json=None, text=""):
    return httpx.Response(status, json=json, text=text if json is None else None,
                          request=httpx.Request("GET", "https://x"))


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    monkeypatch.setattr(client, "APP_KEY", "k")
    monkeypatch.setattr(client, "APP_SECRET", "s")
    monkeypatch.setattr(client, "ALLOW_WRITES", False)
    monkeypatch.setattr(client, "ALLOW_GRADE_WRITES", False)
    monkeypatch.setattr(client, "_token", None)
    monkeypatch.setattr(client, "_token_expires_at", 0.0)


def test_token_is_fetched_once_and_cached(monkeypatch):
    fake = FakeClient([
        resp(200, {"access_token": "t1", "expires_in": 3600}),
        resp(200, {"ok": 1}),
        resp(200, {"ok": 2}),
    ])
    monkeypatch.setattr(client.httpx, "AsyncClient", lambda **kw: fake)
    run(client._request("GET", "/a"))
    run(client._request("GET", "/b"))
    posts = [c for c in fake.calls if c[0] == "POST"]
    assert len(posts) == 1
    assert fake.calls[-1][2]["headers"]["Authorization"] == "Bearer t1"


def test_401_refreshes_token_and_retries_once(monkeypatch):
    fake = FakeClient([
        resp(200, {"access_token": "old", "expires_in": 3600}),
        resp(401, text="API request is not authenticated"),
        resp(200, {"access_token": "new", "expires_in": 3600}),
        resp(200, {"ok": True}),
    ])
    monkeypatch.setattr(client.httpx, "AsyncClient", lambda **kw: fake)
    out = run(client._request("GET", "/x"))
    assert out == {"ok": True}
    assert fake.calls[-1][2]["headers"]["Authorization"] == "Bearer new"


def test_second_401_raises(monkeypatch):
    fake = FakeClient([
        resp(200, {"access_token": "a", "expires_in": 3600}),
        resp(401, text="nope"),
        resp(200, {"access_token": "b", "expires_in": 3600}),
        resp(401, text="still nope"),
    ])
    monkeypatch.setattr(client.httpx, "AsyncClient", lambda **kw: fake)
    with pytest.raises(client.BlackboardError, match="401"):
        run(client._request("GET", "/x"))


def test_write_switch_checked_before_network(monkeypatch):
    fake = FakeClient([])
    monkeypatch.setattr(client.httpx, "AsyncClient", lambda **kw: fake)
    with pytest.raises(client.BlackboardError, match="BB_ALLOW_WRITES"):
        run(client._request("POST", "/x", json={}, write=True))
    assert fake.calls == []


def test_paged_follows_next_and_stops_at_limit(monkeypatch):
    pages = {
        "/v1/items": {"results": [1, 2], "paging": {"nextPage": "/learn/api/public/v1/items?offset=2"}},
        "/v1/items?offset=2": {"results": [3, 4], "paging": {"nextPage": "/learn/api/public/v1/items?offset=4"}},
        "/v1/items?offset=4": {"results": [5]},
    }
    seen = []

    async def fake_request(method, path, **kw):
        seen.append(path)
        return pages[path]

    monkeypatch.setattr(client, "_request", fake_request)
    assert run(client._paged("/v1/items")) == [1, 2, 3, 4, 5]
    assert run(client._paged("/v1/items", limit=3)) == [1, 2, 3]


def test_bbml_rejects_b_and_names_replacement():
    with pytest.raises(client.BlackboardError, match=r"<b> \(use <strong>\)"):
        client.check_bbml("<p><b>x</b></p>")
    client.check_bbml("<p><strong>x</strong> <a href='u'>y</a></p>")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_client.py -q`
Expected: `ModuleNotFoundError: No module named 'bb_mcp.client'`

- [ ] **Step 3: Write `client.py`**

```python
# src/bb_mcp/client.py
"""HTTP side of the Blackboard server: token, requests, paging, BBML.

Auth is OAuth2 client credentials only. The application is registered on the
site and bound to the instructor's user, so the token carries that user's
permissions and lives an hour. Tokens are cached and fetched again once when
a request answers 401, which is what happens when one expires mid-call.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

import httpx


def _load_dotenv() -> None:
    """Read .env at the project root. Existing environment variables win."""
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_dotenv()

BASE_URL = os.environ.get("BB_BASE_URL", "https://blackboard.unicatt.it").rstrip("/")
APP_KEY = os.environ.get("BB_APP_KEY", "")
APP_SECRET = os.environ.get("BB_APP_SECRET", "")
ALLOW_WRITES = os.environ.get("BB_ALLOW_WRITES", "0") == "1"
ALLOW_GRADE_WRITES = os.environ.get("BB_ALLOW_GRADE_WRITES", "0") == "1"
API = f"{BASE_URL}/learn/api/public"


class BlackboardError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------

_token: str | None = None
_token_expires_at: float = 0.0


async def _access_token(client: httpx.AsyncClient, *, force: bool = False) -> str:
    global _token, _token_expires_at

    if not force and _token and time.time() < _token_expires_at:
        return _token

    if not APP_KEY or not APP_SECRET:
        raise BlackboardError(
            "Set BB_APP_KEY / BB_APP_SECRET in .env: the key and secret of the "
            "application registered at developer.anthology.com and added by the "
            "Blackboard administrator under Admin > REST API Integrations."
        )

    resp = await client.post(
        f"{API}/v1/oauth2/token",
        auth=(APP_KEY, APP_SECRET),
        data={"grant_type": "client_credentials"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if resp.status_code != 200:
        raise BlackboardError(f"Token request failed ({resp.status_code}): {resp.text[:400]}")

    payload = resp.json()
    _token = payload["access_token"]
    _token_expires_at = time.time() + payload.get("expires_in", 3600) - 60
    return _token


async def _request(
    method: str,
    path: str,
    *,
    json: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    content: bytes | None = None,
    content_type: str | None = None,
    write: bool = False,
    grade_write: bool = False,
) -> Any:
    """One call to the public API. Raises BlackboardError on any 4xx/5xx."""
    if grade_write and not ALLOW_GRADE_WRITES:
        raise BlackboardError("Grade writes are disabled. Set BB_ALLOW_GRADE_WRITES=1 to allow them.")
    if write and not ALLOW_WRITES:
        raise BlackboardError("Writes are disabled. Set BB_ALLOW_WRITES=1 to allow them.")

    async with httpx.AsyncClient(timeout=60) as client:
        token = await _access_token(client)
        resp = await _send(client, token, method, path, json, params, content, content_type)
        if resp.status_code == 401:
            token = await _access_token(client, force=True)
            resp = await _send(client, token, method, path, json, params, content, content_type)

    if resp.status_code == 404:
        raise BlackboardError(f"Not found: {method} {path}")
    if resp.status_code >= 400:
        raise BlackboardError(f"{resp.status_code} on {method} {path}: {resp.text[:500]}")
    if not resp.content:
        return {"ok": True}
    return resp.json()


async def _send(client, token, method, path, json, params, content, content_type) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"}
    if content_type:
        headers["Content-Type"] = content_type
    return await client.request(
        method, f"{API}{path}", headers=headers, json=json, params=params, content=content,
    )


async def _paged(path: str, params: dict[str, Any] | None = None, limit: int = 200) -> list[dict]:
    """Follow Blackboard's paging links until exhausted or `limit` rows collected."""
    out: list[dict] = []
    query = dict(params or {})
    query.setdefault("limit", 100)
    next_path: str | None = path

    while next_path and len(out) < limit:
        payload = await _request("GET", next_path, params=query)
        out.extend(payload.get("results", []))
        next_url = payload.get("paging", {}).get("nextPage")
        next_path = next_url.replace("/learn/api/public", "") if next_url else None
        query = {}

    return out[:limit]


# --------------------------------------------------------------------------
# Blackboard Markup Language
# --------------------------------------------------------------------------

# The subset of HTML a content or announcement body may contain. Anything else
# is a 400 that quotes the whole body and names no tag, so the check is here.
BBML_TAGS = {"a", "br", "del", "div", "em", "h4", "h5", "h6", "li", "ol", "p",
             "span", "strong", "sub", "sup", "ul"}
_BBML_HINT = {"b": "strong", "i": "em", "h1": "h4", "h2": "h4", "h3": "h4"}


def check_bbml(html: str) -> None:
    used = set(re.findall(r"</?([a-zA-Z][a-zA-Z0-9]*)", html))
    bad = sorted(t for t in used if t.lower() not in BBML_TAGS)
    if bad:
        hints = ", ".join(f"<{t}> (use <{_BBML_HINT[t]}>)" if t in _BBML_HINT else f"<{t}>" for t in bad)
        raise BlackboardError(
            f"Body uses tags outside Blackboard Markup Language: {hints}. "
            f"Allowed: {', '.join(sorted(BBML_TAGS))}."
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_client.py -q`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add src/bb_mcp/client.py tests/test_client.py pyproject.toml uv.lock
git commit -m "Move auth, requests and paging into client.py; retry once on 401"
```

---

### Task 2: `export.py` — move the upload-questions builder

**Files:**
- Create: `src/bb_mcp/export.py`
- Test: `tests/test_export.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_export.py
import pytest

from bb_mcp import export
from bb_mcp.client import BlackboardError


def test_mc_line_has_marker_text_and_answer_pairs():
    q = {"type": "MC", "text": "2+2?", "answers": [{"text": "4", "correct": True}, {"text": "5"}]}
    assert export.question_line(q, 1) == "MC\t2+2?\t4\tcorrect\t5\tincorrect"


def test_mc_needs_exactly_one_correct():
    q = {"type": "MC", "text": "x", "answers": [{"text": "a"}, {"text": "b"}]}
    with pytest.raises(BlackboardError, match="exactly one correct"):
        export.question_line(q, 3)


def test_tabs_and_newlines_are_flattened():
    q = {"type": "ESS", "text": "line\tone\nline two"}
    assert export.question_line(q, 1) == "ESS\tline one line two"


def test_write_test_caps_at_250(tmp_path):
    qs = [{"type": "TF", "text": f"q{i}", "correct": True} for i in range(251)]
    with pytest.raises(BlackboardError, match="250"):
        export.write_test(qs, tmp_path / "t.txt")


def test_write_test_forces_txt_and_counts(tmp_path):
    qs = [{"type": "TF", "text": "a", "correct": True}, {"type": "ESS", "text": "b"}]
    out = export.write_test(qs, tmp_path / "exam.csv")
    assert out["path"].endswith("exam.txt")
    assert out["by_type"] == {"TF": 1, "ESS": 1}
    assert (tmp_path / "exam.txt").read_text() == "TF\ta\ttrue\nESS\tb\n"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_export.py -q`
Expected: `ModuleNotFoundError: No module named 'bb_mcp.export'`

- [ ] **Step 3: Write `export.py`** — the code moves from the old `server.py` unchanged except for names: `_bbq_line` → `question_line`, the body of `bb_export_test` → `write_test(questions, path) -> dict`.

```python
# src/bb_mcp/export.py
"""A test as the tab-delimited file Ultra accepts under Test > Upload Questions.

The public API cannot write question content on Ultra: questions come back as
opaque blocks. What Ultra does accept is this file — one question per line, no
header, at most 250 rows, answer markers in lowercase English. Points do not
travel in it: every question lands at 0 and gets its value after upload.
"""

from __future__ import annotations

import pathlib
import re
from typing import Any

from .client import BlackboardError

MAX_ROWS = 250
MAX_ANSWERS = 100


def _clean(text: Any) -> str:
    """One field: no tabs or newlines, or the row splits in the wrong place."""
    return re.sub(r"[\t\r\n]+", " ", str(text)).strip()


def question_line(q: dict[str, Any], n: int) -> str:
    kind = str(q.get("type", "")).upper()
    text = _clean(q.get("text", ""))
    if not text:
        raise BlackboardError(f"question {n}: empty text")
    answers = q.get("answers") or []

    if kind in ("MC", "MA"):
        if not answers:
            raise BlackboardError(f"question {n} ({kind}): needs answers")
        if len(answers) > MAX_ANSWERS:
            raise BlackboardError(f"question {n}: at most {MAX_ANSWERS} answers")
        correct = sum(1 for a in answers if a.get("correct"))
        if kind == "MC" and correct != 1:
            raise BlackboardError(f"question {n} (MC): exactly one correct answer, got {correct}")
        if kind == "MA" and correct < 1:
            raise BlackboardError(f"question {n} (MA): at least one correct answer")
        cells = [kind, text]
        for a in answers:
            cells += [_clean(a.get("text", "")), "correct" if a.get("correct") else "incorrect"]
        return "\t".join(cells)

    if kind == "TF":
        if "correct" not in q:
            raise BlackboardError(f"question {n} (TF): set correct: true|false")
        return "\t".join(["TF", text, "true" if q["correct"] else "false"])

    if kind == "ESS":
        cells = ["ESS", text]
        if q.get("example"):
            cells.append(_clean(q["example"]))
        return "\t".join(cells)

    if kind == "NUM":
        if "answer" not in q:
            raise BlackboardError(f"question {n} (NUM): needs answer")
        cells = ["NUM", text, str(q["answer"])]
        if q.get("tolerance") is not None:
            cells.append(str(q["tolerance"]))
        return "\t".join(cells)

    if kind == "FIB":
        if not answers:
            raise BlackboardError(f"question {n} (FIB): needs accepted answers")
        return "\t".join(["FIB", text] + [_clean(a if isinstance(a, str) else a.get("text", "")) for a in answers])

    raise BlackboardError(f"question {n}: unknown type {kind!r}; use MC, MA, TF, ESS, NUM or FIB")


def write_test(questions: list[dict[str, Any]], path: str | pathlib.Path) -> dict[str, Any]:
    if not questions:
        raise BlackboardError("no questions")
    if len(questions) > MAX_ROWS:
        raise BlackboardError(f"Blackboard takes at most {MAX_ROWS} questions per file; got {len(questions)}. Split it.")
    lines = [question_line(q, i + 1) for i, q in enumerate(questions)]
    out = pathlib.Path(path).expanduser()
    if out.suffix.lower() != ".txt":
        out = out.with_suffix(".txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    by_type: dict[str, int] = {}
    for ln in lines:
        kind = ln.split("\t", 1)[0]
        by_type[kind] = by_type.get(kind, 0) + 1
    return {
        "path": str(out),
        "questions": len(lines),
        "by_type": by_type,
        "points": "NOT in the file — set them in the test after upload; all arrive at 0",
        "next": "Ultra: open the test > + > Upload Questions > this file",
    }
```

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_export.py -q`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add src/bb_mcp/export.py tests/test_export.py
git commit -m "Move the upload-questions file builder into export.py"
```

---

### Task 3: `server.py` — tools on the new client, students with names, new read tools

**Files:**
- Rewrite: `src/bb_mcp/server.py`
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_tools.py
import asyncio

import pytest

from bb_mcp import client, server


def run(coro):
    return asyncio.run(coro)


def fake_api(routes):
    """routes: {(method, path): payload or callable(params, json)}. Records calls."""
    calls = []

    async def _request(method, path, *, json=None, params=None, **kw):
        calls.append((method, path, params, json))
        hit = routes[(method, path)]
        return hit(params, json) if callable(hit) else hit

    return _request, calls


def test_list_students_returns_names_and_emails(monkeypatch):
    req, calls = fake_api({
        ("GET", "/v1/courses/_1_1/users"): {"results": [
            {"userId": "_9_1", "courseRoleId": "Student",
             "user": {"name": {"given": "Ada", "family": "Lovelace"}, "studentId": "123",
                      "contact": {"email": "ada@icatt.it"}}},
            {"userId": "_8_1", "courseRoleId": "Instructor",
             "user": {"name": {"given": "N", "family": "S"}}},
        ]},
    })
    monkeypatch.setattr(client, "_request", req)
    rows = run(server.bb_list_students("_1_1"))
    assert rows[0] == {"userId": "_9_1", "name": "Ada Lovelace", "email": "ada@icatt.it",
                       "studentId": "123", "role": "Student"}
    assert rows[1]["email"] is None
    assert calls[0][2]["expand"] == "user"
    assert run(server.bb_list_students("_1_1", role="Student")) == rows[:1]


def test_content_tree_respects_depth(monkeypatch):
    req, _ = fake_api({
        ("GET", "/v1/courses/_1_1/contents"): {"results": [
            {"id": "f", "title": "Folder", "hasChildren": True,
             "contentHandler": {"id": "resource/x-bb-folder"}, "availability": {"available": "Yes"}}]},
        ("GET", "/v1/courses/_1_1/contents/f/children"): {"results": [
            {"id": "g", "title": "Sub", "hasChildren": True,
             "contentHandler": {"id": "resource/x-bb-folder"}, "availability": {"available": "No"}}]},
        ("GET", "/v1/courses/_1_1/contents/g/children"): {"results": [
            {"id": "d", "title": "Doc", "hasChildren": False,
             "contentHandler": {"id": "resource/x-bb-document"}, "availability": {"available": "Yes"}}]},
    })
    monkeypatch.setattr(client, "_request", req)
    tree = run(server.bb_content_tree("_1_1", max_depth=2))
    assert tree[0]["children"][0]["title"] == "Sub"
    assert tree[0]["children"][0]["children"] == []
    assert tree[0]["children"][0]["available"] == "No"
    full = run(server.bb_content_tree("_1_1", max_depth=3))
    assert full[0]["children"][0]["children"][0]["id"] == "d"


def test_list_grades_joins_scores_with_students(monkeypatch):
    req, _ = fake_api({
        ("GET", "/v1/courses/_1_1/users"): {"results": [
            {"userId": "_9_1", "courseRoleId": "Student",
             "user": {"name": {"given": "Ada", "family": "Lovelace"}, "studentId": "123",
                      "contact": {"email": "ada@icatt.it"}}},
            {"userId": "_7_1", "courseRoleId": "Student",
             "user": {"name": {"given": "No", "family": "Score"}}},
        ]},
        ("GET", "/v2/courses/_1_1/gradebook/columns/_c_1"): {"id": "_c_1", "name": "Exam",
                                                              "score": {"possible": 30}},
        ("GET", "/v2/courses/_1_1/gradebook/columns/_c_1/users"): {"results": [
            {"userId": "_9_1", "status": "Graded", "exempt": False,
             "displayGrade": {"score": 26.25, "possible": 30}},
        ]},
    })
    monkeypatch.setattr(client, "_request", req)
    out = run(server.bb_list_grades("_1_1", "_c_1"))
    assert out["column"] == {"id": "_c_1", "name": "Exam", "possible": 30}
    assert out["rows"][0]["score"] == 26.25 and out["rows"][0]["name"] == "Ada Lovelace"
    assert out["rows"][1] == {"userId": "_7_1", "name": "No Score", "email": None, "studentId": None,
                              "status": None, "score": None, "exempt": None}


def test_gradebook_report_one_row_per_student(monkeypatch):
    req, _ = fake_api({
        ("GET", "/v1/courses/_1_1/users"): {"results": [
            {"userId": "_9_1", "courseRoleId": "Student",
             "user": {"name": {"given": "Ada", "family": "L"}, "studentId": "1"}},
        ]},
        ("GET", "/v2/courses/_1_1/gradebook/columns"): {"results": [
            {"id": "_a_1", "name": "HW", "score": {"possible": 10}},
            {"id": "_b_1", "name": "Exam", "score": {"possible": 30}},
        ]},
        ("GET", "/v2/courses/_1_1/gradebook/columns/_a_1/users"): {"results": [
            {"userId": "_9_1", "displayGrade": {"score": 8}}]},
        ("GET", "/v2/courses/_1_1/gradebook/columns/_b_1/users"): {"results": []},
    })
    monkeypatch.setattr(client, "_request", req)
    rep = run(server.bb_gradebook_report("_1_1"))
    assert [c["name"] for c in rep["columns"]] == ["HW", "Exam"]
    assert rep["rows"] == [{"userId": "_9_1", "name": "Ada L", "email": None, "studentId": "1",
                            "scores": {"_a_1": 8, "_b_1": None}}]


def test_update_content_checks_bbml_and_sends_only_given_fields(monkeypatch):
    monkeypatch.setattr(client, "ALLOW_WRITES", True)
    req, calls = fake_api({("PATCH", "/v1/courses/_1_1/contents/_d_1"): {"id": "_d_1"}})
    monkeypatch.setattr(client, "_request", req)
    run(server.bb_update_content("_1_1", "_d_1", body_html="<p>ok</p>"))
    assert calls[0][3] == {"body": "<p>ok</p>"}
    with pytest.raises(client.BlackboardError, match="<i>"):
        run(server.bb_update_content("_1_1", "_d_1", body_html="<i>x</i>"))
    with pytest.raises(client.BlackboardError, match="nothing to change"):
        run(server.bb_update_content("_1_1", "_d_1"))


def test_whoami_merges_user_and_scope(monkeypatch):
    req, _ = fake_api({
        ("GET", "/v1/users/me"): {"id": "_u_1", "userName": "me", "name": {"given": "N", "family": "S"}},
    })
    monkeypatch.setattr(client, "_request", req)
    assert run(server.bb_whoami()) == {"id": "_u_1", "userName": "me", "name": "N S",
                                       "auth": "client-credentials"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_tools.py -q`
Expected: failures — `AttributeError: module 'bb_mcp.server' has no attribute 'bb_content_tree'` and the old `bb_list_students` returning `name: None`.

- [ ] **Step 3: Rewrite `server.py`**

```python
# src/bb_mcp/server.py
"""MCP server for Blackboard Learn Ultra at Università Cattolica.

Talks to the documented public REST API with the application's own OAuth2
token, which the site administrator bound to the instructor's user. Reads
are always on. Writes are off unless BB_ALLOW_WRITES=1, and grade writes need
BB_ALLOW_GRADE_WRITES=1 on top, because a wrong number in the official
gradebook is the one mistake here that reaches students directly.
"""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from . import client as bb
from . import export
from .client import BlackboardError, check_bbml

mcp = MCPServer("blackboard")

TEST_HANDLER = "resource/x-bb-asmt-test-link"


def _name(user: dict | None) -> str | None:
    n = (user or {}).get("name") or {}
    full = " ".join(p for p in (n.get("given"), n.get("family")) if p)
    return full or None


def _contents_path(course_id: str, parent_id: str) -> str:
    return (
        f"/v1/courses/{course_id}/contents/{parent_id}/children"
        if parent_id
        else f"/v1/courses/{course_id}/contents"
    )


async def _students(course_id: str) -> list[dict]:
    rows = await bb._paged(
        f"/v1/courses/{course_id}/users",
        {"expand": "user",
         "fields": "userId,courseRoleId,user.name,user.studentId,user.contact.email"},
    )
    return [
        {
            "userId": r.get("userId"),
            "name": _name(r.get("user")),
            "email": ((r.get("user") or {}).get("contact") or {}).get("email"),
            "studentId": (r.get("user") or {}).get("studentId"),
            "role": r.get("courseRoleId"),
        }
        for r in rows
    ]


# --------------------------------------------------------------------------
# read — account and course
# --------------------------------------------------------------------------


@mcp.tool()
async def bb_whoami() -> dict:
    """Who the token acts as. Use this first to confirm auth works."""
    me = await bb._request("GET", "/v1/users/me")
    return {"id": me.get("id"), "userName": me.get("userName"), "name": _name(me),
            "auth": "client-credentials"}


@mcp.tool()
async def bb_config() -> dict:
    """What this server is pointed at and what it is allowed to do."""
    return {
        "base_url": bb.BASE_URL,
        "auth": "client-credentials",
        "credentials_present": bool(bb.APP_KEY and bb.APP_SECRET),
        "writes_enabled": bb.ALLOW_WRITES,
        "grade_writes_enabled": bb.ALLOW_GRADE_WRITES,
    }


@mcp.tool()
async def bb_list_courses(search: str = "") -> list[dict]:
    """List courses visible to the account. `search` filters on course name."""
    params = {"name": search} if search else None
    rows = await bb._paged("/v3/courses", params)
    return [{"id": c.get("id"), "courseId": c.get("courseId"), "name": c.get("name"),
             "termId": c.get("termId"), "available": (c.get("availability") or {}).get("available")}
            for c in rows]


@mcp.tool()
async def bb_get_course(course_id: str) -> dict:
    """One course: name, term, availability, the URL students open."""
    c = await bb._request("GET", f"/v3/courses/{course_id}")
    return {
        "id": c.get("id"), "courseId": c.get("courseId"), "name": c.get("name"),
        "termId": c.get("termId"), "ultraStatus": c.get("ultraStatus"),
        "available": (c.get("availability") or {}).get("available"),
        "url": c.get("externalAccessUrl"),
        "created": c.get("created"), "modified": c.get("modified"),
    }


@mcp.tool()
async def bb_list_students(course_id: str, role: str = "") -> list[dict]:
    """Enrolled users with name, email, student number and course role.

    `role` filters: Student, Instructor, TeachingAssistant, Grader, Guest.
    """
    rows = await _students(course_id)
    return [r for r in rows if not role or r["role"] == role]


# --------------------------------------------------------------------------
# read — content
# --------------------------------------------------------------------------


def _item(r: dict) -> dict:
    return {"id": r.get("id"), "title": r.get("title"),
            "type": (r.get("contentHandler") or {}).get("id"),
            "available": (r.get("availability") or {}).get("available"),
            "hasChildren": bool(r.get("hasChildren"))}


@mcp.tool()
async def bb_list_contents(course_id: str, folder_id: str = "") -> list[dict]:
    """Content items at the top of a course, or inside one folder."""
    rows = await bb._paged(_contents_path(course_id, folder_id))
    return [_item(r) for r in rows]


@mcp.tool()
async def bb_content_tree(course_id: str, max_depth: int = 3) -> list[dict]:
    """The whole course outline as nested folders, down to `max_depth` levels.

    The Ultra outline panel renders blank in a browser session, so this is the
    way to see what a course actually holds. Hidden items are included with
    `available: "No"`.
    """
    async def walk(parent_id: str, depth: int) -> list[dict]:
        rows = await bb._paged(_contents_path(course_id, parent_id))
        out = []
        for r in rows:
            node = _item(r)
            node["children"] = (
                await walk(node["id"], depth + 1)
                if node["hasChildren"] and depth < max_depth else []
            )
            out.append(node)
        return out

    return await walk("", 1)


@mcp.tool()
async def bb_get_content(course_id: str, content_id: str) -> dict:
    """One content item in full: body (BBML), handler, availability, position."""
    return await bb._request("GET", f"/v1/courses/{course_id}/contents/{content_id}")


@mcp.tool()
async def bb_list_announcements(course_id: str) -> list[dict]:
    """Course announcements, newest first as Blackboard returns them."""
    rows = await bb._paged(f"/v1/courses/{course_id}/announcements")
    return [{"id": r.get("id"), "title": r.get("title"), "created": r.get("created"),
             "draft": r.get("draft"), "body": r.get("body")} for r in rows]


# --------------------------------------------------------------------------
# read — assessments and gradebook
# --------------------------------------------------------------------------


@mcp.tool()
async def bb_list_assessments(course_id: str) -> list[dict]:
    """Tests in the course, with the ids needed to read their questions and grades.

    There is no /assessments collection in the public API — a test is a content
    item whose handler is resource/x-bb-asmt-test-link. This walks the outline
    and keeps those, surfacing assessmentId (questions routes) and
    gradeColumnId (gradebook routes).
    """
    async def walk(parent_id: str) -> list[dict]:
        rows = await bb._paged(_contents_path(course_id, parent_id))
        out = []
        for c in rows:
            h = c.get("contentHandler") or {}
            if h.get("id") == TEST_HANDLER:
                out.append({
                    "content_id": c.get("id"), "title": c.get("title"),
                    "assessment_id": h.get("assessmentId"),
                    "grade_column_id": h.get("gradeColumnId"),
                    "available": (c.get("availability") or {}).get("available"),
                    "created": c.get("created"),
                })
            elif c.get("hasChildren"):
                out.extend(await walk(c["id"]))
        return out

    return await walk("")


@mcp.tool()
async def bb_list_questions(course_id: str, assessment_id: str) -> list[dict]:
    """Questions inside one assessment.

    On Ultra each row is an opaque block: id, position and
    questionHandler.type "QuestionBlock". Text and answers are not exposed.
    """
    return await bb._paged(f"/v1/courses/{course_id}/assessments/{assessment_id}/questions")


@mcp.tool()
async def bb_list_gradebook_columns(course_id: str) -> list[dict]:
    """Gradebook columns for a course."""
    rows = await bb._paged(f"/v2/courses/{course_id}/gradebook/columns")
    return [{"id": r.get("id"), "name": r.get("name"),
             "possible": (r.get("score") or {}).get("possible"),
             "grading": (r.get("grading") or {}).get("type"),
             "contentId": r.get("contentId")} for r in rows]


def _score_row(r: dict) -> dict:
    g = r.get("displayGrade") or {}
    return {"status": r.get("status"), "score": g.get("score"), "exempt": r.get("exempt")}


@mcp.tool()
async def bb_list_grades(course_id: str, column_id: str) -> dict:
    """Every student's score on one column, with names — the marks list for a test.

    Students with no entry yet appear with score null.
    """
    col = await bb._request("GET", f"/v2/courses/{course_id}/gradebook/columns/{column_id}")
    scores = {r["userId"]: _score_row(r) for r in
              await bb._paged(f"/v2/courses/{course_id}/gradebook/columns/{column_id}/users")}
    rows = []
    for s in await _students(course_id):
        if s["role"] != "Student":
            continue
        sc = scores.get(s["userId"], {"status": None, "score": None, "exempt": None})
        rows.append({"userId": s["userId"], "name": s["name"], "email": s["email"],
                     "studentId": s["studentId"], **sc})
    return {"column": {"id": col.get("id"), "name": col.get("name"),
                       "possible": (col.get("score") or {}).get("possible")},
            "rows": rows}


@mcp.tool()
async def bb_gradebook_report(course_id: str) -> dict:
    """The whole gradebook: one row per student, one key per column.

    One request per column, so a course with many columns takes a moment.
    """
    cols = await bb._paged(f"/v2/courses/{course_id}/gradebook/columns")
    columns = [{"id": c["id"], "name": c.get("name"),
                "possible": (c.get("score") or {}).get("possible")} for c in cols]
    by_col: dict[str, dict[str, Any]] = {}
    for c in columns:
        rows = await bb._paged(f"/v2/courses/{course_id}/gradebook/columns/{c['id']}/users")
        by_col[c["id"]] = {r["userId"]: (r.get("displayGrade") or {}).get("score") for r in rows}
    out = []
    for s in await _students(course_id):
        if s["role"] != "Student":
            continue
        out.append({"userId": s["userId"], "name": s["name"], "email": s["email"],
                    "studentId": s["studentId"],
                    "scores": {c["id"]: by_col[c["id"]].get(s["userId"]) for c in columns}})
    return {"columns": columns, "rows": out}


@mcp.tool()
async def bb_list_attempts(course_id: str, column_id: str) -> list[dict]:
    """Submissions on a gradebook column — status, score and timestamps."""
    rows = await bb._paged(f"/v2/courses/{course_id}/gradebook/columns/{column_id}/attempts")
    return [{"attemptId": r.get("id"), "userId": r.get("userId"), "status": r.get("status"),
             "score": r.get("score"), "created": r.get("created"),
             "attemptDate": r.get("attemptDate")} for r in rows]


@mcp.tool()
async def bb_get_attempt(course_id: str, column_id: str, attempt_id: str) -> dict:
    """One submission: status, readyToPost, score, created/attemptDate/modified.

    The public API does not return the student's answers for an Ultra test;
    those are only in the Ultra grading view.
    """
    return await bb._request(
        "GET", f"/v2/courses/{course_id}/gradebook/columns/{column_id}/attempts/{attempt_id}")


# --------------------------------------------------------------------------
# write — content and assessments (BB_ALLOW_WRITES=1)
# --------------------------------------------------------------------------


@mcp.tool()
async def bb_create_content(
    course_id: str,
    title: str,
    body_html: str = "",
    parent_id: str = "",
    kind: Literal["document", "folder"] = "document",
    available: bool = False,
) -> dict:
    """Add a document or a folder. Created hidden unless `available=True`.

    In Ultra a document must sit inside a folder, so pass parent_id for
    documents. `body_html` is BBML (see bb_post_announcement).
    """
    handler = {"id": "resource/x-bb-folder"} if kind == "folder" else {"id": "resource/x-bb-document"}
    payload: dict[str, Any] = {"title": title,
                               "availability": {"available": "Yes" if available else "No"},
                               "contentHandler": handler}
    if body_html:
        check_bbml(body_html)
        payload["body"] = body_html
    created = await bb._request("POST", _contents_path(course_id, parent_id), json=payload, write=True)
    return _item(created)


@mcp.tool()
async def bb_update_content(course_id: str, content_id: str, title: str = "", body_html: str = "") -> dict:
    """Change a content item's title and/or body. Only the given fields are sent."""
    payload: dict[str, Any] = {}
    if title:
        payload["title"] = title
    if body_html:
        check_bbml(body_html)
        payload["body"] = body_html
    if not payload:
        raise BlackboardError("nothing to change: give title and/or body_html")
    return await bb._request("PATCH", f"/v1/courses/{course_id}/contents/{content_id}",
                             json=payload, write=True)


@mcp.tool()
async def bb_set_availability(course_id: str, content_id: str, available: bool) -> dict:
    """Show or hide one content item — a folder, a document, a test link.

    Returns the item as Blackboard now holds it: read `available` off the
    response rather than trust the request.
    """
    r = await bb._request("PATCH", f"/v1/courses/{course_id}/contents/{content_id}",
                          json={"availability": {"available": "Yes" if available else "No"}},
                          write=True)
    return _item(r)


@mcp.tool()
async def bb_upload_file(course_id: str, parent_id: str, path: str, title: str = "") -> dict:
    """Upload a local file and add it to a folder as a file item (hidden).

    Two calls: POST /v1/uploads with the bytes, then a child content item with
    the resource/x-bb-file handler pointing at the upload id. Unverified on
    this site until scripts/probe_writes.py has run.
    """
    import pathlib

    p = pathlib.Path(path).expanduser()
    if not p.is_file():
        raise BlackboardError(f"no such file: {p}")
    up = await bb._request("POST", "/v1/uploads", content=p.read_bytes(),
                           content_type="application/octet-stream", write=True)
    created = await bb._request(
        "POST", _contents_path(course_id, parent_id),
        json={"title": title or p.name, "availability": {"available": "No"},
              "contentHandler": {"id": "resource/x-bb-file",
                                 "file": {"uploadId": up.get("id"), "fileName": p.name}}},
        write=True)
    return _item(created)


@mcp.tool()
async def bb_create_assessment(course_id: str, title: str, parent_id: str = "",
                               available: bool = False) -> dict:
    """Create a test shell (hidden by default).

    Since Learn 3900.98 a test is a content item with the
    resource/x-bb-asmt-test-link handler. The response carries assessment_id
    and grade_column_id. Questions go in through Ultra's Upload Questions:
    build the file with bb_export_test.
    """
    created = await bb._request(
        "POST", _contents_path(course_id, parent_id),
        json={"title": title, "availability": {"available": "Yes" if available else "No"},
              "contentHandler": {"id": TEST_HANDLER}},
        write=True)
    h = created.get("contentHandler") or {}
    return {"content_id": created.get("id"), "title": created.get("title"),
            "assessment_id": h.get("assessmentId"), "grade_column_id": h.get("gradeColumnId"),
            "available": (created.get("availability") or {}).get("available")}


@mcp.tool()
async def bb_add_question(
    course_id: str,
    assessment_id: str,
    question_type: Literal["MultipleChoice", "TrueFalse", "Essay", "MultipleAnswer"],
    text: str,
    answers: list[dict] | None = None,
    points: float = 1.0,
) -> dict:
    """Add one question to an assessment. `answers`: [{"text", "correct"}].

    Unverified on Ultra: the route answers 200 for reads but returns opaque
    blocks, and this payload is the Original-era shape. If it fails, use
    bb_export_test and Upload Questions.
    """
    payload: dict[str, Any] = {"title": text[:80], "questionType": question_type,
                               "displayText": text, "points": points}
    if question_type != "Essay":
        if not answers:
            raise BlackboardError(f"{question_type} needs an `answers` list.")
        payload["answers"] = [{"displayText": a["text"], "correct": bool(a.get("correct"))}
                              for a in answers]
    return await bb._request("POST", f"/v1/courses/{course_id}/assessments/{assessment_id}/questions",
                             json=payload, write=True)


@mcp.tool()
async def bb_create_gradebook_column(course_id: str, name: str, points_possible: float,
                                     description: str = "") -> dict:
    """Create a manual gradebook column."""
    return await bb._request("POST", f"/v2/courses/{course_id}/gradebook/columns",
                             json={"name": name, "description": description,
                                   "score": {"possible": points_possible},
                                   "grading": {"type": "Manual"}},
                             write=True)


@mcp.tool()
async def bb_post_announcement(course_id: str, title: str, body_html: str, draft: bool = False) -> dict:
    """Post a course announcement. Students are notified per their own settings.

    `body_html` is Blackboard Markup Language, not free HTML: p, ul/ol/li, a
    with href, strong, em, br, div, span, sub, sup, del, h4–h6. Checked here
    because the server's 400 does not say which tag it objected to.
    `draft=True` saves without publishing. There is no email flag in the API.
    """
    check_bbml(body_html)
    return await bb._request("POST", f"/v1/courses/{course_id}/announcements",
                             json={"title": title, "body": body_html, "draft": draft,
                                   "availability": {"duration": {"type": "Permanent"}}},
                             write=True)


@mcp.tool()
async def bb_export_test(questions: list[dict], path: str) -> dict:
    """Write a test as the tab-delimited file Ultra accepts under Upload Questions.

    Each question: `type` and `text`, plus
      MC / MA — `answers`: [{"text", "correct"}] (MC: exactly one correct)
      TF — `correct`: true|false;  ESS — optional `example`
      NUM — `answer`, optional `tolerance`;  FIB — `answers`: [str]
    Max 250 rows. Points are NOT carried: every question arrives at 0.
    No network, no write switch: the upload is a click in Ultra.
    """
    return export.write_test(questions, path)


# --------------------------------------------------------------------------
# write — grades (BB_ALLOW_GRADE_WRITES=1)
# --------------------------------------------------------------------------


@mcp.tool()
async def bb_set_grade(course_id: str, column_id: str, user_id: str, score: float,
                       feedback: str = "") -> dict:
    """Post a grade for one student on one column. The student sees it."""
    return await bb._request("PATCH",
                             f"/v2/courses/{course_id}/gradebook/columns/{column_id}/users/{user_id}",
                             json={"score": score, "feedback": feedback}, grade_write=True)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run all tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q`
Expected: `17 passed`

- [ ] **Step 5: Commit**

```bash
git add src/bb_mcp/server.py tests/test_tools.py
git commit -m "Run tools on the app token: students with names, outline tree, grades with names, content edit, file upload"
```

---

### Task 4: `.mcp.json`, probe script, smoke script, README

**Files:**
- Create: `.mcp.json`, `scripts/probe_writes.py`, `scripts/smoke.py`
- Modify: `README.md`

- [ ] **Step 1: `.mcp.json`** (committed, no secrets — they stay in `.env`)

```json
{
  "mcpServers": {
    "blackboard": {
      "command": ".venv/bin/python",
      "args": ["-m", "bb_mcp.server"],
      "env": {"PYTHONPATH": "src"}
    }
  }
}
```

- [ ] **Step 2: `scripts/smoke.py`** — read-only, run by hand

```python
"""Read-only smoke test against the live site. PYTHONPATH=src .venv/bin/python scripts/smoke.py [course_id]"""
import asyncio
import json
import sys

from bb_mcp import server

COURSE = sys.argv[1] if len(sys.argv) > 1 else "_170037_1"


async def main():
    print("whoami", json.dumps(await server.bb_whoami()))
    print("course", json.dumps(await server.bb_get_course(COURSE)))
    tree = await server.bb_content_tree(COURSE, max_depth=3)
    print("tree", json.dumps(tree, indent=1)[:1500])
    students = await server.bb_list_students(COURSE, role="Student")
    print("students", len(students), students[:2])
    print("assessments", await server.bb_list_assessments(COURSE))
    cols = await server.bb_list_gradebook_columns(COURSE)
    print("columns", cols[:3])
    if cols:
        print("grades", json.dumps(await server.bb_list_grades(COURSE, cols[0]["id"]))[:600])
    print("announcements", [(a["id"], a["title"]) for a in await server.bb_list_announcements(COURSE)])


asyncio.run(main())
```

- [ ] **Step 3: `scripts/probe_writes.py`** — decides bb_add_question and bb_upload_file. Makes a hidden folder, a hidden test, one question, one uploaded file, one document with an attachment; deletes the folder at the end and reports what answered what.

```python
"""Probe the two unverified writes on a hidden scratch folder, then delete it.

BB_ALLOW_WRITES=1 PYTHONPATH=src .venv/bin/python scripts/probe_writes.py [course_id]
"""
import asyncio
import json
import sys
import tempfile
import pathlib

from bb_mcp import client as bb, server

COURSE = sys.argv[1] if len(sys.argv) > 1 else "_170037_1"


async def try_(label, coro):
    try:
        r = await coro
        print(f"OK   {label}: {json.dumps(r)[:300]}")
        return r
    except bb.BlackboardError as e:
        print(f"FAIL {label}: {e}")
        return None


async def main():
    folder = await server.bb_create_content(COURSE, "zz-api-probe (delete me)", kind="folder")
    fid = folder["id"]
    try:
        test = await try_("create test", server.bb_create_assessment(COURSE, "zz-probe-test", parent_id=fid))
        if test:
            await try_("add question", server.bb_add_question(
                COURSE, test["assessment_id"], "MultipleChoice", "<p>2+2?</p>",
                [{"text": "4", "correct": True}, {"text": "5", "correct": False}]))
            await try_("list questions", server.bb_list_questions(COURSE, test["assessment_id"]))
        tmp = pathlib.Path(tempfile.mkdtemp()) / "zz-probe.txt"
        tmp.write_text("probe\n")
        await try_("upload file", server.bb_upload_file(COURSE, fid, str(tmp)))
        doc = await try_("create doc", server.bb_create_content(COURSE, "zz-probe-doc", "<p>probe</p>", parent_id=fid))
        if doc:
            up = await try_("raw upload", bb._request("POST", "/v1/uploads", content=b"attach\n",
                                                      content_type="application/octet-stream", write=True))
            if up:
                await try_("attachment on doc", bb._request(
                    "POST", f"/v1/courses/{COURSE}/contents/{doc['id']}/attachments",
                    json={"uploadId": up["id"], "fileName": "zz-attach.txt"}, write=True))
    finally:
        await try_("delete folder", bb._request(
            "DELETE", f"/v1/courses/{COURSE}/contents/{fid}", params={"deleteGradebookEntries": "true"}, write=True))
        left = [c["title"] for c in await server.bb_list_contents(COURSE) if c["title"].startswith("zz")]
        cols = [c["name"] for c in await server.bb_list_gradebook_columns(COURSE) if "zz" in (c["name"] or "")]
        print("leftover contents:", left, "leftover columns:", cols)


asyncio.run(main())
```

- [ ] **Step 4: README** — replace the auth section: the app is registered, `.env` needs `BB_BASE_URL`, `BB_APP_KEY`, `BB_APP_SECRET`, optional `BB_ALLOW_WRITES=1`, `BB_ALLOW_GRADE_WRITES=1`. Remove every mention of `BB_TOKEN` / DevTools. List the tools by group (account, content, assessments, gradebook, writes, export). Keep the "how a generated test reaches Ultra" section. State the two unverified tools and the probe script.

- [ ] **Step 5: Run the smoke script**

Run: `PYTHONPATH=src .venv/bin/python scripts/smoke.py`
Expected: whoami shows `dmi99400.salvini`, tree shows the "Prof. Salvini"/"Prof. Arbia" folders, students have names.

- [ ] **Step 6: Commit**

```bash
git add .mcp.json scripts/smoke.py scripts/probe_writes.py README.md
git commit -m "Launch config, live smoke script, write probe for the two unverified tools"
```

---

## Self-review

- Spec coverage: auth/retry (T1), students/attempt docstring/whoami (T3), content tree, get/update content, announcements, grades, report, get_course, upload_file (T3), split (T1–T3), tests (T1–T3), smoke + probe + `.mcp.json` (T4). `bb_set_grade` path corrected to v2 (the v1 path in the old code was never exercised; v2 is the documented one and matches the read side).
- Placeholders: none.
- Names: `check_bbml`, `bb._request`, `bb._paged`, `export.write_test`, `export.question_line`, `_students`, `_item`, `_contents_path` used consistently.
