"""Second set of tools: engagement, attendance, rubrics, adaptive release,
per-student exceptions, forum, calendar, audit, course copy, groups.

Registered on the same MCPServer as server.py; imported from there. Same
switches: reads always, writes with BB_ALLOW_WRITES, marks with
BB_ALLOW_GRADE_WRITES on top.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any

from . import client as bb
from .client import BlackboardError, check_bbml
from .server import _students, mcp


async def _by_id(course_id: str) -> dict[str, dict]:
    """Students keyed by userId AND by student number, so callers may pass either."""
    rows = await _students(course_id)
    out: dict[str, dict] = {}
    for r in rows:
        out[r["userId"]] = r
        if r.get("studentId"):
            out[r["studentId"]] = r
    return out


def _resolve(index: dict[str, dict], ids: list[str]) -> tuple[list[str], list[str]]:
    found, unknown = [], []
    for i in ids:
        r = index.get(i)
        (found if r else unknown).append(r["userId"] if r else i)
    return found, unknown


# --------------------------------------------------------------------------
# engagement
# --------------------------------------------------------------------------


@mcp.tool()
async def bb_review_status(course_id: str) -> list[dict]:
    """How much of the course each student has opened: reviewed / reviewable items.

    A student at 0 after three weeks is the early signal worth a message.
    """
    students = {s["userId"]: s for s in await _students(course_id)}
    rows = await bb._paged(f"/v1/courses/{course_id}/performance/contentReviewStatus")
    out = []
    for r in rows:
        s = students.get(r.get("userId"), {})
        if s and s.get("role") != "Student":
            continue
        out.append({"userId": r.get("userId"), "name": s.get("name"), "studentId": s.get("studentId"),
                    "reviewed": r.get("reviewedCount"), "reviewable": r.get("reviewableCount")})
    return out


@mcp.tool()
async def bb_content_viewed(course_id: str, content_id: str) -> list[dict]:
    """Per student, whether one content item was opened (state Completed / NotStarted).

    One request per student: fine for a course, slow for a faculty. Blackboard
    answers 404 for a student who never touched the item: reported as state None.
    """
    out = []
    for s in await _students(course_id):
        if s["role"] != "Student":
            continue
        try:
            st = await bb._request("GET", f"/v1/courses/{course_id}/contents/{content_id}/states/{s['userId']}")
            state = st.get("state")
        except BlackboardError as e:
            if "Not found" not in str(e):
                raise
            state = None
        out.append({"userId": s["userId"], "name": s["name"], "studentId": s["studentId"], "state": state})
    return out


# --------------------------------------------------------------------------
# attendance
# --------------------------------------------------------------------------


@mcp.tool()
async def bb_list_meetings(course_id: str) -> list[dict]:
    """Lectures registered in the Attendance tool, with dates."""
    rows = await bb._paged(f"/v1/courses/{course_id}/meetings")
    return [{"id": r.get("id"), "title": r.get("title"), "start": r.get("start"), "end": r.get("end")}
            for r in rows]


@mcp.tool()
async def bb_create_meeting(course_id: str, title: str, start: str, end: str, description: str = "") -> dict:
    """Register one lecture for attendance. Creates the Attendance gradebook column if missing.

    Dates ISO 8601 UTC, e.g. "2026-10-05T08:00:00.000Z".
    """
    payload: dict[str, Any] = {"title": title, "start": start, "end": end}
    if description:
        payload["description"] = description
    r = await bb._request("POST", f"/v1/courses/{course_id}/meetings", json=payload, write=True)
    return {"id": r.get("id"), "title": r.get("title")}


@mcp.tool()
async def bb_mark_attendance(course_id: str, meeting_id: str, present: list[str],
                             late: list[str] | None = None, excused: list[str] | None = None,
                             mark_absent: bool = True) -> dict:
    """Mark attendance for one lecture from lists of userIds or student numbers.

    Everyone enrolled and not listed is marked Absent unless mark_absent=False.
    Returns ids it could not match, so a typo never silently becomes an absence.
    """
    index = await _by_id(course_id)
    status: dict[str, str] = {}
    unknown: list[str] = []
    for ids, label in ((present, "Present"), (late or [], "Late"), (excused or [], "Excused")):
        found, missing = _resolve(index, ids)
        unknown += missing
        for uid in found:
            status[uid] = label
    if mark_absent:
        for s in await _students(course_id):
            if s["role"] == "Student":
                status.setdefault(s["userId"], "Absent")
    records = [{"meetingId": meeting_id, "userId": uid, "status": st} for uid, st in status.items()]
    if records:
        await bb._request("POST", f"/v1/courses/{course_id}/meetings/{meeting_id}/users/bulk",
                          json=records, write=True)
    counts = {k: sum(1 for v in status.values() if v == k) for k in ("Present", "Late", "Absent")}
    return {"meetingId": meeting_id, "present": counts["Present"], "late": counts["Late"],
            "absent": counts["Absent"], "unknown": unknown}


@mcp.tool()
async def bb_attendance_report(course_id: str) -> dict:
    """Attendance grid: one row per student, one key per lecture, plus a presence count."""
    meetings = await bb_list_meetings(course_id)
    by_meeting: dict[str, dict[str, str]] = {}
    for m in meetings:
        rows = await bb._paged(f"/v1/courses/{course_id}/meetings/{m['id']}/users")
        by_meeting[m["id"]] = {r.get("userId"): r.get("status") for r in rows}
    out = []
    for s in await _students(course_id):
        if s["role"] != "Student":
            continue
        st = {m["id"]: by_meeting[m["id"]].get(s["userId"]) for m in meetings}
        out.append({"userId": s["userId"], "name": s["name"], "studentId": s["studentId"], "status": st,
                    "present": sum(1 for v in st.values() if v in ("Present", "Late"))})
    return {"meetings": meetings, "rows": out}


# --------------------------------------------------------------------------
# rubrics
# --------------------------------------------------------------------------


@mcp.tool()
async def bb_list_rubrics(course_id: str) -> list[dict]:
    """Rubrics defined in the course."""
    rows = await bb._paged(f"/v1/courses/{course_id}/rubrics")
    return [{"id": r.get("id"), "title": r.get("title"), "type": r.get("rubricType")} for r in rows]


@mcp.tool()
async def bb_get_rubric(course_id: str, rubric_id: str) -> dict:
    """One rubric in full: rows (criteria), columns (levels), cells with ids and points.

    The cell and row ids are what bb_evaluate_rubric needs.
    """
    return await bb._request("GET", f"/v1/courses/{course_id}/rubrics/{rubric_id}")


@mcp.tool()
async def bb_create_rubric(course_id: str, title: str, criteria: list[dict], description: str = "") -> dict:
    """Create a numeric rubric from criteria, each with the same ordered levels.

    criteria: [{"criterion": "Code runs", "levels": [{"header": "Full", "points": 5,
    "description": "…"}, {"header": "None", "points": 0}]}, …]. Level headers of
    the first criterion become the columns; every criterion must have as many levels.
    """
    if not criteria:
        raise BlackboardError("no criteria")
    headers = [lv.get("header", f"Level {i + 1}") for i, lv in enumerate(criteria[0].get("levels") or [])]
    if not headers:
        raise BlackboardError("first criterion has no levels")
    for c in criteria:
        if len(c.get("levels") or []) != len(headers):
            raise BlackboardError(f"criterion {c.get('criterion')!r}: every criterion needs the same number "
                                  f"of levels ({len(headers)})")
    rows, cells = [], []
    for ri, c in enumerate(criteria):
        pts = [float(lv.get("points", 0)) for lv in c["levels"]]
        rows.append({"header": c.get("criterion", f"Criterion {ri + 1}"), "position": ri, "rowPoints": max(pts)})
        for ci, lv in enumerate(c["levels"]):
            cell: dict[str, Any] = {"rowPosition": ri, "columnPosition": ci, "points": {"value": pts[ci]}}
            if lv.get("description"):
                cell["description"] = lv["description"]
            cells.append(cell)
    payload = {"title": title, "description": description, "rubricType": "Numeric",
               "columns": [{"header": h, "position": i} for i, h in enumerate(headers)],
               "rows": rows, "cells": cells}
    r = await bb._request("POST", f"/v1/courses/{course_id}/rubrics", json=payload, write=True)
    return {"id": r.get("id"), "title": r.get("title"), "rows": len(rows), "columns": len(headers)}


@mcp.tool()
async def bb_attach_rubric(course_id: str, rubric_id: str, column_id: str, used_for_grading: bool = True,
                           visibility: str = "VisibleAfterGrading") -> dict:
    """Associate a rubric with a gradebook column (an assignment or test).

    visibility: Disabled | VisibleWithScores | VisibleWithoutScores | VisibleAfterGrading.
    Returns the association id, needed by bb_evaluate_rubric.
    """
    r = await bb._request("POST", f"/v1/courses/{course_id}/rubrics/{rubric_id}/associations",
                          json={"associationEntity": {"gradebookColumnId": column_id},
                                "usedForGrading": used_for_grading, "rubricVisibility": visibility},
                          write=True)
    return {"id": r.get("id"), "rubricId": rubric_id, "columnId": column_id}


@mcp.tool()
async def bb_evaluate_rubric(course_id: str, rubric_id: str, association_id: str, attempt_id: str,
                             selections: list[dict]) -> dict:
    """Fill a rubric for one submission: one selected cell per row, optional score and feedback.

    selections: [{"row_id", "cell_id", "score"?: number, "feedback"?: BBML}]. Every
    row of the rubric must be present. The gradebook column follows the rubric
    when the association is used for grading.
    """
    cells = []
    for s in selections:
        cell: dict[str, Any] = {"rubricRowId": s["row_id"], "rubricCellId": s["cell_id"]}
        if s.get("score") is not None:
            f = Fraction(str(s["score"])).limit_denominator(1000)
            cell["selectedScore"] = {"numerator": f.numerator, "denominator": f.denominator}
        if s.get("feedback"):
            check_bbml(s["feedback"])
            cell["feedback"] = s["feedback"]
        cells.append(cell)
    return await bb._request(
        "POST", f"/v1/courses/{course_id}/rubrics/{rubric_id}/associations/{association_id}/evaluations",
        json={"evaluationEntity": {"attemptId": attempt_id}, "cells": cells}, grade_write=True)


@mcp.tool()
async def bb_rubric_evaluations(course_id: str, column_id: str, attempt_id: str) -> list[dict]:
    """Rubric evaluations already recorded on one submission."""
    return await bb._paged(
        f"/v1/courses/{course_id}/gradebook/columns/{column_id}/attempts/{attempt_id}/rubric/evaluations")


# --------------------------------------------------------------------------
# adaptive release
# --------------------------------------------------------------------------


def _ar(course_id: str, content_id: str) -> str:
    return f"/v1/courses/{course_id}/contents/{content_id}/adaptiveRelease/rules"


async def _rule_with_criterion(course_id: str, content_id: str, title: str, criterion: dict) -> dict:
    rule = await bb._request("POST", _ar(course_id, content_id), json={"title": title[:100]}, write=True)
    crit = await bb._request("POST", f"{_ar(course_id, content_id)}/{rule['id']}/criteria",
                             json=criterion, write=True)
    return {"rule_id": rule.get("id"), "criterion_id": crit.get("id")}


@mcp.tool()
async def bb_list_release_rules(course_id: str, content_id: str) -> list[dict]:
    """Adaptive release rules on one item, with their criteria (date, grade, members…)."""
    out = []
    for r in await bb._paged(_ar(course_id, content_id)):
        crit = await bb._paged(f"{_ar(course_id, content_id)}/{r['id']}/criteria")
        out.append({"id": r.get("id"), "title": r.get("title"),
                    "criteria": [{"id": c.get("id"), "type": c.get("type")} for c in crit]})
    return out


@mcp.tool()
async def bb_release_by_grade(course_id: str, content_id: str, column_id: str, min_score: float,
                              max_score: float, title: str = "") -> dict:
    """Show an item only to students whose score on `column_id` lies in [min, max].

    E.g. recovery material for those under 18 on the intermediate. Criterion
    shape is not documented for Ultra: verify once with scripts/probe_writes.py.
    """
    return await _rule_with_criterion(
        course_id, content_id, title or f"Grade on {column_id} between {min_score} and {max_score}",
        {"type": "GradeRange", "gradebookColumnId": column_id, "minScore": min_score, "maxScore": max_score})


@mcp.tool()
async def bb_release_by_date(course_id: str, content_id: str, start: str = "", end: str = "",
                             title: str = "") -> dict:
    """Show an item only inside a date window (either bound optional). ISO 8601 UTC."""
    crit: dict[str, Any] = {"type": "DateRange"}
    if start:
        crit["startDate"] = start
    if end:
        crit["endDate"] = end
    if len(crit) == 1:
        raise BlackboardError("give start and/or end")
    return await _rule_with_criterion(course_id, content_id, title or f"Dates {start or '…'} – {end or '…'}", crit)


@mcp.tool()
async def bb_release_to_users(course_id: str, content_id: str, user_ids: list[str], title: str = "") -> dict:
    """Show an item only to the listed students (userIds)."""
    out = await _rule_with_criterion(course_id, content_id, title or f"{len(user_ids)} selected students",
                                     {"type": "Memberships"})
    await bb._request("PUT", f"{_ar(course_id, content_id)}/{out['rule_id']}/criteria/{out['criterion_id']}/users",
                      json=[{"userId": u} for u in user_ids], write=True)
    return {**out, "users": len(user_ids)}


@mcp.tool()
async def bb_delete_release_rule(course_id: str, content_id: str, rule_id: str) -> dict:
    """Remove one adaptive release rule; the item goes back to its plain availability."""
    await bb._request("DELETE", f"{_ar(course_id, content_id)}/{rule_id}", write=True)
    return {"ok": True, "deleted": rule_id}


# --------------------------------------------------------------------------
# per-student exceptions
# --------------------------------------------------------------------------


@mcp.tool()
async def bb_get_exception(course_id: str, column_id: str, user_id: str) -> dict:
    """The due-date / attempts exception one student has on one column, if any."""
    try:
        return await bb._request("GET", f"/v1/courses/{course_id}/gradebook/columns/{column_id}/exceptions/users/{user_id}")
    except BlackboardError as e:
        if "Not found" in str(e):
            return {"userId": user_id, "columnId": column_id, "exception": None}
        raise


@mcp.tool()
async def bb_set_exception(course_id: str, column_id: str, user_id: str, due: str = "",
                           no_due_date: bool = False, attempts: int | None = None) -> dict:
    """Give one student a different due date and/or more attempts on one test or assignment.

    The accommodation route: nothing changes for the rest of the class.
    `due` ISO 8601 UTC, or `no_due_date=True`; `attempts` overrides the item's count.
    """
    payload: dict[str, Any] = {}
    if no_due_date:
        payload["gradableItemUserOptions"] = {"dueDateExceptionType": "Unlimited"}
    elif due:
        payload["gradableItemUserOptions"] = {"dueDateExceptionType": "LimitedFixed", "fixedDueDate": due}
    if attempts is not None:
        payload["assessmentUserOptions"] = {"attempts": attempts}
    if not payload:
        raise BlackboardError("nothing to change: give due, no_due_date or attempts")
    return await bb._request("PUT", f"/v1/courses/{course_id}/gradebook/columns/{column_id}/exceptions/users/{user_id}",
                             json=payload, write=True)


@mcp.tool()
async def bb_delete_exception(course_id: str, column_id: str, user_id: str) -> dict:
    """Remove a student's exception on one column."""
    await bb._request("DELETE", f"/v1/courses/{course_id}/gradebook/columns/{column_id}/exceptions/users/{user_id}",
                      write=True)
    return {"ok": True, "userId": user_id, "columnId": column_id}


# --------------------------------------------------------------------------
# forum
# --------------------------------------------------------------------------


@mcp.tool()
async def bb_list_discussions(course_id: str) -> list[dict]:
    """Discussion boards in the course."""
    rows = await bb._paged(f"/v1/courses/{course_id}/discussions")
    return [{"id": r.get("id"), "title": r.get("title"), "available": r.get("available"),
             "gradable": r.get("gradable"), "created": r.get("createdDate")} for r in rows]


@mcp.tool()
async def bb_discussion_messages(course_id: str, discussion_id: str, since: str = "") -> list[dict]:
    """Messages in one discussion with author names — the raw material for a weekly FAQ.

    `since` (ISO 8601) keeps only what was posted from that moment on.
    """
    params = {"posted": since} if since else None
    rows = await bb._paged(f"/v1/courses/{course_id}/discussions/{discussion_id}/messages", params)
    return [{"id": r.get("id"),
             "author": " ".join(p for p in (r.get("givenName"), r.get("familyName")) if p) or None,
             "body": r.get("body"), "posted": r.get("postDate"), "replyTo": r.get("parentId"),
             "read": r.get("isRead")} for r in rows]


@mcp.tool()
async def bb_create_discussion(course_id: str, title: str, body_html: str, available: bool = False,
                               gradable: bool = False) -> dict:
    """Open a discussion board with its first post (BBML). Hidden unless available=True."""
    check_bbml(body_html)
    r = await bb._request("POST", f"/v1/courses/{course_id}/discussions",
                          json={"title": title, "available": available, "gradable": gradable,
                                "topic": {"body": body_html, "status": "Published"}}, write=True)
    return {"id": r.get("id"), "title": title, "available": available}


@mcp.tool()
async def bb_post_discussion_message(course_id: str, discussion_id: str, body_html: str,
                                     reply_to: str = "") -> dict:
    """Post in a discussion, as a new message or as a reply to `reply_to`. Body is BBML."""
    check_bbml(body_html)
    base = f"/v1/courses/{course_id}/discussions/{discussion_id}/messages"
    path = f"{base}/{reply_to}/replies" if reply_to else base
    r = await bb._request("POST", path, json={"body": body_html, "status": "Published"}, write=True)
    return {"id": r.get("id"), "replyTo": reply_to or None}


# --------------------------------------------------------------------------
# calendar
# --------------------------------------------------------------------------


@mcp.tool()
async def bb_list_calendar(course_id: str, since: str = "", until: str = "") -> list[dict]:
    """Course calendar items (lectures, due dates) in a window; default is the next two weeks."""
    params: dict[str, Any] = {"courseId": course_id}
    if since:
        params["since"] = since
    if until:
        params["until"] = until
    rows = await bb._paged("/v1/calendars/items", params)
    return [{"id": r.get("id"), "title": r.get("title"), "type": r.get("type"), "start": r.get("start"),
             "end": r.get("end"), "location": r.get("location")} for r in rows]


@mcp.tool()
async def bb_create_calendar_item(course_id: str, title: str, start: str, end: str, description: str = "",
                                  location: str = "") -> dict:
    """Add one event to the course calendar students see. ISO 8601 UTC."""
    payload: dict[str, Any] = {"type": "Course", "calendarId": course_id, "title": title, "start": start, "end": end}
    if description:
        payload["description"] = description
    if location:
        payload["location"] = location
    r = await bb._request("POST", "/v1/calendars/items", json=payload, write=True)
    return {"id": r.get("id"), "title": title, "start": start}


@mcp.tool()
async def bb_delete_calendar_item(course_id: str, item_id: str) -> dict:
    """Remove one course calendar event."""
    await bb._request("DELETE", f"/v1/calendars/items/Course/{item_id}", write=True)
    return {"ok": True, "deleted": item_id}


# --------------------------------------------------------------------------
# audit
# --------------------------------------------------------------------------


@mcp.tool()
async def bb_gradebook_log(course_id: str, column_id: str = "", limit: int = 200) -> list[dict]:
    """Who changed which grade, when, from where — the whole course or one column."""
    path = (f"/v1/courses/{course_id}/gradebook/columns/{column_id}/logs" if column_id
            else f"/v1/courses/{course_id}/gradebook/logs")
    rows = await bb._paged(path, limit=limit)
    return [{"date": r.get("date"), "columnId": r.get("gradebookColumnId"),
             "student": (r.get("user") or {}).get("name"),
             "by": (r.get("modifier") or {}).get("name"), "role": (r.get("modifier") or {}).get("role"),
             "ip": (r.get("modifier") or {}).get("ipAddress"),
             "score": r.get("score"), "previous": r.get("previousScore")} for r in rows]


@mcp.tool()
async def bb_attempt_receipt(course_id: str, receipt_id: str) -> dict:
    """Verify a submission receipt a student shows you: date, size, attempt, whether it still exists."""
    return await bb._request("GET", f"/v1/courses/{course_id}/attemptReceipts/{receipt_id}")


# --------------------------------------------------------------------------
# course copy
# --------------------------------------------------------------------------

COPY_OPTIONS = {"adaptiveReleaseRules", "announcements", "assessments", "calendar", "contentAreas",
                "gradebook", "groupSettings", "rubrics", "discussions"}


@mcp.tool()
async def bb_copy_course(source_course_id: str, target_course_id: str, what: list[str]) -> dict:
    """Copy selected parts of one course into an existing one (e.g. last year's into this year's).

    what ⊆ {contentAreas, assessments, announcements, calendar, gradebook, rubrics,
    adaptiveReleaseRules, groupSettings, discussions}. Runs as a server task;
    the target course is modified in place, so read it back afterwards.
    """
    bad = [w for w in what if w not in COPY_OPTIONS]
    if bad or not what:
        raise BlackboardError(f"unknown copy option(s) {bad}; choose from {sorted(COPY_OPTIONS)}")
    copy: dict[str, Any] = {w: True for w in what if w != "discussions"}
    if "discussions" in what:
        copy["discussions"] = "ForumsOnly"
    return await bb._request("POST", f"/v2/courses/{source_course_id}/copy",
                             json={"targetCourse": {"id": {"id": target_course_id}}, "copy": copy}, write=True)


# --------------------------------------------------------------------------
# groups
# --------------------------------------------------------------------------


@mcp.tool()
async def bb_list_groups(course_id: str) -> list[dict]:
    """Groups in the course with their members (names and student numbers)."""
    students = {s["userId"]: s for s in await _students(course_id)}
    out = []
    for g in await bb._paged(f"/v2/courses/{course_id}/groups"):
        members = await bb._paged(f"/v2/courses/{course_id}/groups/{g['id']}/users")
        out.append({"id": g.get("id"), "name": g.get("name"), "setId": g.get("groupSetId"),
                    "available": (g.get("availability") or {}).get("available"),
                    "members": [{"userId": m.get("userId"),
                                 "name": students.get(m.get("userId"), {}).get("name"),
                                 "studentId": students.get(m.get("userId"), {}).get("studentId")}
                                for m in members]})
    return out


@mcp.tool()
async def bb_create_group_set(course_id: str, name: str, groups: list[dict], available: bool = False,
                              description: str = "", self_enroll: bool = False, limit: int = 0) -> dict:
    """Create a group set with its groups and members in one go.

    Two uses. Project teams from a sheet: groups = [{"name": "Team A", "members":
    [userId or student number, …]}, …], instructor-enrolled, hidden unless
    available=True. Booking slots (office hours, exam review): self_enroll=True
    with a `limit` per group — students pick a slot themselves from the sign-up
    sheet; bb_list_groups then tells you who comes when. Unmatched member ids
    are returned, not silently dropped.
    """
    index = await _by_id(course_id)
    if self_enroll:
        avail = {"available": "SignupOnly" if available else "No"}
        enrol: dict[str, Any] = {"type": "SelfEnrollment", "signupSheet": {"name": name, "showMembers": False}}
        if limit:
            enrol["limit"] = limit
    else:
        avail = {"available": "Yes" if available else "No"}
        enrol = {"type": "InstructorOnly"}
    gs = await bb._request("POST", f"/v2/courses/{course_id}/groups/sets",
                           json={"name": name, "description": description, "availability": avail,
                                 "enrollment": enrol}, write=True)
    made, unknown = [], []
    for g in groups:
        created = await bb._request("POST", f"/v2/courses/{course_id}/groups/sets/{gs['id']}/groups",
                                    json={"name": g["name"], "availability": avail, "enrollment": enrol},
                                    write=True)
        found, missing = _resolve(index, g.get("members") or [])
        unknown += missing
        for uid in found:
            await bb._request("PUT", f"/v2/courses/{course_id}/groups/{created['id']}/users/{uid}", write=True)
        made.append({"id": created.get("id"), "name": g["name"], "members": found})
    return {"set_id": gs.get("id"), "groups": made, "unknown": unknown}


@mcp.tool()
async def bb_assign_content_to_groups(course_id: str, content_id: str, group_ids: list[str]) -> dict:
    """Make an assignment a group assignment: associate it with every group of a set."""
    for gid in group_ids:
        await bb._request("PUT", f"/v1/courses/{course_id}/contents/{content_id}/groups/{gid}", write=True)
    return {"contentId": content_id, "groups": group_ids}


@mcp.tool()
async def bb_list_group_attempts(course_id: str, column_id: str) -> list[dict]:
    """Submissions made by groups on one column."""
    rows = await bb._paged(f"/v1/courses/{course_id}/gradebook/columns/{column_id}/groupAttempts")
    return [{"groupAttemptId": r.get("id"), "groupId": r.get("groupId"), "status": r.get("status"),
             "score": r.get("score"), "attemptDate": r.get("attemptDate")} for r in rows]


@mcp.tool()
async def bb_grade_group_attempt(course_id: str, column_id: str, group_attempt_id: str, score: float,
                                 feedback: str = "", notes: str = "", post: bool = True) -> dict:
    """Mark a group submission once; every member receives it. post=True publishes."""
    payload: dict[str, Any] = {"score": score}
    if feedback:
        payload["feedback"] = feedback
    if notes:
        payload["notes"] = notes
    if post:
        payload["status"] = "Completed"
    return await bb._request(
        "PATCH", f"/v1/courses/{course_id}/gradebook/columns/{column_id}/groupAttempts/{group_attempt_id}",
        json=payload, grade_write=True)
