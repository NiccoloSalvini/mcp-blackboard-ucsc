"""Tools on Ultra's private API: question banks from pools, tests filled from a manifest (see bb_mcp.ultra).

They need a signed-in browser: start Chrome with
`--remote-debugging-port=9223 --user-data-dir=<a folder>`, sign in to
Blackboard there, then bb_ultra_connect. Writes obey BB_ALLOW_WRITES like
every other write here.
"""
from __future__ import annotations

from collections import Counter

from . import client as bb
from . import exams, ultra
from .client import BlackboardError
from .server import mcp


def _writes():
    if not bb.ALLOW_WRITES:
        raise BlackboardError("Writes are disabled. Set BB_ALLOW_WRITES=1 to allow them.")


@mcp.tool()
async def bb_ultra_connect(cdp_endpoint: str = "http://127.0.0.1:9223") -> dict:
    """Take the Blackboard session from a signed-in Chrome (DevTools port) for the Ultra tools.

    Reads the cookies once through CDP; nothing is stored on disk. The session
    lasts as long as the browser's (BbRouter `timeout`, 3 hours here).
    """
    return ultra.connect(cdp_endpoint)


@mcp.tool()
async def bb_banks_list(course_id: str) -> list[dict]:
    """The course's question banks (Ultra: assessments of type Pool), with question counts."""
    return await ultra.list_banks(course_id)


@mcp.tool()
async def bb_bank_create(course_id: str, title: str) -> dict:
    """An empty question bank."""
    _writes()
    return await ultra.create_bank(course_id, title)


@mcp.tool()
async def bb_bank_delete(course_id: str, bank_id: str) -> dict:
    """Delete a question bank (Ultra refuses if a test's question pool uses it)."""
    _writes()
    return await ultra.delete_bank(course_id, bank_id)


@mcp.tool()
async def bb_assessment_questions(course_id: str, assessment_id: str) -> list[dict]:
    """The questions of a bank or a test as Ultra holds them: text, answers, correct flags, points.

    A question pool shows as type `questionBlock` with its draw size. This is
    the view to check an exam against its pool file before it opens.
    """
    return await ultra.list_questions(course_id, assessment_id)


@mcp.tool()
async def bb_assessment_clear(course_id: str, assessment_id: str, question_ids: list[str] | None = None) -> dict:
    """Delete questions from a bank or a test — all of them, or `question_ids`.

    Question pools and essays go too, which the public API refuses. A test is
    emptied this way before it is filled again; attempts already taken are not
    touched here, so do not run it on a test that has been sat.
    """
    _writes()
    rows = await ultra.list_questions(course_id, assessment_id)
    wanted = set(question_ids or [])
    deleted, failed = [], []
    for q in rows:
        if wanted and q["id"] not in wanted:
            continue
        try:
            await ultra.delete_question(course_id, assessment_id, q["id"])
            deleted.append(q["id"])
        except BlackboardError as e:
            failed.append({"id": q["id"], "error": str(e)[:200]})
    return {"assessment": assessment_id, "deleted": deleted, "failed": failed,
            "left": len(await ultra.list_questions(course_id, assessment_id))}


async def _add_from_pool(course_id: str, assessment_id: str, pool_path: str, ids: list[str] | None, points: float) -> dict:
    pool = exams.load_pool(pool_path)
    by_id = {str(q.get("id")): q for q in pool["questions"]}
    # the manifest's order is the paper's order; without ids, the pool's own
    qs = [by_id[i] for i in ids if i in by_id] if ids else list(pool["questions"])
    missing = [i for i in (ids or []) if i not in by_id]
    done, failed = [], [{"id": i, "error": "not in the pool"} for i in missing]
    for q in qs:
        try:
            r = await ultra.add_question(course_id, assessment_id, q, points)
            done.append({"id": q.get("id"), **r})
        except BlackboardError as e:
            failed.append({"id": q.get("id"), "error": str(e)[:300]})
    return {"assessment": assessment_id, "added": len(done), "failed": failed,
            "by_type": dict(Counter(d["type"] for d in done)), "questions": done}


@mcp.tool()
async def bb_bank_add_questions(course_id: str, bank_id: str, pool_path: str, ids: list[str] | None = None,
                                points: float = 1.0) -> dict:
    """Add questions from a pool file to a bank (or to a test: same route).

    `ids` limits to those questions, in that order; otherwise every MC, MA, TF
    and ESS question in the pool goes in. Returns what Ultra created, and the
    ones it refused with the reason, so a half-done bank shows.
    """
    _writes()
    return await _add_from_pool(course_id, bank_id, pool_path, ids, points)


@mcp.tool()
async def bb_test_fill(course_id: str, test_id: str, manifest_path: str, points: float | None = None,
                       replace: bool = False) -> dict:
    """Put a fixed paper into a test: the questions of a `bb_build_exam` manifest, in its order.

    The manifest names its pool files (paths relative to the manifest's
    directory, as `bb_build_exam` writes them) and its points per question;
    `points` overrides. `replace` empties the test first. The test's own
    settings (time limit, attempts, visibility) are not touched: set them in
    the interface, then make it visible when the exam starts.
    """
    _writes()
    import json as _json
    from pathlib import Path
    mpath = Path(manifest_path).expanduser()
    manifest = _json.loads(mpath.read_text(encoding="utf-8"))
    pts = float(points if points is not None else manifest.get("points", 1.0))
    if replace:
        await bb_assessment_clear(course_id, test_id)
    results = []
    for pool in manifest["pools"]:
        ids = [str(q["id"]) for q in manifest["questions"] if q.get("pool") == pool["name"]]
        if not ids:
            continue
        ppath = Path(pool["path"]).expanduser()
        if not ppath.is_absolute():
            for base in (mpath.parent, mpath.parent.parent, Path.cwd()):
                if (base / ppath).exists():
                    ppath = base / ppath
                    break
        results.append({"pool": pool["name"], **await _add_from_pool(course_id, test_id, str(ppath), ids, pts)})
    return {"test": test_id, "points_each": pts, "added": sum(r["added"] for r in results),
            "failed": [f for r in results for f in r["failed"]], "pools": results,
            "questions_now": len(await ultra.list_questions(course_id, test_id))}


@mcp.tool()
async def bb_banks_from_pool(course_id: str, pool_path: str, by: str = "topic", prefix: str = "",
                             points: float = 1.0, only: list[str] | None = None) -> dict:
    """One question bank per topic (or per any field `by`) from a pool file, questions tagged.

    Bank title = `prefix` + the field's value, e.g. "M1 · hypothesis testing".
    `only` restricts to those values. A bank whose title already exists in the
    course is skipped, so the call can be repeated after a failure.
    """
    _writes()
    pool = exams.load_pool(pool_path)
    existing = {b["title"] for b in await ultra.list_banks(course_id)}
    groups: dict[str, list[dict]] = {}
    for q in pool["questions"]:
        key = str(q.get(by) or "untagged")
        if only and key not in only:
            continue
        groups.setdefault(key, []).append(q)
    out = []
    for key, qs in groups.items():
        title = f"{prefix}{key}"
        if title in existing:
            out.append({"bank": title, "skipped": "already exists"})
            continue
        bank = await ultra.create_bank(course_id, title)
        done, failed = 0, []
        for q in qs:
            try:
                await ultra.add_question(course_id, bank["id"], q, points)
                done += 1
            except BlackboardError as e:
                failed.append({"id": q.get("id"), "error": str(e)[:200]})
        out.append({"bank": title, "id": bank["id"], "added": done, "failed": failed})
    return {"pool": pool["name"], "banks": out}
