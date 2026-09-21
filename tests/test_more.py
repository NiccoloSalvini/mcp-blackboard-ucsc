"""Engagement, attendance, rubrics, adaptive release, exceptions, forum,
calendar, audit, course copy, groups."""
import asyncio

import pytest

from bb_mcp import client, server


def run(coro):
    return asyncio.run(coro)


def fake_api(routes):
    calls = []

    async def _request(method, path, *, json=None, params=None, **kw):
        calls.append((method, path, params, json, kw))
        hit = routes[(method, path)]
        return hit(params, json) if callable(hit) else hit

    return _request, calls


STUDENTS = ("GET", "/v1/courses/_1_1/users"), {"results": [
    {"userId": "_9_1", "courseRoleId": "Student",
     "user": {"name": {"given": "Ada", "family": "Lovelace"}, "studentId": "123",
              "contact": {"email": "ada@icatt.it"}}},
    {"userId": "_8_1", "courseRoleId": "Student",
     "user": {"name": {"given": "Bob", "family": "Ross"}, "studentId": "456"}},
]}


@pytest.fixture
def writes(monkeypatch):
    monkeypatch.setattr(client, "ALLOW_WRITES", True)
    monkeypatch.setattr(client, "ALLOW_GRADE_WRITES", True)


# --- engagement ---------------------------------------------------------------

def test_review_status_joins_names_and_flags_never_opened(monkeypatch):
    req, _ = fake_api({
        STUDENTS[0]: STUDENTS[1],
        ("GET", "/v1/courses/_1_1/performance/contentReviewStatus"): {"results": [
            {"userId": "_9_1", "reviewedCount": 3, "reviewableCount": 15},
            {"userId": "_8_1", "reviewedCount": 0, "reviewableCount": 15}]},
    })
    monkeypatch.setattr(client, "_request", req)
    out = run(server.bb_review_status("_1_1"))
    assert out[0] == {"userId": "_9_1", "name": "Ada Lovelace", "studentId": "123",
                      "reviewed": 3, "reviewable": 15}
    assert out[1]["reviewed"] == 0


def test_content_viewed_asks_state_per_student(monkeypatch):
    req, calls = fake_api({
        STUDENTS[0]: STUDENTS[1],
        ("GET", "/v1/courses/_1_1/contents/_c_1/states/_9_1"): {"state": "Completed"},
    })
    monkeypatch.setattr(client, "_request", req)

    async def req404(method, path, **kw):
        if path.endswith("/_8_1"):
            raise client.BlackboardError(f"Not found: GET {path}")
        return await req(method, path, **kw)

    monkeypatch.setattr(client, "_request", req404)
    out = run(server.bb_content_viewed("_1_1", "_c_1"))
    assert [(r["name"], r["state"]) for r in out] == [("Ada Lovelace", "Completed"), ("Bob Ross", None)]


# --- attendance -----------------------------------------------------------------

def test_create_meeting(monkeypatch, writes):
    req, calls = fake_api({("POST", "/v1/courses/_1_1/meetings"): {"id": "_m_1", "title": "L3"}})
    monkeypatch.setattr(client, "_request", req)
    out = run(server.bb_create_meeting("_1_1", "L3", "2026-10-05T08:00:00.000Z", "2026-10-05T10:00:00.000Z"))
    assert calls[0][3] == {"title": "L3", "start": "2026-10-05T08:00:00.000Z", "end": "2026-10-05T10:00:00.000Z"}
    assert out["id"] == "_m_1"


def test_mark_attendance_resolves_student_numbers_and_defaults_absent(monkeypatch, writes):
    req, calls = fake_api({
        STUDENTS[0]: STUDENTS[1],
        ("POST", "/v1/courses/_1_1/meetings/_m_1/users/bulk"): {"ok": True},
    })
    monkeypatch.setattr(client, "_request", req)
    out = run(server.bb_mark_attendance("_1_1", "_m_1", present=["123"], late=[]))
    body = calls[-1][3]
    assert body == [{"meetingId": "_m_1", "userId": "_9_1", "status": "Present"},
                    {"meetingId": "_m_1", "userId": "_8_1", "status": "Absent"}]
    assert out == {"meetingId": "_m_1", "present": 1, "late": 0, "absent": 1, "unknown": []}


def test_mark_attendance_reports_unknown_ids(monkeypatch, writes):
    req, calls = fake_api({
        STUDENTS[0]: STUDENTS[1],
        ("POST", "/v1/courses/_1_1/meetings/_m_1/users/bulk"): {"ok": True},
    })
    monkeypatch.setattr(client, "_request", req)
    out = run(server.bb_mark_attendance("_1_1", "_m_1", present=["999"], late=["_8_1"], mark_absent=False))
    assert calls[-1][3] == [{"meetingId": "_m_1", "userId": "_8_1", "status": "Late"}]
    assert out["unknown"] == ["999"]


def test_attendance_report_one_row_per_student(monkeypatch):
    req, _ = fake_api({
        STUDENTS[0]: STUDENTS[1],
        ("GET", "/v1/courses/_1_1/meetings"): {"results": [
            {"id": "_m_1", "title": "L1", "start": "2026-10-01T08:00:00Z"},
            {"id": "_m_2", "title": "L2", "start": "2026-10-03T08:00:00Z"}]},
        ("GET", "/v1/courses/_1_1/meetings/_m_1/users"): {"results": [{"userId": "_9_1", "status": "Present"}]},
        ("GET", "/v1/courses/_1_1/meetings/_m_2/users"): {"results": [
            {"userId": "_9_1", "status": "Late"}, {"userId": "_8_1", "status": "Present"}]},
    })
    monkeypatch.setattr(client, "_request", req)
    rep = run(server.bb_attendance_report("_1_1"))
    assert [m["title"] for m in rep["meetings"]] == ["L1", "L2"]
    assert rep["rows"][0] == {"userId": "_9_1", "name": "Ada Lovelace", "studentId": "123",
                              "status": {"_m_1": "Present", "_m_2": "Late"}, "present": 2}
    assert rep["rows"][1]["status"] == {"_m_1": None, "_m_2": "Present"}


# --- rubrics ------------------------------------------------------------------------

def test_create_rubric_builds_grid_from_criteria(monkeypatch, writes):
    req, calls = fake_api({("POST", "/v1/courses/_1_1/rubrics"): {"id": "_r_1", "title": "HW"}})
    monkeypatch.setattr(client, "_request", req)
    run(server.bb_create_rubric("_1_1", "HW", [
        {"criterion": "Codice", "levels": [{"header": "Ottimo", "points": 5, "description": "gira"},
                                            {"header": "Scarso", "points": 1, "description": "non gira"}]},
        {"criterion": "Relazione", "levels": [{"header": "Ottimo", "points": 5},
                                               {"header": "Scarso", "points": 0}]},
    ]))
    body = calls[0][3]
    assert body["rubricType"] == "Numeric"
    assert [c["header"] for c in body["columns"]] == ["Ottimo", "Scarso"]
    assert [r["header"] for r in body["rows"]] == ["Codice", "Relazione"]
    assert body["rows"][0]["rowPoints"] == 5 and body["rows"][0]["position"] == 0
    cell = body["cells"][1]
    assert cell == {"rowPosition": 0, "columnPosition": 1, "description": "non gira", "points": {"value": 1}}
    assert len(body["cells"]) == 4


def test_create_rubric_rejects_ragged_levels(monkeypatch, writes):
    req, calls = fake_api({})
    monkeypatch.setattr(client, "_request", req)
    with pytest.raises(client.BlackboardError, match="same number of levels"):
        run(server.bb_create_rubric("_1_1", "HW", [
            {"criterion": "A", "levels": [{"header": "x", "points": 1}]},
            {"criterion": "B", "levels": [{"header": "x", "points": 1}, {"header": "y", "points": 0}]}]))
    assert calls == []


def test_attach_rubric_to_column(monkeypatch, writes):
    req, calls = fake_api({("POST", "/v1/courses/_1_1/rubrics/_r_1/associations"): {"id": "_ra_1"}})
    monkeypatch.setattr(client, "_request", req)
    out = run(server.bb_attach_rubric("_1_1", "_r_1", "_g_1"))
    assert calls[0][3] == {"associationEntity": {"gradebookColumnId": "_g_1"}, "usedForGrading": True,
                           "rubricVisibility": "VisibleAfterGrading"}
    assert out["id"] == "_ra_1"


def test_evaluate_rubric_sends_fractions_and_needs_grade_switch(monkeypatch, writes):
    req, calls = fake_api({
        ("POST", "/v1/courses/_1_1/rubrics/_r_1/associations/_ra_1/evaluations"): {"id": "_e_1"},
    })
    monkeypatch.setattr(client, "_request", req)
    run(server.bb_evaluate_rubric("_1_1", "_r_1", "_ra_1", "_t_1", [
        {"row_id": "_row_1", "cell_id": "_cell_1", "score": 4.5, "feedback": "<p>ok</p>"},
        {"row_id": "_row_2", "cell_id": "_cell_9"}]))
    body = calls[0][3]
    assert body["evaluationEntity"] == {"attemptId": "_t_1"}
    assert body["cells"][0] == {"rubricRowId": "_row_1", "rubricCellId": "_cell_1",
                                "selectedScore": {"numerator": 9, "denominator": 2}, "feedback": "<p>ok</p>"}
    assert body["cells"][1] == {"rubricRowId": "_row_2", "rubricCellId": "_cell_9"}
    assert calls[0][4]["grade_write"] is True


# --- adaptive release -----------------------------------------------------------------

def test_release_by_grade_creates_rule_then_criterion(monkeypatch, writes):
    req, calls = fake_api({
        ("POST", "/v1/courses/_1_1/contents/_c_1/adaptiveRelease/rules"): {"id": "_rule_1"},
        ("POST", "/v1/courses/_1_1/contents/_c_1/adaptiveRelease/rules/_rule_1/criteria"): {"id": "_cr_1"},
    })
    monkeypatch.setattr(client, "_request", req)
    out = run(server.bb_release_by_grade("_1_1", "_c_1", "_g_1", min_score=0, max_score=17.99))
    assert calls[0][3] == {"title": "Grade on _g_1 between 0 and 17.99"}
    assert calls[1][3] == {"type": "GradeRange", "gradebookColumnId": "_g_1", "minScore": 0, "maxScore": 17.99}
    assert out == {"rule_id": "_rule_1", "criterion_id": "_cr_1"}


def test_release_by_date(monkeypatch, writes):
    req, calls = fake_api({
        ("POST", "/v1/courses/_1_1/contents/_c_1/adaptiveRelease/rules"): {"id": "_rule_1"},
        ("POST", "/v1/courses/_1_1/contents/_c_1/adaptiveRelease/rules/_rule_1/criteria"): {"id": "_cr_1"},
    })
    monkeypatch.setattr(client, "_request", req)
    run(server.bb_release_by_date("_1_1", "_c_1", start="2026-10-01T00:00:00.000Z", title="Week 3"))
    assert calls[0][3] == {"title": "Week 3"}
    assert calls[1][3] == {"type": "DateRange", "startDate": "2026-10-01T00:00:00.000Z"}


def test_release_to_users_sets_membership_list(monkeypatch, writes):
    req, calls = fake_api({
        ("POST", "/v1/courses/_1_1/contents/_c_1/adaptiveRelease/rules"): {"id": "_rule_1"},
        ("POST", "/v1/courses/_1_1/contents/_c_1/adaptiveRelease/rules/_rule_1/criteria"): {"id": "_cr_1"},
        ("PUT", "/v1/courses/_1_1/contents/_c_1/adaptiveRelease/rules/_rule_1/criteria/_cr_1/users"): {"ok": True},
    })
    monkeypatch.setattr(client, "_request", req)
    run(server.bb_release_to_users("_1_1", "_c_1", ["_9_1", "_8_1"]))
    assert calls[1][3] == {"type": "Memberships"}
    assert calls[2][3] == [{"userId": "_9_1"}, {"userId": "_8_1"}]


def test_list_release_rules_includes_criteria(monkeypatch):
    req, _ = fake_api({
        ("GET", "/v1/courses/_1_1/contents/_c_1/adaptiveRelease/rules"): {"results": [{"id": "_rule_1", "title": "T"}]},
        ("GET", "/v1/courses/_1_1/contents/_c_1/adaptiveRelease/rules/_rule_1/criteria"): {"results": [
            {"id": "_cr_1", "type": "DateRange"}]},
    })
    monkeypatch.setattr(client, "_request", req)
    assert run(server.bb_list_release_rules("_1_1", "_c_1")) == [
        {"id": "_rule_1", "title": "T", "criteria": [{"id": "_cr_1", "type": "DateRange"}]}]


# --- exceptions ---------------------------------------------------------------------------

def test_set_exception_due_and_attempts(monkeypatch, writes):
    req, calls = fake_api({("PUT", "/v1/courses/_1_1/gradebook/columns/_g_1/exceptions/users/_9_1"): {"ok": True}})
    monkeypatch.setattr(client, "_request", req)
    run(server.bb_set_exception("_1_1", "_g_1", "_9_1", due="2026-10-20T22:59:00.000Z", attempts=3))
    assert calls[0][3] == {"gradableItemUserOptions": {"dueDateExceptionType": "LimitedFixed",
                                                        "fixedDueDate": "2026-10-20T22:59:00.000Z"},
                           "assessmentUserOptions": {"attempts": 3}}
    run(server.bb_set_exception("_1_1", "_g_1", "_9_1", no_due_date=True))
    assert calls[1][3] == {"gradableItemUserOptions": {"dueDateExceptionType": "Unlimited"}}
    with pytest.raises(client.BlackboardError, match="nothing to change"):
        run(server.bb_set_exception("_1_1", "_g_1", "_9_1"))


# --- forum ----------------------------------------------------------------------------------

def test_discussion_messages_carry_author_names(monkeypatch):
    req, calls = fake_api({
        ("GET", "/v1/courses/_1_1/discussions/_d_1/messages"): {"results": [
            {"id": "_m_1", "givenName": "Ada", "familyName": "Lovelace", "body": "<p>Q?</p>",
             "postDate": "2026-10-02T10:00:00Z", "parentId": None, "isRead": False}]},
    })
    monkeypatch.setattr(client, "_request", req)
    out = run(server.bb_discussion_messages("_1_1", "_d_1", since="2026-10-01T00:00:00.000Z"))
    assert out == [{"id": "_m_1", "author": "Ada Lovelace", "body": "<p>Q?</p>", "posted": "2026-10-02T10:00:00Z",
                    "replyTo": None, "read": False}]
    assert calls[0][2]["posted"] == "2026-10-01T00:00:00.000Z"


def test_create_discussion_and_post(monkeypatch, writes):
    req, calls = fake_api({
        ("POST", "/v1/courses/_1_1/discussions"): {"id": "_d_1"},
        ("POST", "/v1/courses/_1_1/discussions/_d_1/messages"): {"id": "_m_1"},
        ("POST", "/v1/courses/_1_1/discussions/_d_1/messages/_m_1/replies"): {"id": "_m_2"},
    })
    monkeypatch.setattr(client, "_request", req)
    run(server.bb_create_discussion("_1_1", "FAQ", "<p>Ask here</p>"))
    assert calls[0][3] == {"title": "FAQ", "available": False, "gradable": False,
                           "topic": {"body": "<p>Ask here</p>", "status": "Published"}}
    run(server.bb_post_discussion_message("_1_1", "_d_1", "<p>A</p>"))
    assert calls[1][3] == {"body": "<p>A</p>", "status": "Published"}
    run(server.bb_post_discussion_message("_1_1", "_d_1", "<p>R</p>", reply_to="_m_1"))
    assert calls[2][1].endswith("/messages/_m_1/replies")
    with pytest.raises(client.BlackboardError, match="<b>"):
        run(server.bb_post_discussion_message("_1_1", "_d_1", "<b>x</b>"))


# --- calendar --------------------------------------------------------------------------------

def test_calendar_list_and_create(monkeypatch, writes):
    req, calls = fake_api({
        ("GET", "/v1/calendars/items"): {"results": [{"id": "_i_1", "title": "L1", "start": "s", "end": "e",
                                                       "type": "Course", "location": "Aula 3"}]},
        ("POST", "/v1/calendars/items"): {"id": "_i_2"},
    })
    monkeypatch.setattr(client, "_request", req)
    out = run(server.bb_list_calendar("_1_1", since="2026-10-01T00:00:00.000Z", until="2026-12-01T00:00:00.000Z"))
    assert {k: calls[0][2][k] for k in ("courseId", "since", "until")} == {"courseId": "_1_1", "since": "2026-10-01T00:00:00.000Z", "until": "2026-12-01T00:00:00.000Z"}
    assert out[0]["location"] == "Aula 3"
    run(server.bb_create_calendar_item("_1_1", "L2", "2026-10-05T08:00:00.000Z", "2026-10-05T10:00:00.000Z",
                                       location="Aula 3"))
    assert calls[1][3] == {"type": "Course", "calendarId": "_1_1", "title": "L2", "start": "2026-10-05T08:00:00.000Z",
                           "end": "2026-10-05T10:00:00.000Z", "location": "Aula 3"}


# --- audit ------------------------------------------------------------------------------------

def test_gradebook_log_flattens_who_changed_what(monkeypatch):
    req, calls = fake_api({
        ("GET", "/v1/courses/_1_1/gradebook/columns/_g_1/logs"): {"results": [
            {"id": "_l_1", "gradebookColumnId": "_g_1", "user": {"userId": "_9_1", "name": "Ada Lovelace"},
             "modifier": {"userId": "_u_1", "name": "N S", "role": "Instructor", "ipAddress": "1.2.3.4"},
             "date": "2026-10-30T14:00:00Z", "score": 28.0, "previousScore": 27.0}]},
    })
    monkeypatch.setattr(client, "_request", req)
    out = run(server.bb_gradebook_log("_1_1", column_id="_g_1"))
    assert out[0] == {"date": "2026-10-30T14:00:00Z", "columnId": "_g_1", "student": "Ada Lovelace",
                      "by": "N S", "role": "Instructor", "ip": "1.2.3.4", "score": 28.0, "previous": 27.0}


# --- course copy ------------------------------------------------------------------------------

def test_copy_course_partial_into_existing(monkeypatch, writes):
    req, calls = fake_api({("POST", "/v2/courses/_old_1/copy"): {"ok": True}})
    monkeypatch.setattr(client, "_request", req)
    run(server.bb_copy_course("_old_1", "_new_1", what=["contentAreas", "rubrics"]))
    assert calls[0][3] == {"targetCourse": {"id": {"id": "_new_1"}},
                           "copy": {"contentAreas": True, "rubrics": True}}
    with pytest.raises(client.BlackboardError, match="unknown copy option"):
        run(server.bb_copy_course("_old_1", "_new_1", what=["students"]))


# --- groups -------------------------------------------------------------------------------------

def test_create_group_set_with_members(monkeypatch, writes):
    req, calls = fake_api({
        STUDENTS[0]: STUDENTS[1],
        ("POST", "/v2/courses/_1_1/groups/sets"): {"id": "_set_1"},
        ("POST", "/v2/courses/_1_1/groups/sets/_set_1/groups"): lambda p, j: {"id": "_grp_" + j["name"]},
        ("PUT", "/v2/courses/_1_1/groups/_grp_A/users/_9_1"): {"ok": True},
        ("PUT", "/v2/courses/_1_1/groups/_grp_B/users/_8_1"): {"ok": True},
    })
    monkeypatch.setattr(client, "_request", req)
    out = run(server.bb_create_group_set("_1_1", "Progetti", [{"name": "A", "members": ["123"]},
                                                             {"name": "B", "members": ["_8_1"]}]))
    assert calls[1][3]["availability"] == {"available": "No"}
    assert calls[1][3]["enrollment"] == {"type": "InstructorOnly"}
    assert out == {"set_id": "_set_1", "groups": [{"id": "_grp_A", "name": "A", "members": ["_9_1"]},
                                                  {"id": "_grp_B", "name": "B", "members": ["_8_1"]}],
                   "unknown": []}


def test_list_groups_with_members(monkeypatch):
    req, _ = fake_api({
        STUDENTS[0]: STUDENTS[1],
        ("GET", "/v2/courses/_1_1/groups"): {"results": [{"id": "_grp_1", "name": "A", "groupSetId": "_set_1",
                                                          "availability": {"available": "No"}}]},
        ("GET", "/v2/courses/_1_1/groups/_grp_1/users"): {"results": [{"userId": "_9_1"}]},
    })
    monkeypatch.setattr(client, "_request", req)
    out = run(server.bb_list_groups("_1_1"))
    assert out == [{"id": "_grp_1", "name": "A", "setId": "_set_1", "available": "No",
                    "members": [{"userId": "_9_1", "name": "Ada Lovelace", "studentId": "123"}]}]


def test_assign_content_to_groups(monkeypatch, writes):
    req, calls = fake_api({
        ("PUT", "/v1/courses/_1_1/contents/_c_1/groups/_grp_1"): {"ok": True},
        ("PUT", "/v1/courses/_1_1/contents/_c_1/groups/_grp_2"): {"ok": True},
    })
    monkeypatch.setattr(client, "_request", req)
    assert run(server.bb_assign_content_to_groups("_1_1", "_c_1", ["_grp_1", "_grp_2"])) == {
        "contentId": "_c_1", "groups": ["_grp_1", "_grp_2"]}
    assert len(calls) == 2 and calls[0][4]["write"] is True


def test_grade_group_attempt(monkeypatch, writes):
    req, calls = fake_api({("PATCH", "/v1/courses/_1_1/gradebook/columns/_g_1/groupAttempts/_ga_1"): {"id": "_ga_1"}})
    monkeypatch.setattr(client, "_request", req)
    run(server.bb_grade_group_attempt("_1_1", "_g_1", "_ga_1", 27, feedback="<p>bravi</p>"))
    assert calls[0][3] == {"score": 27, "feedback": "<p>bravi</p>", "status": "Completed"}
    assert calls[0][4]["grade_write"] is True
