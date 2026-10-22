"""Ultra's private API — the one the browser uses — for what the public API refuses.

The public REST API cannot write questions (README). The Ultra interface
does it through `/learn/api/v1/…` with the browser's session cookies and an
`X-Blackboard-XSRF` header, the token carried in the `BbRouter` cookie
(`…,xsrf:<token>,…`). Observed on Learn 4001.2.0, 22 October 2026, by
recording the interface with Chrome DevTools:

    POST /learn/api/v1/courses/{c}/assessments            {"title": …, "type": "Pool"}   → a question bank
    POST /learn/api/v1/courses/{c}/assessments/{a}/questions   one question, JSON per type (below)
    GET  /learn/api/v1/courses/{c}/assessments?types=Pool  the banks
    POST /learn/api/v1/files  +  POST …/courses/{c}/imports?fileLocation=…   import a package
    POST /learn/api/v1/courses/{c}/exports                 export a bank

Undocumented: it can change with any Learn release, and it needs a signed-in
browser. The session comes from a Chrome started with
`--remote-debugging-port` in which the lecturer has signed in; cookies are
read once through CDP and kept in memory for this process only. Everything
else in this server keeps using the public API with client credentials.
"""
from __future__ import annotations

import json
import urllib.request
import uuid
from typing import Any

import httpx

from .client import BASE_URL, BlackboardError

_session: dict[str, Any] = {}        # cookies, xsrf, user — this process only


# ----------------------------------------------------------------------------- session
def _cdp_cookies(cdp_endpoint: str, host: str) -> list[dict[str, Any]]:
    try:
        import websocket  # websocket-client
    except ImportError as e:
        raise BlackboardError("pip install websocket-client (needed to read the browser session)") from e
    targets = json.load(urllib.request.urlopen(f"{cdp_endpoint}/json/list", timeout=10))
    pages = [t for t in targets if t.get("type") == "page" and host in t.get("url", "")]
    if not pages:
        pages = [t for t in targets if t.get("type") == "page"]
    if not pages:
        raise BlackboardError(f"no page open in the browser at {cdp_endpoint}; open Blackboard there and sign in")
    ws = websocket.create_connection(pages[0]["webSocketDebuggerUrl"], suppress_origin=True, timeout=10)
    try:
        ws.send(json.dumps({"id": 1, "method": "Network.getCookies", "params": {"urls": [f"https://{host}/"]}}))
        msg = json.loads(ws.recv())
    finally:
        ws.close()
    return (msg.get("result") or {}).get("cookies") or []


def connect(cdp_endpoint: str = "http://127.0.0.1:9223") -> dict[str, Any]:
    """Read the signed-in Blackboard session from the browser; keep it for this process."""
    host = BASE_URL.split("//", 1)[1].rstrip("/")
    cookies = {c["name"]: c["value"] for c in _cdp_cookies(cdp_endpoint, host) if host.endswith(c["domain"].lstrip("."))}
    router = cookies.get("BbRouter", "")
    fields = dict(p.split(":", 1) for p in router.split(",") if ":" in p)
    xsrf = fields.get("xsrf")
    if not xsrf:
        raise BlackboardError("no signed-in Blackboard session in that browser (BbRouter cookie without xsrf): sign in first")
    _session.update(cookies=cookies, xsrf=xsrf, user=fields.get("user"), expires=fields.get("expires"))
    return {"host": host, "user": fields.get("user"), "expires": fields.get("expires"), "cookies": sorted(cookies)}


def _client() -> httpx.AsyncClient:
    if not _session:
        raise BlackboardError("no Ultra session: call bb_ultra_connect first (a signed-in Chrome with --remote-debugging-port)")
    return httpx.AsyncClient(base_url=f"{BASE_URL}/learn/api/v1", cookies=_session["cookies"], timeout=60,
                             headers={"X-Blackboard-XSRF": _session["xsrf"], "Accept": "application/json",
                                      "Referer": f"{BASE_URL}/ultra/"})


async def _call(method: str, path: str, **kw) -> Any:
    async with _client() as c:
        r = await c.request(method, path, **kw)
    if r.status_code == 401 or r.status_code == 403:
        raise BlackboardError(f"{r.status_code} on {method} {path}: the browser session is gone or lacks the right; sign in again and bb_ultra_connect")
    if r.status_code >= 400:
        raise BlackboardError(f"{r.status_code} on {method} {path}: {r.text[:400]}")
    return r.json() if r.content else {}


# ----------------------------------------------------------------------------- question JSON
def _text(s: str) -> dict[str, Any]:
    return {"rawText": s, "displayText": f"<div>\n  {s} \n</div>", "webLocation": None, "fileLocation": None}


def question_json(course_id: str, assessment_id: str, q: dict[str, Any], points: float) -> dict[str, Any]:
    """The body Ultra posts for one question, from a pool entry (bb_mcp.exams format)."""
    kind = str(q.get("type", "")).upper()
    # Tags are objects in Ultra (a plain string answers 400 "no String-argument constructor");
    # their shape was never recorded, so none are sent: the bank itself is the topic.
    tags = {"categoryTags": [], "keywordTags": [], "levelOfDifficultyTags": [], "topicTags": []}
    base = {"courseId": course_id, "assessmentId": assessment_id,
            "correctResponseFeedback": {"rawText": ""}, "incorrectResponseFeedback": {"rawText": ""},
            "points": float(q.get("points", points)), "questionText": _text(str(q["text"])), "aiState": "No", **tags}
    if kind == "ESS":
        return {**base, "questionType": "essay", "isAutoGraded": False, "isShortAnswer": False,
                "answerText": _text(str(q.get("example", "")))}
    if kind == "TF":
        return {**base, "questionType": "eitherOr", "answer": bool(q["correct"]), "answerType": "true_false",
                "isAutoGraded": True}
    if kind in ("MC", "MA"):
        # As the interface posts them (recorded 22 Oct 2026): lowercase type, answers with a
        # client-side id, `correctAnswer`, and the counts Ultra shows in the editor.
        answers = q.get("answers") or []
        n_correct = sum(1 for a in answers if a.get("correct"))
        # Ultra has one type for both: "multipleanswer" with singleCorrectAnswer true for a multiple
        # choice. "multipleChoice" exists but answers "Read only question type" (Original's).
        return {**base, "questionType": "multipleanswer",
                "positionAfter": "start",
                "answers": [{"id": f"new_{uuid.uuid4()}", "answerText": {"rawText": f"<p>{a.get('text', '')}</p>"},
                             "correctAnswer": bool(a.get("correct"))} for a in answers],
                "answersCount": len(answers), "singleCorrectAnswer": kind == "MC",
                "answerSelectionLimit": 1 if kind == "MC" else n_correct, "isAutoGraded": True}
    raise BlackboardError(f"question {q.get('id')}: type {kind!r} not supported here (MC, MA, TF, ESS)")


# ----------------------------------------------------------------------------- operations
async def list_banks(course_id: str) -> list[dict[str, Any]]:
    r = await _call("GET", f"/courses/{course_id}/assessments", params={"limit": 100, "offset": 0, "types": "Pool"})
    return [{"id": b["id"], "title": b.get("title"), "questions": b.get("questionCount"), "modified": b.get("lastModifiedDate")}
            for b in r.get("results", [])]


async def create_bank(course_id: str, title: str) -> dict[str, Any]:
    r = await _call("POST", f"/courses/{course_id}/assessments", json={"title": title, "type": "Pool"})
    return {"id": r["id"], "title": r.get("title")}


async def add_question(course_id: str, bank_id: str, q: dict[str, Any], points: float) -> dict[str, Any]:
    r = await _call("POST", f"/courses/{course_id}/assessments/{bank_id}/questions", json=question_json(course_id, bank_id, q, points))
    return {"id": r.get("id"), "type": r.get("questionType"), "points": r.get("points")}


async def delete_bank(course_id: str, bank_id: str) -> dict[str, Any]:
    await _call("DELETE", f"/courses/{course_id}/assessments/{bank_id}")
    return {"deleted": bank_id}
