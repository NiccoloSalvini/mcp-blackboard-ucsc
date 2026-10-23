"""Exams from question pools.

Ultra's public API cannot write questions (see README), so an exam is built
here as the Upload Questions file plus a key, from pools kept as JSON files
outside this repo (they are answer keys). One pool per module; a midterm
draws from its module's pool, the full exam draws from both, half and half.

Pool file::

    {"name": "Module 1", "points": 1.5,
     "questions": [
       {"id": "A1", "type": "MC", "topic": "hypothesis testing", "deck": 2,
        "difficulty": "easy", "text": "...",
        "answers": [{"text": "...", "correct": true}, {"text": "...", "correct": false}]},
       {"id": "B1", "type": "TF", ..., "correct": false, "why": "one line for the key"},
       {"id": "C1", "type": "ESS", ..., "example": "model answer", "partial": "how to split the points",
        "marking": "notes for the marker only — never sent to Blackboard, where `example` is the model answer"}
     ]}

`type` and the answer fields are exactly what bb_export_test takes (MC, MA,
TF, ESS, NUM, FIB). `difficulty` is easy | medium | challenging. Ids are
unique within a pool and, by convention, across pools.

An exam is a *manifest*: the ids drawn, where from, with what seed. The next
exam excludes the ids of earlier manifests, so a midterm never repeats the
mock and the full exam never repeats a midterm.
"""
from __future__ import annotations

import json
import pathlib
import random
from collections import Counter
from typing import Any

from . import export
from .client import BlackboardError

DIFFICULTIES = ("easy", "medium", "challenging")
KIND_NAMES = {"MC": "Multiple choice", "MA": "Multiple answer", "TF": "True or false",
              "ESS": "Open", "NUM": "Numeric", "FIB": "Fill in the blank"}


# ----------------------------------------------------------------------------- pools
def load_pool(path: str | pathlib.Path) -> dict[str, Any]:
    p = pathlib.Path(path).expanduser()
    if not p.exists():
        raise BlackboardError(f"pool not found: {p}")
    try:
        pool = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise BlackboardError(f"{p}: not valid JSON ({e})")
    if not isinstance(pool, dict) or not isinstance(pool.get("questions"), list):
        raise BlackboardError(f"{p}: expected an object with a 'questions' list")
    pool.setdefault("name", p.stem)
    pool["_path"] = str(p)
    return pool


def check_pool(pool: dict[str, Any]) -> dict[str, Any]:
    """Validate every question with the upload writer; count by type, difficulty, topic."""
    errors: list[str] = []
    seen: set[str] = set()
    for i, q in enumerate(pool["questions"], 1):
        qid = str(q.get("id", "")).strip()
        if not qid:
            errors.append(f"question {i}: no id")
        elif qid in seen:
            errors.append(f"question {i}: duplicate id {qid}")
        seen.add(qid)
        if q.get("difficulty") not in DIFFICULTIES:
            errors.append(f"{qid or i}: difficulty must be one of {DIFFICULTIES}")
        if not q.get("topic"):
            errors.append(f"{qid or i}: no topic")
        try:
            export.question_line(q, i)
        except BlackboardError as e:
            errors.append(str(e))
    qs = pool["questions"]
    return {
        "pool": pool["name"], "path": pool.get("_path"), "questions": len(qs),
        "by_type": dict(Counter(str(q.get("type", "")).upper() for q in qs)),
        "by_difficulty": dict(Counter(q.get("difficulty") for q in qs)),
        "by_topic": dict(Counter(q.get("topic") for q in qs)),
        "errors": errors,
    }


# ----------------------------------------------------------------------------- drawing
def _split(n: int, shares: list[float]) -> list[int]:
    """n items over pools in proportion to shares; largest remainder, so it sums to n."""
    total = sum(shares) or 1.0
    raw = [n * s / total for s in shares]
    base = [int(x) for x in raw]
    for i in sorted(range(len(raw)), key=lambda i: raw[i] - base[i], reverse=True)[: n - sum(base)]:
        base[i] += 1
    return base


def _pick(cands: list[dict], k: int, rng: random.Random, difficulty_mix: dict[str, int] | None) -> list[dict]:
    """k questions out of cands; with a difficulty mix, honour it as far as the pool allows."""
    if k > len(cands):
        raise BlackboardError(f"asked for {k}, only {len(cands)} available")
    if not difficulty_mix:
        return rng.sample(cands, k)
    chosen: list[dict] = []
    left = {d: list(v) for d, v in _group(cands).items()}
    for d, n in difficulty_mix.items():
        take = min(n, len(left.get(d, [])))
        picked = rng.sample(left[d], take) if take else []
        chosen += picked
        for q in picked:
            left[d].remove(q)
    rest = [q for v in left.values() for q in v]
    if len(chosen) < k:
        chosen += rng.sample(rest, k - len(chosen))
    return chosen[:k]


def _group(qs: list[dict]) -> dict[str, list[dict]]:
    g: dict[str, list[dict]] = {}
    for q in qs:
        g.setdefault(q.get("difficulty", "medium"), []).append(q)
    return g


def draw(pools: list[dict[str, Any]], shares: list[float], mix: dict[str, int], seed: int,
         exclude: set[str], difficulty_mix: dict[str, dict[str, int]] | None = None) -> list[dict]:
    """Draw `mix[type]` questions of each type, split across pools by share.

    difficulty_mix: per type, how many of each difficulty, e.g. {"ESS": {"medium": 5, "challenging": 3}}.
    Deterministic for a given seed and the same pools.
    """
    rng = random.Random(seed)
    drawn: list[dict] = []
    for kind, n in mix.items():
        kind = kind.upper()
        per_pool = _split(n, shares)
        for pool, k in zip(pools, per_pool):
            if k == 0:
                continue
            cands = [q for q in pool["questions"]
                     if str(q.get("type", "")).upper() == kind and str(q.get("id")) not in exclude]
            cands.sort(key=lambda q: str(q["id"]))          # order independent of file order
            try:
                picked = _pick(cands, k, rng, (difficulty_mix or {}).get(kind))
            except BlackboardError as e:
                raise BlackboardError(f"{pool['name']}, type {kind}: {e}")
            for q in picked:
                drawn.append({**q, "_pool": pool["name"]})
    return drawn


def order(drawn: list[dict], kinds_order: tuple[str, ...] = ("MC", "MA", "TF", "NUM", "FIB", "ESS")) -> list[dict]:
    """Closed questions first, open last; within a type easy → challenging; stable otherwise."""
    rank = {k: i for i, k in enumerate(kinds_order)}
    drank = {d: i for i, d in enumerate(DIFFICULTIES)}
    return sorted(drawn, key=lambda q: (rank.get(str(q.get("type")).upper(), 99),
                                        drank.get(q.get("difficulty"), 1)))


# ----------------------------------------------------------------------------- output
def key_markdown(title: str, subtitle: str, note: str, questions: list[dict], points: float) -> str:
    out = [f'---\ntitle: "{title}"\nsubtitle: "{subtitle}"\nformat:\n  typst:\n    toc: false\n'
           '    papersize: a4\n    margin:\n      x: 2.2cm\n      y: 2.4cm\n    fontsize: 10pt\n---\n', note, ""]
    for n, q in enumerate(questions, 1):
        kind = str(q.get("type", "")).upper()
        tag = f"{q.get('id')} · {q.get('_pool', '')} · {q.get('topic', '')} · {q.get('difficulty', '')}"
        out.append(f"### {n}. {KIND_NAMES.get(kind, kind)} · {points:g} points  [{tag}]\n")
        out.append(str(q.get("text", "")) + "\n")
        if kind in ("MC", "MA"):
            for a in q.get("answers", []):
                out.append(f"- {a.get('text')}" + ("  **✓**" if a.get("correct") else ""))
            out.append("")
        elif kind == "TF":
            out.append(f"**Key:** {'True' if q.get('correct') else 'False'}. {q.get('why', '')}\n")
        elif kind == "ESS":
            out.append(f"**Key:** {q.get('example', '')}\n")
            if q.get("marking"):
                out.append(f"*Marking:* {q['marking']}\n")
            if q.get("partial"):
                out.append(f"*Partial credit:* {q['partial']}\n")
        elif kind == "NUM":
            out.append(f"**Key:** {q.get('answer')} ± {q.get('tolerance', 0)}\n")
        elif kind == "FIB":
            out.append(f"**Key:** {', '.join(map(str, q.get('answers', [])))}\n")
    return "\n".join(out)


def build_exam(name: str, out_dir: str | pathlib.Path, pools: list[dict[str, Any]], shares: list[float],
               mix: dict[str, int], seed: int, exclude: set[str], points: float, title: str, subtitle: str,
               note: str, difficulty_mix: dict[str, dict[str, int]] | None = None,
               ids: list[str] | None = None) -> dict[str, Any]:
    """Draw, order, write the upload file, the key and the manifest. Returns paths and the summary.

    With `ids`, no draw: exactly those questions, in that order, looked up across the pools
    (a paper fixed by hand, or one already given, so that its manifest exists).
    """
    out = pathlib.Path(out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    if ids:
        index = {str(q["id"]): {**q, "_pool": pool["name"]} for pool in pools for q in pool["questions"]}
        missing = [i for i in ids if i not in index]
        if missing:
            raise BlackboardError(f"ids not in the pools: {missing}")
        drawn = [index[i] for i in ids]
        mix = dict(Counter(q["type"] for q in drawn))
    else:
        drawn = order(draw(pools, shares, mix, seed, exclude, difficulty_mix))
    upload = out / f"{name}-upload.txt"
    export.write_test(drawn, upload)
    key = out / f"{name}-key.qmd"
    key.write_text(key_markdown(title, subtitle, note, drawn, points), encoding="utf-8")
    manifest = {
        "name": name, "title": title, "seed": None if ids else seed, "points": points, "mix": mix,
        "pools": [{"name": p["name"], "path": p.get("_path"), "share": s} for p, s in zip(pools, shares)],
        "excluded": sorted(exclude),
        "questions": [{"id": q["id"], "pool": q["_pool"], "type": q["type"], "difficulty": q.get("difficulty"),
                       "topic": q.get("topic")} for q in drawn],
    }
    mpath = out / f"{name}.json"
    mpath.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    return {
        "questions": len(drawn), "points_each": points, "total": points * len(drawn),
        "by_pool": dict(Counter(q["_pool"] for q in drawn)),
        "by_type": dict(Counter(q["type"] for q in drawn)),
        "by_difficulty": dict(Counter(q.get("difficulty") for q in drawn)),
        "ids": [q["id"] for q in drawn],
        "upload": str(upload), "key": str(key), "manifest": str(mpath),
    }


def ids_of_manifests(paths: list[str]) -> set[str]:
    ids: set[str] = set()
    for p in paths:
        m = json.loads(pathlib.Path(p).expanduser().read_text(encoding="utf-8"))
        ids.update(str(q["id"]) for q in m.get("questions", []))
    return ids
