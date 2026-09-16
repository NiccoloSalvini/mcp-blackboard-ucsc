"""MCP server for Blackboard Learn Ultra.

Talks to the documented public REST API using an OAuth2 client-credentials
token. Every route used here was probed against the target instance: paths
that do not exist answer 404 unauthenticated, the ones below answer 401,
which is how we know they are present on this build.

Writes are off unless explicitly enabled, and grade writes need their own
switch, because posting a wrong number to an official gradebook is the one
mistake here that reaches students directly.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Literal

import httpx
from mcp.server.mcpserver import MCPServer


def _load_dotenv() -> None:
    """Read .env next to the project root, without adding a dependency.

    Credentials live here and not in .mcp.json: that file is committed to a
    public repository, this one is git-ignored. Existing environment
    variables win, so a shell override still works.
    """
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
# A token lifted from a logged-in Ultra session, used as-is. Blackboard issues it
# to its own first-party UI, so it carries the signed-in user's permissions and
# lives for minutes — the client refreshes it as it works. It exists so the server is usable
# before an administrator registers our Application ID; it is not a substitute
# for that. When set, it wins over the client-credentials exchange below.
SESSION_TOKEN = os.environ.get("BB_TOKEN", "")
ALLOW_WRITES = os.environ.get("BB_ALLOW_WRITES", "0") == "1"
ALLOW_GRADE_WRITES = os.environ.get("BB_ALLOW_GRADE_WRITES", "0") == "1"

API = f"{BASE_URL}/learn/api/public"

mcp = MCPServer("blackboard")


class BlackboardError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------

_token: str | None = None
_token_expires_at: float = 0.0


async def _access_token(client: httpx.AsyncClient) -> str:
    """Fetch and cache an OAuth2 token, refreshing a minute before expiry."""
    global _token, _token_expires_at

    if SESSION_TOKEN:
        return SESSION_TOKEN

    if _token and time.time() < _token_expires_at:
        return _token

    if not APP_KEY or not APP_SECRET:
        raise BlackboardError(
            "Set BB_TOKEN to a token from a logged-in Ultra session, or set "
            "BB_APP_KEY / BB_APP_SECRET. For the latter, register an application "
            "at developer.anthology.com, then ask the Blackboard administrator to "
            "add that Application ID under Admin > REST API Integrations."
        )

    resp = await client.post(
        f"{API}/v1/oauth2/token",
        auth=(APP_KEY, APP_SECRET),
        data={"grant_type": "client_credentials"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if resp.status_code != 200:
        raise BlackboardError(
            f"Token request failed ({resp.status_code}): {resp.text[:400]}"
        )

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
    write: bool = False,
    grade_write: bool = False,
) -> Any:
    if grade_write and not ALLOW_GRADE_WRITES:
        raise BlackboardError(
            "Grade writes are disabled. Set BB_ALLOW_GRADE_WRITES=1 to allow them."
        )
    if write and not ALLOW_WRITES:
        raise BlackboardError(
            "Writes are disabled. Set BB_ALLOW_WRITES=1 to allow them."
        )

    async with httpx.AsyncClient(timeout=30) as client:
        token = await _access_token(client)
        resp = await client.request(
            method,
            f"{API}{path}",
            headers={"Authorization": f"Bearer {token}"},
            json=json,
            params=params,
        )

    if resp.status_code == 404:
        raise BlackboardError(f"Not found: {method} {path}")
    if resp.status_code == 401 and SESSION_TOKEN:
        # The usual cause, and it says nothing useful on its own.
        raise BlackboardError(
            "401: the BB_TOKEN session token has expired — they last minutes, "
            "not hours. Grab a fresh one from a logged-in Blackboard tab: DevTools > "
            "Network, filter 'tokeninfo', reload a course page, copy access_token "
            "off the request URL."
        )
    if resp.status_code >= 400:
        raise BlackboardError(f"{resp.status_code} on {method} {path}: {resp.text[:500]}")
    if not resp.content:
        return {"ok": True}
    return resp.json()


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
        # nextPage comes back as a full API path; strip the prefix and drop
        # the params we already encoded into it.
        next_path = next_url.replace("/learn/api/public", "") if next_url else None
        query = {}

    return out[:limit]


# Blackboard Markup Language: the subset of HTML a content or announcement body
# may contain. Anything else is rejected server-side with a 400 that quotes the
# whole body and says nothing about which tag broke it. Check here first and
# name the tag. Notably <b> and <i> are not in the set; <strong> and <em> are.
_BBML_TAGS = {"a", "br", "del", "div", "em", "h4", "h5", "h6", "li", "ol", "p",
              "span", "strong", "sub", "sup", "ul"}
_BBML_HINT = {"b": "strong", "i": "em", "h1": "h4", "h2": "h4", "h3": "h4"}


def _check_bbml(html: str) -> None:
    used = set(re.findall(r"</?([a-zA-Z][a-zA-Z0-9]*)", html))
    bad = sorted(t for t in used if t.lower() not in _BBML_TAGS)
    if bad:
        hints = ", ".join(f"<{t}> (use <{_BBML_HINT[t]}>)" if t in _BBML_HINT else f"<{t}>" for t in bad)
        raise BlackboardError(
            f"Body uses tags outside Blackboard Markup Language: {hints}. "
            f"Allowed: {', '.join(sorted(_BBML_TAGS))}."
        )


# --------------------------------------------------------------------------
# read
# --------------------------------------------------------------------------


@mcp.tool()
async def bb_whoami() -> dict:
    """Who the token acts as. Use this first to confirm auth works."""
    return await _request("GET", "/v1/users/me")


@mcp.tool()
async def bb_list_courses(search: str = "") -> list[dict]:
    """List courses visible to the account. `search` filters on course name."""
    params = {"name": search} if search else None
    rows = await _paged("/v3/courses", params)
    return [
        {"id": c.get("id"), "courseId": c.get("courseId"), "name": c.get("name")}
        for c in rows
    ]


@mcp.tool()
async def bb_list_students(course_id: str) -> list[dict]:
    """Enrolled users for a course, with their role."""
    rows = await _paged(f"/v1/courses/{course_id}/users")
    return [
        {
            "userId": r.get("userId"),
            "role": r.get("courseRoleId"),
            "name": (r.get("user") or {}).get("name"),
        }
        for r in rows
    ]


@mcp.tool()
async def bb_list_contents(course_id: str, folder_id: str = "") -> list[dict]:
    """Content items in a course, or inside one folder."""
    path = (
        f"/v1/courses/{course_id}/contents/{folder_id}/children"
        if folder_id
        else f"/v1/courses/{course_id}/contents"
    )
    rows = await _paged(path)
    return [
        {"id": r.get("id"), "title": r.get("title"), "type": r.get("contentHandler", {}).get("id")}
        for r in rows
    ]


@mcp.tool()
async def bb_list_assessments(course_id: str) -> list[dict]:
    """Tests in the course, with the ids needed to read their questions and grades.

    There is no /assessments collection in the public API — a test is a content
    item whose handler is resource/x-bb-asmt-test-link. So this lists course
    contents and keeps those, surfacing assessmentId (for the questions routes)
    and gradeColumnId (for the gradebook routes).
    """
    rows = await _paged(f"/v1/courses/{course_id}/contents")
    out = []
    for c in rows:
        h = c.get("contentHandler", {})
        if h.get("id") != "resource/x-bb-asmt-test-link":
            continue
        out.append({
            "content_id": c.get("id"),
            "title": c.get("title"),
            "assessment_id": h.get("assessmentId"),
            "grade_column_id": h.get("gradeColumnId"),
            "available": c.get("availability", {}).get("available"),
            "created": c.get("created"),
        })
    return out


@mcp.tool()
async def bb_list_questions(course_id: str, assessment_id: str) -> list[dict]:
    """Questions inside one assessment."""
    return await _paged(f"/v1/courses/{course_id}/assessments/{assessment_id}/questions")


@mcp.tool()
async def bb_list_gradebook_columns(course_id: str) -> list[dict]:
    """Gradebook columns for a course."""
    rows = await _paged(f"/v2/courses/{course_id}/gradebook/columns")
    return [
        {
            "id": r.get("id"),
            "name": r.get("name"),
            "score": (r.get("score") or {}).get("possible"),
            "graded": r.get("grading", {}).get("type"),
        }
        for r in rows
    ]


@mcp.tool()
async def bb_list_attempts(course_id: str, column_id: str) -> list[dict]:
    """Submissions on a gradebook column — what is waiting to be marked."""
    rows = await _paged(f"/v2/courses/{course_id}/gradebook/columns/{column_id}/attempts")
    return [
        {
            "attemptId": r.get("id"),
            "userId": r.get("userId"),
            "status": r.get("status"),
            "score": r.get("score"),
            "submitted": r.get("created"),
        }
        for r in rows
    ]


@mcp.tool()
async def bb_get_attempt(course_id: str, column_id: str, attempt_id: str) -> dict:
    """One submission in full, including the student's answers."""
    return await _request(
        "GET", f"/v2/courses/{course_id}/gradebook/columns/{column_id}/attempts/{attempt_id}"
    )


# --------------------------------------------------------------------------
# write — content and assessments
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
    """Add a content item — a document or a folder — to the course page.

    Created hidden by default (`available=False`) so nothing appears to
    students before you have looked at it. In Ultra a document must sit inside
    a folder that is a page (isBbPage), so pass parent_id for documents.
    """
    path = (
        f"/v1/courses/{course_id}/contents/{parent_id}/children"
        if parent_id
        else f"/v1/courses/{course_id}/contents"
    )
    handler = {"id": "resource/x-bb-folder"} if kind == "folder" else {"id": "resource/x-bb-document"}
    payload: dict[str, Any] = {
        "title": title,
        "availability": {"available": "Yes" if available else "No"},
        "contentHandler": handler,
    }
    if body_html:
        _check_bbml(body_html)
        payload["body"] = body_html
    return await _request("POST", path, json=payload, write=True)


@mcp.tool()
async def bb_create_assessment(
    course_id: str,
    title: str,
    parent_id: str = "",
    available: bool = False,
) -> dict:
    """Create a test shell. Add questions to it afterwards with bb_add_question.

    Since Learn 3900.98 a test is created as a content item with the
    resource/x-bb-asmt-test-link handler, not through an /assessments route
    (there is none). The response carries assessmentId — pass that to
    bb_add_question — and gradeColumnId for the gradebook.
    """
    path = (
        f"/v1/courses/{course_id}/contents/{parent_id}/children"
        if parent_id
        else f"/v1/courses/{course_id}/contents"
    )
    created = await _request(
        "POST",
        path,
        json={
            "title": title,
            "availability": {"available": "Yes" if available else "No"},
            "contentHandler": {"id": "resource/x-bb-asmt-test-link"},
        },
        write=True,
    )
    h = created.get("contentHandler", {})
    return {
        "content_id": created.get("id"),
        "title": created.get("title"),
        "assessment_id": h.get("assessmentId"),
        "grade_column_id": h.get("gradeColumnId"),
        "available": created.get("availability", {}).get("available"),
    }


@mcp.tool()
async def bb_add_question(
    course_id: str,
    assessment_id: str,
    question_type: Literal["MultipleChoice", "TrueFalse", "Essay", "MultipleAnswer"],
    text: str,
    answers: list[dict] | None = None,
    points: float = 1.0,
) -> dict:
    """Add one question to an assessment.

    `answers` is a list of {"text": str, "correct": bool} — required for
    MultipleChoice, TrueFalse and MultipleAnswer, ignored for Essay.

    Unverified on Ultra (Learn 4000.x): the questions route answers 200 there
    but returns question blocks as opaque handles, and this payload is the
    Original-era shape. Try it on a scratch course before trusting it.
    """
    payload: dict[str, Any] = {
        "title": text[:80],
        "questionType": question_type,
        "displayText": text,
        "points": points,
    }
    if question_type != "Essay":
        if not answers:
            raise BlackboardError(f"{question_type} needs an `answers` list.")
        payload["answers"] = [
            {"displayText": a["text"], "correct": bool(a.get("correct"))} for a in answers
        ]

    return await _request(
        "POST",
        f"/v1/courses/{course_id}/assessments/{assessment_id}/questions",
        json=payload,
        write=True,
    )


@mcp.tool()
async def bb_create_gradebook_column(
    course_id: str,
    name: str,
    points_possible: float,
    description: str = "",
) -> dict:
    """Create a manual gradebook column."""
    return await _request(
        "POST",
        f"/v2/courses/{course_id}/gradebook/columns",
        json={
            "name": name,
            "description": description,
            "score": {"possible": points_possible},
            "grading": {"type": "Manual"},
        },
        write=True,
    )


@mcp.tool()
async def bb_set_availability(course_id: str, content_id: str, available: bool) -> dict:
    """Show or hide one content item — a folder, a document, a test link.

    This is how something created hidden gets published to students, and how it
    is pulled back. Returns the item as Blackboard now holds it, so the caller
    can read `availability.available` off the response rather than trust the
    request.
    """
    return await _request(
        "PATCH",
        f"/v1/courses/{course_id}/contents/{content_id}",
        json={"availability": {"available": "Yes" if available else "No"}},
        write=True,
    )


@mcp.tool()
async def bb_post_announcement(
    course_id: str,
    title: str,
    body_html: str,
    draft: bool = False,
) -> dict:
    """Post a course announcement. Students are notified per their own settings.

    `body_html` is Blackboard Markup Language, not free HTML: p, ul/ol/li, a
    with href, strong, em, br, div, span, sub, sup, del, h4–h6. It is checked
    here before sending, because the server's 400 does not say which tag it
    objected to. `draft=True` saves without publishing.

    The course announcement object has no email flag; the "send a copy by
    email" tick exists only in the Ultra UI at creation time.
    """
    _check_bbml(body_html)
    return await _request(
        "POST",
        f"/v1/courses/{course_id}/announcements",
        json={
            "title": title,
            "body": body_html,
            "draft": draft,
            "availability": {"duration": {"type": "Permanent"}},
        },
        write=True,
    )


# --------------------------------------------------------------------------
# write — grades (separate switch)
# --------------------------------------------------------------------------


@mcp.tool()
async def bb_set_grade(
    course_id: str,
    column_id: str,
    user_id: str,
    score: float,
    feedback: str = "",
) -> dict:
    """Post a grade for one student on one column.

    Needs BB_ALLOW_GRADE_WRITES=1. This writes to the official gradebook and
    the student sees it — check the column and the user id before calling.
    """
    return await _request(
        "PATCH",
        f"/v1/courses/{course_id}/gradebook/columns/{column_id}/users/{user_id}",
        json={"score": score, "feedback": feedback},
        grade_write=True,
    )


# --------------------------------------------------------------------------


@mcp.tool()
async def bb_config() -> dict:
    """What this server is pointed at and what it is allowed to do."""
    return {
        "base_url": BASE_URL,
        "auth_mode": "session-token" if SESSION_TOKEN else "client-credentials",
        "session_token_present": bool(SESSION_TOKEN),
        "credentials_present": bool(APP_KEY and APP_SECRET),
        "writes_enabled": ALLOW_WRITES,
        "grade_writes_enabled": ALLOW_GRADE_WRITES,
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
