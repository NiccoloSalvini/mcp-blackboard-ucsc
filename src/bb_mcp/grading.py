"""Marking help for open questions: what a student's answer contains, against the model answer.

Nothing here decides a mark. It reads the text, lists the key numbers it found
and missed, the R functions it used, whether there is a sentence at all, and
proposes points on the question's scale with the reasons; the lecturer (or
the session) confirms or changes them before anything is written.
"""
from __future__ import annotations

import html
import re
from typing import Any

_NUM = re.compile(r"(?<![\w.])[-+]?\d+(?:[.,]\d+)?(?:e-?\d+)?%?(?![\w])")
_FUN = re.compile(r"(?<![\w.])([A-Za-z][\w.]*)\(")
_TAG = re.compile(r"<[^>]+>")
_NOISE_FUN = {"c", "data.frame", "library", "print", "round", "head", "str", "names", "nrow", "ncol", "length", "paste", "cat", "sum", "mean", "exp", "log", "sqrt", "seq", "rep", "factor", "subset", "list", "as.numeric", "as.factor", "table", "I"}


def plain(s: str | None) -> str:
    """HTML to text: tags out, entities decoded, whitespace folded."""
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", s or ""))).strip()


def numbers(text: str) -> list[float]:
    out = []
    for m in _NUM.findall(text or ""):
        m = m.rstrip("%").replace(",", ".")
        try:
            out.append(float(m))
        except ValueError:
            pass
    return out


def key_numbers(model: str) -> list[float]:
    """The numbers worth checking in a model answer: decimals, and integers from 20 up."""
    out: list[float] = []
    for x in numbers(model):
        if not (float(x).is_integer() and abs(x) < 20) and abs(x) not in [abs(y) for y in out]:
            out.append(x)
    return out


def functions(text: str) -> list[str]:
    seen, out = set(), []
    for f in _FUN.findall(text or ""):
        if f not in seen and f not in _NOISE_FUN:
            seen.add(f)
            out.append(f)
    return out


def close(a: float, b: float, rel: float = 0.02, abs_tol: float = 0.05) -> bool:
    """Same number within 2% (sign ignored: a student may drop a minus or state the gap the other way)."""
    a, b = abs(a), abs(b)
    return abs(a - b) <= max(abs_tol, rel * b)


def sentence_words(text: str) -> int:
    """Words in the prose part: code-looking tokens removed."""
    prose = re.sub(r"[\w.$]+\s*\([^)]*\)", " ", text)       # calls
    prose = re.sub(r"[<>=~|&%^*/+-]{1,3}|[\d.]+", " ", prose)  # operators and numbers
    return len([w for w in re.findall(r"[A-Za-z]{3,}", prose)])


_AI_PATTERNS = [
    (r"\$[^$\n]{1,40}\\%?\$|\\(?:times|frac|approx|text|beta|hat)\b", "LaTeX in a typed answer"),
    (r"(?m)^\s*(?:#{1,4} |\*\*[^*]+\*\*:?|- \*\*)", "markdown headings or bold labels"),
    (r"\b(?:taught in your (?:lectures?|course)|your (?:course|lecture|instructor)|as (?:you|your professor) (?:saw|mentioned))", "addresses the student as 'you' / 'your lectures'"),
    (r"\b(?:in the standard [\w ]+ dataset|depending on (?:the )?(?:exact )?(?:dataset|version|threshold|preprocessing))", "hedges about the dataset or version"),
    (r"\b(?:approximately|roughly) \$?\d", "approximate numbers where R prints exact ones"),
    (r"\b(?:Let'?s|Here(?:'s| is) (?:the|a) (?:complete|step-by-step)|I hope this helps|In summary,|Key takeaways?)\b", "chat-assistant phrasing"),
]


def ai_signals(answer: str, model: str = "", found: list | None = None, keys: list | None = None) -> list[str]:
    """Signs that an answer was pasted from an AI tool. Evidence for a person to weigh, never a verdict.

    The strongest sign is not in the wording: numbers presented as R output
    that the data do not give. That one is reported when the answer quotes
    several numbers and almost none of the model's key numbers.
    """
    raw = html.unescape(answer or "")
    out = [why for pat, why in _AI_PATTERNS if re.search(pat, raw, re.I)]
    nums = numbers(plain(answer))
    if keys and len(nums) >= 4 and found is not None and len(found) <= max(0, len(keys) // 4):
        out.append(f"quotes {len(nums)} numbers but only {len(found)} of {len(keys)} match the data")
    if len(plain(answer).split()) > 140 and not functions(plain(answer)):
        out.append("long polished prose with no R code")
    return out


def assess(answer: str, model: str, points_possible: float) -> dict[str, Any]:
    """What the answer has, and a proposal on the question's scale (steps of a quarter)."""
    text = plain(answer)
    keys = key_numbers(model)
    got = numbers(text)
    found = [k for k in keys if any(close(g, k) for g in got)]
    fun_model = functions(model)
    fun_got = [f for f in fun_model if f in text]
    words = sentence_words(text)
    step = points_possible / 4
    enough = bool(found) and (len(found) >= min(2, len(keys)) or len(found) * 2 >= len(keys))
    if not text:
        share, why = 0.0, "blank"
    elif enough and fun_got and words >= 8:
        share, why = 1.0, "numbers, code and a sentence"
    elif enough and fun_got:
        share, why = 0.75, "numbers and code, little or no sentence"
    elif fun_got or (found and words >= 8):
        share, why = 0.5, "the right function, or the right numbers with a sentence, not both"
    elif words >= 5 or functions(text) or got:
        share, why = 0.25, "a relevant attempt"
    else:
        share, why = 0.0, "nothing usable"
    proposed = round(share * points_possible / step) * step
    return {"proposed": proposed, "of": points_possible, "why": why,
            "ai_signals": ai_signals(answer, model, found, keys),
            "numbers_found": found, "numbers_missing": [k for k in keys if k not in found],
            "functions_used": fun_got, "functions_missing": [f for f in fun_model if f not in fun_got],
            "sentence_words": words, "answer_words": len(text.split())}
