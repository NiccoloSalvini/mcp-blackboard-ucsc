"""A test as the tab-delimited file Ultra accepts under Test > Upload Questions.

The public API cannot write question content on Ultra: questions come back as
opaque blocks. What Ultra does accept is this file — one question per line, no
header, at most 250 rows, answer markers in lowercase English. Points do not
travel in it: every question lands at 0 and gets its value after upload.
"""

from __future__ import annotations

import pathlib
import re
from typing import Any

from .client import BlackboardError

MAX_ROWS = 250
MAX_ANSWERS = 100


def _clean(text: Any) -> str:
    """One field: no tabs or newlines, or the row splits in the wrong place."""
    return re.sub(r"[\t\r\n]+", " ", str(text)).strip()


def question_line(q: dict[str, Any], n: int) -> str:
    kind = str(q.get("type", "")).upper()
    text = _clean(q.get("text", ""))
    if not text:
        raise BlackboardError(f"question {n}: empty text")
    answers = q.get("answers") or []

    if kind in ("MC", "MA"):
        if not answers:
            raise BlackboardError(f"question {n} ({kind}): needs answers")
        if len(answers) > MAX_ANSWERS:
            raise BlackboardError(f"question {n}: at most {MAX_ANSWERS} answers")
        correct = sum(1 for a in answers if a.get("correct"))
        if kind == "MC" and correct != 1:
            raise BlackboardError(f"question {n} (MC): exactly one correct answer, got {correct}")
        if kind == "MA" and correct < 1:
            raise BlackboardError(f"question {n} (MA): at least one correct answer")
        cells = [kind, text]
        for a in answers:
            cells += [_clean(a.get("text", "")), "correct" if a.get("correct") else "incorrect"]
        return "\t".join(cells)

    if kind == "TF":
        if "correct" not in q:
            raise BlackboardError(f"question {n} (TF): set correct: true|false")
        return "\t".join(["TF", text, "true" if q["correct"] else "false"])

    if kind == "ESS":
        cells = ["ESS", text]
        if q.get("example"):
            cells.append(_clean(q["example"]))
        return "\t".join(cells)

    if kind == "NUM":
        if "answer" not in q:
            raise BlackboardError(f"question {n} (NUM): needs answer")
        cells = ["NUM", text, str(q["answer"])]
        if q.get("tolerance") is not None:
            cells.append(str(q["tolerance"]))
        return "\t".join(cells)

    if kind == "FIB":
        if not answers:
            raise BlackboardError(f"question {n} (FIB): needs accepted answers")
        return "\t".join(["FIB", text] + [_clean(a if isinstance(a, str) else a.get("text", "")) for a in answers])

    raise BlackboardError(f"question {n}: unknown type {kind!r}; use MC, MA, TF, ESS, NUM or FIB")


def write_test(questions: list[dict[str, Any]], path: str | pathlib.Path) -> dict[str, Any]:
    if not questions:
        raise BlackboardError("no questions")
    if len(questions) > MAX_ROWS:
        raise BlackboardError(f"Blackboard takes at most {MAX_ROWS} questions per file; got {len(questions)}. Split it.")
    lines = [question_line(q, i + 1) for i, q in enumerate(questions)]
    out = pathlib.Path(path).expanduser()
    if out.suffix.lower() != ".txt":
        out = out.with_suffix(".txt")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    by_type: dict[str, int] = {}
    for ln in lines:
        kind = ln.split("\t", 1)[0]
        by_type[kind] = by_type.get(kind, 0) + 1
    return {
        "path": str(out),
        "questions": len(lines),
        "by_type": by_type,
        "points": "NOT in the file — set them in the test after upload; all arrive at 0",
        "next": "Ultra: open the test > + > Upload Questions > this file",
    }
