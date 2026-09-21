"""Probe the unverified writes on a hidden scratch folder, then delete it.

BB_ALLOW_WRITES=1 PYTHONPATH=src .venv/bin/python scripts/probe_writes.py [course_id]

Everything is created hidden (students never see it) and removed at the end.
Prints OK/FAIL per route so README's table can be updated.
"""
import asyncio
import json
import pathlib
import sys
import tempfile

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
        # exam shell + question POST (expected to fail on Ultra: questions are opaque)
        test = await try_("create test", server.bb_create_assessment(COURSE, "zz-probe-test", parent_id=fid))
        if test:
            await try_("add question", server.bb_add_question(
                COURSE, test["assessment_id"], "MultipleChoice", "<p>2+2?</p>",
                [{"text": "4", "correct": True}, {"text": "5", "correct": False}]))
            await try_("list questions", server.bb_list_questions(COURSE, test["assessment_id"]))
            await try_("update column (points/due)", server.bb_update_gradebook_column(
                COURSE, test["grade_column_id"], points_possible=30, due="2026-12-31T22:59:00.000Z"))

        # homework: createAssignment in one POST
        hw = await try_("create assignment", server.bb_create_assignment(
            COURSE, "zz-probe-hw", "<p>Hand in a PDF.</p>", parent_id=fid,
            points_possible=10, due="2026-12-31T22:59:00.000Z", attempts_allowed=2))
        if hw:
            await try_("assignment column", bb._request(
                "GET", f"/v2/courses/{COURSE}/gradebook/columns/{hw['grade_column_id']}"))

        # file upload (multipart) + file item
        tmp = pathlib.Path(tempfile.mkdtemp()) / "zz-probe.txt"
        tmp.write_text("probe\n")
        await try_("upload file", server.bb_upload_file(COURSE, fid, str(tmp)))

        # second tool set, all on the hidden folder / hidden items
        doc = await try_("create doc for rules", server.bb_create_content(
            COURSE, "zz-probe-rules", "<p>rules</p>", parent_id=fid))
        if doc:
            await try_("release by date", server.bb_release_by_date(
                COURSE, doc["id"], start="2026-12-01T00:00:00.000Z", title="zz-date"))
            if test:
                await try_("release by grade", server.bb_release_by_grade(
                    COURSE, doc["id"], test["grade_column_id"], 0, 17.99, title="zz-grade"))
            await try_("list rules", server.bb_list_release_rules(COURSE, doc["id"]))
        if hw:
            me = await server.bb_whoami()
            students = await server.bb_list_students(COURSE, role="Student")
            if students:
                await try_("set exception (first student, +2 attempts)", server.bb_set_exception(
                    COURSE, hw["grade_column_id"], students[0]["userId"], attempts=3))
                await try_("get exception", server.bb_get_exception(
                    COURSE, hw["grade_column_id"], students[0]["userId"]))
                await try_("delete exception", server.bb_delete_exception(
                    COURSE, hw["grade_column_id"], students[0]["userId"]))
        rub = await try_("create rubric", server.bb_create_rubric(COURSE, "zz-probe-rubric", [
            {"criterion": "Code", "levels": [{"header": "Full", "points": 5}, {"header": "None", "points": 0}]},
            {"criterion": "Report", "levels": [{"header": "Full", "points": 5}, {"header": "None", "points": 0}]}]))
        if rub and hw:
            await try_("get rubric", server.bb_get_rubric(COURSE, rub["id"]))
            await try_("attach rubric", server.bb_attach_rubric(COURSE, rub["id"], hw["grade_column_id"]))
        meeting = await try_("create meeting", server.bb_create_meeting(
            COURSE, "zz-probe-lecture", "2026-12-01T08:00:00.000Z", "2026-12-01T10:00:00.000Z"))
        if meeting:
            await try_("mark attendance (nobody, no absents)", server.bb_mark_attendance(
                COURSE, meeting["id"], present=[], mark_absent=False))
            await try_("delete meeting", bb._request(
                "DELETE", f"/v1/courses/{COURSE}/meetings/{meeting['id']}", write=True))
        disc = await try_("create discussion (hidden)", server.bb_create_discussion(
            COURSE, "zz-probe-forum", "<p>probe</p>"))
        if disc:
            msg = await try_("post message", server.bb_post_discussion_message(COURSE, disc["id"], "<p>hi</p>"))
            await try_("read messages", server.bb_discussion_messages(COURSE, disc["id"]))
        cal = await try_("calendar item", server.bb_create_calendar_item(
            COURSE, "zz-probe-event", "2026-12-01T08:00:00.000Z", "2026-12-01T10:00:00.000Z"))
        if cal:
            await try_("delete calendar item", server.bb_delete_calendar_item(COURSE, cal["id"]))
        gs = await try_("group set (hidden)", server.bb_create_group_set(
            COURSE, "zz-probe-groups", [{"name": "zz-A", "members": []}, {"name": "zz-B", "members": []}]))
        if gs and hw:
            await try_("assign hw to groups", server.bb_assign_content_to_groups(
                COURSE, hw["content_id"], [g["id"] for g in gs["groups"]]))
        if gs:
            await try_("delete group set", bb._request(
                "DELETE", f"/v2/courses/{COURSE}/groups/sets/{gs['set_id']}", write=True))

        # announcement as draft, edit, delete — never reaches students
        ann = await try_("draft announcement", server.bb_post_announcement(
            COURSE, "zz-probe", "<p>probe</p>", draft=True))
        if ann:
            await try_("update announcement", server.bb_update_announcement(
                COURSE, ann["id"], title="zz-probe edited"))
            await try_("delete announcement", server.bb_delete_announcement(COURSE, ann["id"]))
    finally:
        await try_("delete folder", server.bb_delete_content(COURSE, fid, delete_grades=True))
        left = [c["title"] for c in await server.bb_list_contents(COURSE) if c["title"].startswith("zz")]
        cols = [c["name"] for c in await server.bb_list_gradebook_columns(COURSE) if "zz" in (c["name"] or "")]
        anns = [a["title"] for a in await server.bb_list_announcements(COURSE) if a["title"].startswith("zz")]
        rubs = [r["title"] for r in await server.bb_list_rubrics(COURSE) if (r["title"] or "").startswith("zz")]
        discs = [d["title"] for d in await server.bb_list_discussions(COURSE) if (d["title"] or "").startswith("zz")]
        grps = [g["name"] for g in await server.bb_list_groups(COURSE) if (g["name"] or "").startswith("zz")]
        mts = [m["title"] for m in await server.bb_list_meetings(COURSE) if (m["title"] or "").startswith("zz")]
        print("leftover contents:", left, "columns:", cols, "announcements:", anns)
        print("leftover rubrics:", rubs, "discussions:", discs, "groups:", grps, "meetings:", mts)
        print("NOTE: rubrics and discussions have no delete tool here; remove leftovers in Ultra if any.")


asyncio.run(main())
