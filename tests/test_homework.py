"""Tools for homework: create the assignment, find what needs grading, fetch
what students handed in, grade the attempt. Plus announcement edits, content
delete, column edit, CSV export, the exam shortcut, and the multipart upload."""
import asyncio
import csv

import httpx
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


STUDENTS = {"results": [
    {"userId": "_9_1", "courseRoleId": "Student",
     "user": {"name": {"given": "Ada", "family": "Lovelace"}, "studentId": "123",
              "contact": {"email": "ada@icatt.it"}}},
]}


@pytest.fixture
def writes(monkeypatch):
    monkeypatch.setattr(client, "ALLOW_WRITES", True)
    monkeypatch.setattr(client, "ALLOW_GRADE_WRITES", True)


# --- create assignment ------------------------------------------------------

def test_create_assignment_one_post_hidden_with_points_and_due(monkeypatch, writes):
    req, calls = fake_api({
        ("POST", "/v1/courses/_1_1/contents/createAssignment"):
            {"contentId": "_c_1", "gradeColumnId": "_g_1", "assessmentId": "_a_1"},
    })
    monkeypatch.setattr(client, "_request", req)
    out = run(server.bb_create_assignment(
        "_1_1", "HW1", "<p>Do it</p>", parent_id="_f_1", points_possible=10,
        due="2026-10-15T23:59:00.000Z", attempts_allowed=2))
    assert out == {"content_id": "_c_1", "grade_column_id": "_g_1", "assessment_id": "_a_1",
                   "title": "HW1", "available": "No"}
    body = calls[0][3]
    assert body["parentId"] == "_f_1" and body["title"] == "HW1"
    assert body["instructions"] == "<p>Do it</p>"
    assert body["availability"] == {"available": "No"}
    assert body["score"] == {"possible": 10}
    assert body["grading"] == {"due": "2026-10-15T23:59:00.000Z", "attemptsAllowed": 2}
    assert "fileUploadIds" not in body
    assert calls[0][4]["write"] is True


def test_create_assignment_rejects_non_bbml_instructions(monkeypatch, writes):
    req, calls = fake_api({})
    monkeypatch.setattr(client, "_request", req)
    with pytest.raises(client.BlackboardError, match="<b>"):
        run(server.bb_create_assignment("_1_1", "HW1", "<b>x</b>"))
    assert calls == []


# --- what needs grading -----------------------------------------------------

def test_needs_grading_joins_attempts_with_names(monkeypatch):
    req, _ = fake_api({
        ("GET", "/v1/courses/_1_1/users"): STUDENTS,
        ("GET", "/v2/courses/_1_1/gradebook/columns"): {"results": [
            {"id": "_g_1", "name": "HW1", "grading": {"type": "Attempts"}},
            {"id": "_g_2", "name": "Manual", "grading": {"type": "Manual"}},
        ]},
        ("GET", "/v2/courses/_1_1/gradebook/columns/_g_1/attempts"): {"results": [
            {"id": "_t_1", "userId": "_9_1", "status": "NeedsGrading", "attemptDate": "2026-10-10T10:00:00Z"},
            {"id": "_t_2", "userId": "_9_1", "status": "Completed", "score": 9},
        ]},
    })
    monkeypatch.setattr(client, "_request", req)
    out = run(server.bb_needs_grading("_1_1"))
    assert out == [{"columnId": "_g_1", "column": "HW1", "attemptId": "_t_1", "userId": "_9_1",
                    "name": "Ada Lovelace", "email": "ada@icatt.it", "studentId": "123",
                    "attemptDate": "2026-10-10T10:00:00Z"}]


# --- submission files -------------------------------------------------------

def test_list_submission_files_uses_v1_attempt_route(monkeypatch):
    req, calls = fake_api({
        ("GET", "/v1/courses/_1_1/gradebook/attempts/_t_1/files"): {"results": [
            {"id": "_f_1", "name": "hw1.pdf"}]},
    })
    monkeypatch.setattr(client, "_request", req)
    assert run(server.bb_list_submission_files("_1_1", "_t_1")) == [{"id": "_f_1", "name": "hw1.pdf"}]


def test_download_submission_writes_each_file(monkeypatch, tmp_path):
    req, _ = fake_api({
        ("GET", "/v1/courses/_1_1/gradebook/attempts/_t_1/files"): {"results": [
            {"id": "_f_1", "name": "hw1.pdf"}, {"id": "_f_2", "name": "code.R"}]},
    })
    monkeypatch.setattr(client, "_request", req)
    got = []

    async def fake_bytes(path):
        got.append(path)
        return b"%PDF" if path.endswith("_f_1/download") else b"x <- 1"

    monkeypatch.setattr(client, "_download", fake_bytes)
    out = run(server.bb_download_submission("_1_1", "_t_1", str(tmp_path / "ada")))
    assert got == ["/v1/courses/_1_1/gradebook/attempts/_t_1/files/_f_1/download",
                   "/v1/courses/_1_1/gradebook/attempts/_t_1/files/_f_2/download"]
    assert (tmp_path / "ada" / "hw1.pdf").read_bytes() == b"%PDF"
    assert (tmp_path / "ada" / "code.R").read_text() == "x <- 1"
    assert out["files"] == [str(tmp_path / "ada" / "hw1.pdf"), str(tmp_path / "ada" / "code.R")]


def test_client_download_returns_bytes_and_follows_redirect(monkeypatch):
    class FakeClient:
        def __init__(self, **kw):
            self.kw = kw
            self.calls = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600},
                                  request=httpx.Request("POST", url))

        async def request(self, method, url, **kw):
            self.calls.append((method, url, kw))
            return httpx.Response(200, content=b"bytes!", request=httpx.Request(method, url))

    made = []
    monkeypatch.setattr(client, "APP_KEY", "k")
    monkeypatch.setattr(client, "APP_SECRET", "s")
    monkeypatch.setattr(client, "_token", None)
    monkeypatch.setattr(client.httpx, "AsyncClient", lambda **kw: made.append(FakeClient(**kw)) or made[-1])
    assert run(client._download("/v1/x/download")) == b"bytes!"
    assert made[-1].kw.get("follow_redirects") is True
    assert made[-1].calls[0][2]["headers"]["Authorization"] == "Bearer t"


# --- grade an attempt -------------------------------------------------------

def test_grade_attempt_posts_with_completed_status(monkeypatch, writes):
    req, calls = fake_api({
        ("PATCH", "/v2/courses/_1_1/gradebook/columns/_g_1/attempts/_t_1"):
            {"id": "_t_1", "status": "Completed", "score": 8.5},
    })
    monkeypatch.setattr(client, "_request", req)
    out = run(server.bb_grade_attempt("_1_1", "_g_1", "_t_1", 8.5, feedback="<p>Good</p>"))
    assert calls[0][3] == {"score": 8.5, "feedback": "<p>Good</p>", "status": "Completed"}
    assert calls[0][4]["grade_write"] is True
    assert out["status"] == "Completed"


def test_grade_attempt_can_hold_back_posting(monkeypatch, writes):
    req, calls = fake_api({
        ("PATCH", "/v2/courses/_1_1/gradebook/columns/_g_1/attempts/_t_1"): {"id": "_t_1"},
    })
    monkeypatch.setattr(client, "_request", req)
    run(server.bb_grade_attempt("_1_1", "_g_1", "_t_1", 8.5, notes="check q3", post=False))
    assert calls[0][3] == {"score": 8.5, "notes": "check q3"}


def test_grade_attempt_needs_grade_switch(monkeypatch):
    monkeypatch.setattr(client, "ALLOW_WRITES", True)
    monkeypatch.setattr(client, "ALLOW_GRADE_WRITES", False)
    with pytest.raises(client.BlackboardError, match="BB_ALLOW_GRADE_WRITES"):
        run(server.bb_grade_attempt("_1_1", "_g_1", "_t_1", 8.5))


def test_student_grades_lists_every_column_for_one_student(monkeypatch):
    req, _ = fake_api({
        ("GET", "/v2/courses/_1_1/gradebook/columns"): {"results": [
            {"id": "_g_1", "name": "HW1", "score": {"possible": 10}}]},
        ("GET", "/v2/courses/_1_1/gradebook/users/_9_1"): {"results": [
            {"columnId": "_g_1", "status": "Graded", "displayGrade": {"score": 8}}]},
    })
    monkeypatch.setattr(client, "_request", req)
    assert run(server.bb_student_grades("_1_1", "_9_1")) == [
        {"columnId": "_g_1", "column": "HW1", "possible": 10, "status": "Graded", "score": 8}]


# --- announcements ----------------------------------------------------------

def test_update_announcement_sends_only_given_fields(monkeypatch, writes):
    req, calls = fake_api({("PATCH", "/v1/courses/_1_1/announcements/_n_1"): {"id": "_n_1"}})
    monkeypatch.setattr(client, "_request", req)
    run(server.bb_update_announcement("_1_1", "_n_1", body_html="<p>fixed</p>"))
    assert calls[0][3] == {"body": "<p>fixed</p>"}
    run(server.bb_update_announcement("_1_1", "_n_1", title="T", draft=False))
    assert calls[1][3] == {"title": "T", "draft": False}
    with pytest.raises(client.BlackboardError, match="nothing to change"):
        run(server.bb_update_announcement("_1_1", "_n_1"))


def test_delete_announcement(monkeypatch, writes):
    req, calls = fake_api({("DELETE", "/v1/courses/_1_1/announcements/_n_1"): {"ok": True}})
    monkeypatch.setattr(client, "_request", req)
    assert run(server.bb_delete_announcement("_1_1", "_n_1")) == {"ok": True, "deleted": "_n_1"}
    assert calls[0][4]["write"] is True


# --- content delete, column edit --------------------------------------------

def test_delete_content_passes_gradebook_flag(monkeypatch, writes):
    req, calls = fake_api({("DELETE", "/v1/courses/_1_1/contents/_c_1"): {"ok": True}})
    monkeypatch.setattr(client, "_request", req)
    run(server.bb_delete_content("_1_1", "_c_1"))
    assert calls[0][2] == {"deleteGradebookEntries": "false"}
    run(server.bb_delete_content("_1_1", "_c_1", delete_grades=True))
    assert calls[1][2] == {"deleteGradebookEntries": "true"}


def test_update_gradebook_column_sends_only_given_fields(monkeypatch, writes):
    req, calls = fake_api({("PATCH", "/v2/courses/_1_1/gradebook/columns/_g_1"): {"id": "_g_1"}})
    monkeypatch.setattr(client, "_request", req)
    run(server.bb_update_gradebook_column("_1_1", "_g_1", points_possible=30, due="2026-11-01T10:00:00.000Z"))
    assert calls[0][3] == {"score": {"possible": 30}, "grading": {"due": "2026-11-01T10:00:00.000Z"}}
    run(server.bb_update_gradebook_column("_1_1", "_g_1", name="Exam", available=True))
    assert calls[1][3] == {"name": "Exam", "availability": {"available": "Yes"}}
    with pytest.raises(client.BlackboardError, match="nothing to change"):
        run(server.bb_update_gradebook_column("_1_1", "_g_1"))


# --- CSV export ---------------------------------------------------------------

def test_export_gradebook_csv(monkeypatch, tmp_path):
    req, _ = fake_api({
        ("GET", "/v1/courses/_1_1/users"): STUDENTS,
        ("GET", "/v2/courses/_1_1/gradebook/columns"): {"results": [
            {"id": "_a_1", "name": "HW1", "score": {"possible": 10}},
            {"id": "_b_1", "name": "Exam", "score": {"possible": 30}}]},
        ("GET", "/v2/courses/_1_1/gradebook/columns/_a_1/users"): {"results": [
            {"userId": "_9_1", "displayGrade": {"score": 8}}]},
        ("GET", "/v2/courses/_1_1/gradebook/columns/_b_1/users"): {"results": []},
    })
    monkeypatch.setattr(client, "_request", req)
    out = run(server.bb_export_gradebook_csv("_1_1", str(tmp_path / "g.csv")))
    rows = list(csv.reader(open(tmp_path / "g.csv", newline="")))
    assert rows[0] == ["studentId", "name", "email", "HW1 (10)", "Exam (30)"]
    assert rows[1] == ["123", "Ada Lovelace", "ada@icatt.it", "8", ""]
    assert out == {"path": str(tmp_path / "g.csv"), "students": 1, "columns": 2}


# --- exam shortcut -------------------------------------------------------------

def test_prepare_exam_creates_shell_and_writes_file(monkeypatch, writes, tmp_path):
    req, calls = fake_api({
        ("POST", "/v1/courses/_1_1/contents/_f_1/children"): {
            "id": "_c_1", "title": "Exam", "availability": {"available": "No"},
            "contentHandler": {"id": server.TEST_HANDLER, "assessmentId": "_a_1", "gradeColumnId": "_g_1"}},
    })
    monkeypatch.setattr(client, "_request", req)
    qs = [{"type": "TF", "text": "OLS is linear", "correct": True}]
    out = run(server.bb_prepare_exam("_1_1", "Exam", qs, parent_id="_f_1", path=str(tmp_path / "exam.txt")))
    assert out["test"]["assessment_id"] == "_a_1"
    assert out["file"]["questions"] == 1
    assert (tmp_path / "exam.txt").read_text() == "TF\tOLS is linear\ttrue\n"
    assert "Upload Questions" in out["next"]


def test_prepare_exam_validates_questions_before_touching_blackboard(monkeypatch, writes, tmp_path):
    req, calls = fake_api({})
    monkeypatch.setattr(client, "_request", req)
    with pytest.raises(client.BlackboardError, match="exactly one correct"):
        run(server.bb_prepare_exam("_1_1", "Exam", [{"type": "MC", "text": "q", "answers": [
            {"text": "a", "correct": False}]}], path=str(tmp_path / "e.txt")))
    assert calls == []


# --- upload is multipart ---------------------------------------------------------

def test_upload_file_sends_multipart_form(monkeypatch, writes, tmp_path):
    f = tmp_path / "notes.pdf"
    f.write_bytes(b"%PDF-1.4")
    req, calls = fake_api({
        ("POST", "/v1/uploads"): {"id": "up_1"},
        ("POST", "/v1/courses/_1_1/contents/_f_1/children"): {
            "id": "_c_1", "title": "notes.pdf", "contentHandler": {"id": "resource/x-bb-file"},
            "availability": {"available": "No"}},
    })
    monkeypatch.setattr(client, "_request", req)
    run(server.bb_upload_file("_1_1", "_f_1", str(f)))
    up = calls[0]
    assert up[3] is None and "content" not in up[4]
    assert up[4]["files"] == {"file": ("notes.pdf", b"%PDF-1.4")}
    assert calls[1][3]["contentHandler"]["file"] == {"uploadId": "up_1", "fileName": "notes.pdf"}
