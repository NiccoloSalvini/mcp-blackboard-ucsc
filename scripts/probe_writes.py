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
