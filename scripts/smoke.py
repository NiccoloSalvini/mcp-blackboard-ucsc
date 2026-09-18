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
