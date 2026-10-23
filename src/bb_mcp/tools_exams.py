"""Tools that build exams from question pools, and look the API up in its own swagger.

No network, no write switch: pools and exams are local files; the upload is
a click in Ultra (Test > + > Upload Questions), because the public API does
not write questions (see README and bb_add_question).
"""
from __future__ import annotations

import json
import pathlib
import re
from typing import Any

from . import exams
from .client import BlackboardError
from .server import mcp

DOCS = pathlib.Path(__file__).resolve().parent.parent.parent / "docs"


@mcp.tool()
async def bb_pool_check(path: str) -> dict:
    """Validate a question pool (JSON) and count it by type, difficulty and topic.

    Pool format: see bb_mcp.exams. Every question is run through the Upload
    Questions writer, so what passes here uploads.
    """
    return exams.check_pool(exams.load_pool(path))


@mcp.tool()
async def bb_build_exam(
    name: str,
    out_dir: str,
    pools: list[dict],
    mix: dict[str, int] | None = None,
    seed: int = 1,
    exclude_manifests: list[str] | None = None,
    ids: list[str] | None = None,
    points: float = 1.5,
    title: str = "",
    subtitle: str = "",
    note: str = "",
    difficulty_mix: dict[str, dict[str, int]] | None = None,
) -> dict:
    """Build an exam from pools: the Upload Questions file, the key (.qmd) and a manifest.

    `pools`: [{"path": "pool-module-1.json", "share": 1}] — a midterm; the full
    exam is two pools at share 0.5 each. `mix`: how many of each type, e.g.
    {"MC": 8, "TF": 4, "ESS": 8}; each count is split across the pools by
    share and drawn at random with `seed`, closed questions first in the paper.
    `exclude_manifests`: manifests of earlier exams (the mock, the midterms):
    their ids are never drawn again. `difficulty_mix`, optional, per type:
    {"ESS": {"medium": 5, "challenging": 3}}. `ids` instead of a draw: exactly
    those questions, in that order (a paper fixed by hand).

    `points` is per question and must be passed on purpose: this course's
    intermediates are 15 questions at 2 points in 60 minutes, the full exam 20
    at 2 in 60. Points do not travel in the upload file: after the upload, set
    them in Ultra or with bb_set_question_points (bb_test_fill sets them). The key is rendered with
    `quarto render <name>-key.qmd --to typst` outside any website project.
    """
    if not pools:
        raise BlackboardError("give at least one pool")
    loaded = [exams.load_pool(p["path"]) for p in pools]
    shares = [float(p.get("share", 1.0)) for p in pools]
    for pool in loaded:
        errs = exams.check_pool(pool)["errors"]
        if errs:
            raise BlackboardError(f"{pool['name']}: fix the pool first: " + "; ".join(errs[:5]))
    if not ids and not mix:
        raise BlackboardError("give mix (how many of each type) or ids")
    exclude = exams.ids_of_manifests(exclude_manifests or [])
    return exams.build_exam(name, out_dir, loaded, shares, mix or {}, seed, exclude, points,
                            title or name, subtitle, note, difficulty_mix, ids)


@mcp.tool()
async def bb_api_doc(query: str, max_results: int = 8) -> dict:
    """Search the Learn REST API swagger kept in docs/ (paths, summaries, descriptions, schemas).

    The newest docs/learn-swagger-<version>.json is used; refresh it with
    scripts/fetch_swagger.py when the server's /v1/system/version moves.
    """
    files = sorted(DOCS.glob("learn-swagger-*.json"))
    if not files:
        raise BlackboardError("no docs/learn-swagger-*.json; run scripts/fetch_swagger.py")
    sw = json.loads(files[-1].read_text(encoding="utf-8"))
    rx = re.compile(re.escape(query), re.I)
    hits: list[dict[str, Any]] = []
    for path, methods in sw.get("paths", {}).items():
        for method, op in methods.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            blob = " ".join([path, op.get("summary", ""), op.get("description", ""), json.dumps(op.get("parameters", []))])
            if rx.search(blob):
                hits.append({"method": method.upper(), "path": path, "summary": op.get("summary", ""),
                             "description": (op.get("description") or "")[:400]})
    defs = {n: d for n, d in sw.get("definitions", {}).items() if rx.search(n) or rx.search(json.dumps(d))}
    return {"swagger": files[-1].name, "version": sw.get("info", {}).get("version"), "paths": len(sw.get("paths", {})),
            "hits": hits[:max_results], "definitions": list(defs)[:max_results]}
