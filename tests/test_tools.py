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
