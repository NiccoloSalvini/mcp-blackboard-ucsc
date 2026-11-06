"""Tools on Ultra's private API: question banks from pools, tests filled from a manifest (see bb_mcp.ultra).

They need a signed-in browser: start Chrome with
`--remote-debugging-port=9223 --user-data-dir=<a folder>`, sign in to
Blackboard there, then bb_ultra_connect. Writes obey BB_ALLOW_WRITES like
every other write here.
"""
from __future__ import annotations

from collections import Counter

from . import client as bb
from . import exams, grading, ultra
from .client import BlackboardError
from .server import mcp, _students


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
async def bb_test_settings(course_id: str, content_id: str, attempts: int | None = None,
                           show_after_submission: list[str] | None = None,
                           auto_post_grades: bool | None = None) -> dict:
    """Read a test's settings, and change attempts, results release and grade posting.

    `content_id` is the test link's content id (bb_list_assessments). With no
    other argument it only reads. `attempts`: 0 for unlimited, or a number.
    `show_after_submission`: what students see as soon as they submit, from
    SCORE, USER_ANSWERS, CORRECT_ANSWERS, INCORRECT_ANSWERS, ALL_ANSWERS,
    INSTRUCTOR_FEEDBACK; it replaces every later release ("after the attempt
    is graded" and the rest), so a practice test shows everything at once.
    `auto_post_grades`: post grades automatically. The time limit is read, not
    written (its shape is not recorded); a test without one shows `None`.
    Visibility stays with bb_set_availability and bb_release_by_date.
    """
    if attempts is None and show_after_submission is None and auto_post_grades is None:
        return await ultra.test_settings(course_id, content_id)
    _writes()
    feedback = None if show_after_submission is None else {"as": [o.upper() for o in show_after_submission]}
    return await ultra.set_test_settings(course_id, content_id, attempts, feedback, auto_post_grades)


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


async def _add_from_pool(course_id: str, assessment_id: str, pool_path: str, ids: list[str] | None, points: float,
                         feedback: bool = False) -> dict:
    pool = exams.load_pool(pool_path)
    by_id = {str(q.get("id")): q for q in pool["questions"]}
    # the manifest's order is the paper's order; without ids, the pool's own
    qs = [by_id[i] for i in ids if i in by_id] if ids else list(pool["questions"])
    missing = [i for i in (ids or []) if i not in by_id]
    done, failed = [], [{"id": i, "error": "not in the pool"} for i in missing]
    for q in qs:
        try:
            r = await ultra.add_question(course_id, assessment_id, q, points, feedback)
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
                       replace: bool = False, feedback: bool = False) -> dict:
    """Put a fixed paper into a test: the questions of a `bb_build_exam` manifest, in its order.

    The manifest names its pool files (paths relative to the manifest's
    directory, as `bb_build_exam` writes them) and its points per question;
    `points` overrides. `replace` empties the test first. The test's own
    settings (time limit, attempts, visibility) are not touched: set them in
    the interface, then make it visible when the exam starts.
    `feedback` sends each question's `why` as its automatic feedback, shown
    to right and wrong answers alike: for a practice test, whose results show
    at submission. Never for an exam: the banks are drawn from again.
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
        results.append({"pool": pool["name"], **await _add_from_pool(course_id, test_id, str(ppath), ids, pts, feedback)})
    return {"test": test_id, "points_each": pts, "added": sum(r["added"] for r in results),
            "failed": [f for r in results for f in r["failed"]], "pools": results,
            "questions_now": len(await ultra.list_questions(course_id, test_id))}


@mcp.tool()
async def bb_banks_from_pool(course_id: str, pool_path: str, by: str = "topic", prefix: str = "",
                             points: float = 1.0, only: list[str] | None = None,
                             exclude_manifests: list[str] | None = None) -> dict:
    """One question bank per topic (or per any field `by`) from a pool file.

    Bank title = `prefix` + the field's value, e.g. "M1 · hypothesis testing".
    `only` restricts to those values; `exclude_manifests` (paths of
    `bb_build_exam` manifests, e.g. the mock's) keeps out every question that
    has already been sat. A bank whose title already exists in the course is
    skipped, so the call can be repeated after a failure.
    """
    _writes()
    pool = exams.load_pool(pool_path)
    seen = exams.ids_of_manifests(exclude_manifests or [])
    existing = {b["title"] for b in await ultra.list_banks(course_id)}
    groups: dict[str, list[dict]] = {}
    for q in pool["questions"]:
        key = str(q.get(by) or "untagged")
        if only and key not in only:
            continue
        if str(q.get("id")) in seen:
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
    return {"pool": pool["name"], "excluded": len(seen), "banks": out}


@mcp.tool()
async def bb_test_pools(course_id: str, test_id: str, pool_path: str, groups: list[dict], points: float,
                        exclude_manifests: list[str] | None = None, bank_prefix: str = "",
                        replace: bool = False) -> dict:
    """Question pools in a test, so every student gets a different paper of the same shape.

    `groups` is the shape, in paper order: [{"type": "MC", "difficulty": "easy",
    "choose": 4}, …]. Each group becomes one pool drawing `choose` questions at
    random from the questions of that type and difficulty, across every bank
    whose title starts with `bank_prefix`. Bank questions are matched to the pool
    file by their text; questions of `exclude_manifests` (the mock, the practice
    paper) are left out of every pool. `points` is per question. `replace`
    empties the test first.
    """
    _writes()
    pool = exams.load_pool(pool_path)
    seen = exams.ids_of_manifests(exclude_manifests or [])
    by_text = {grading.plain(q["text"]): q for q in pool["questions"]}
    banks = [b for b in await ultra.list_banks(course_id) if (b.get("title") or "").startswith(bank_prefix)]
    found: list[tuple[str, str, dict]] = []
    for b in banks:
        for bq in await ultra.list_questions(course_id, b["id"]):
            src = by_text.get(grading.plain(bq["text"]))
            if src and str(src.get("id")) not in seen:
                found.append((b["id"], bq["id"], src))
    if replace:
        await bb_assessment_clear(course_id, test_id)
    after, out = "start", []
    for g in groups:
        kind, level, k = str(g["type"]).upper(), g.get("difficulty"), int(g["choose"])
        hits = [(bank, qid) for bank, qid, src in found
                if str(src.get("type", "")).upper() == kind and (level is None or src.get("difficulty") == level)]
        if len(hits) < k:
            out.append({**g, "available": len(hits), "error": "fewer questions than the draw"})
            continue
        block = await ultra.add_question_block(course_id, test_id, [q for _, q in hits], sorted({b for b, _ in hits}),
                                               k, points, after)
        after = block["id"]
        out.append({**g, **block})
    return {"test": test_id, "matched": len(found), "pools": out,
            "questions_per_student": sum(p.get("choose") or 0 for p in out if "error" not in p),
            "points_total": sum((p.get("choose") or 0) * points for p in out if "error" not in p)}


# ----------------------------------------------------------------------------- marking a test
def _pool_index(pool_path: str | None) -> dict[str, dict]:
    if not pool_path:
        return {}
    pool = exams.load_pool(pool_path)
    return {grading.plain(q["text"]): q for q in pool["questions"]}


@mcp.tool()
async def bb_test_answers(course_id: str, column_id: str, only_open: bool = True, pool_path: str | None = None,
                          attempt_ids: list[str] | None = None) -> dict:
    """Every submission of a test with what each student answered, question by question.

    Closed questions carry Blackboard's own points; open ones carry the
    student's text, the model answer, the points so far and, with `pool_path`,
    the pool's marking notes and a *proposal* from bb_mcp.grading (numbers
    found, functions used, sentence or not). `only_open` leaves the closed
    questions out and reports their total instead. Read-only.
    """
    students = {s["userId"]: s for s in await _students(course_id)}
    attempts = await bb._paged(f"/v2/courses/{course_id}/gradebook/columns/{column_id}/attempts")
    if attempt_ids:
        attempts = [a for a in attempts if a.get("id") in set(attempt_ids)]
    pool = _pool_index(pool_path)
    out = []
    for a in attempts:
        rows = await ultra.attempt_answers(course_id, a["id"])
        closed = [r for r in rows if r["type"] != "essay"]
        opens = [r for r in rows if r["type"] == "essay"]
        for r in opens:
            src = pool.get(grading.plain(r["question"]))
            if src:
                r["pool_id"] = src.get("id")
                r["marking"] = src.get("marking") or src.get("partial")
            if r["model_answer"]:
                r["proposal"] = grading.assess(r["answer"], r["model_answer"], float(r["points_possible"] or 0))
        s = students.get(a.get("userId"), {})
        out.append({"attempt_id": a["id"], "user_id": a.get("userId"), "name": s.get("name"), "status": a.get("status"),
                    "closed_points": sum((r["points"] or 0) for r in closed), "closed_correct": sum(1 for r in closed if r["correct"]),
                    "closed_total": len(closed), "open_points": sum((r["points"] or 0) for r in opens if r["points"] is not None),
                    "open_unmarked": sum(1 for r in opens if r["points"] is None),
                    "questions": opens if only_open else rows})
    return {"column": column_id, "attempts": len(out), "rows": out}


@mcp.tool()
async def bb_grade_answers(course_id: str, grades: list[dict]) -> dict:
    """Write marks on open answers: [{"attempt_id", "question_attempt_id", "points", "feedback"?}, …].

    `points` None clears a mark. Nothing is posted to the student here: when
    every open answer of an attempt has its points, post the attempt (the
    column's "Post" in Ultra, or bb_post_attempt). Returns each write as
    Blackboard now holds it, and the failures with their reason.
    """
    _writes()
    done, failed = [], []
    for g in grades:
        try:
            r = await ultra.grade_answer(course_id, g["attempt_id"], g["question_attempt_id"], g.get("points"), g.get("feedback", ""))
            done.append({"attempt_id": g["attempt_id"], **r})
        except (BlackboardError, KeyError) as e:
            failed.append({**g, "error": str(e)[:200]})
    return {"written": len(done), "failed": failed, "grades": done}


@mcp.tool()
async def bb_question_answers(course_id: str, assessment_id: str, question_id: str, pool_path: str | None = None) -> dict:
    """One question, every student's answer: the way to mark an open question across the class in one pass.

    With `pool_path` the model answer, the marking notes and a proposal per answer come along.
    """
    rows = await ultra.question_answers(course_id, assessment_id, question_id)
    students = {s["userId"]: s for s in await _students(course_id)}
    pool = _pool_index(pool_path)
    model = next((r["model_answer"] for r in rows if r.get("model_answer")), None)
    src = pool.get(grading.plain(rows[0]["question"])) if rows else None
    for r in rows:
        r["name"] = students.get(r.get("user_id"), {}).get("name")
        if r["type"] == "essay" and model:
            r["proposal"] = grading.assess(r["answer"], model, float(r["points_possible"] or 0))
    return {"question_id": question_id, "question": rows[0]["question"] if rows else None, "model_answer": model,
            "marking": (src or {}).get("marking") or (src or {}).get("partial"), "answers": rows}
