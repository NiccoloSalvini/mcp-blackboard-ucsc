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
