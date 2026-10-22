import json

import pytest

from bb_mcp import exams
from bb_mcp.client import BlackboardError


def pool(name, n_mc=6, n_tf=3, n_ess=5, prefix="P"):
    qs = []
    for i in range(n_mc):
        qs.append({"id": f"{prefix}A{i}", "type": "MC", "topic": "t", "difficulty": ["easy", "medium"][i % 2],
                   "text": f"mc {i}?", "answers": [{"text": "yes", "correct": True}, {"text": "no", "correct": False}]})
    for i in range(n_tf):
        qs.append({"id": f"{prefix}B{i}", "type": "TF", "topic": "t", "difficulty": "easy", "text": f"tf {i}", "correct": i % 2 == 0, "why": "because"})
    for i in range(n_ess):
        qs.append({"id": f"{prefix}C{i}", "type": "ESS", "topic": "t", "difficulty": "challenging" if i == 0 else "medium",
                   "text": f"open {i}", "example": "model", "partial": "halves"})
    return {"name": name, "questions": qs}


def test_check_pool_counts_and_flags_errors():
    p = pool("M1")
    p["questions"].append({"id": "PA0", "type": "MC", "topic": "t", "difficulty": "hard", "text": "dup", "answers": []})
    r = exams.check_pool(p)
    assert r["by_type"] == {"MC": 7, "TF": 3, "ESS": 5}
    assert any("duplicate id PA0" in e for e in r["errors"])
    assert any("difficulty" in e for e in r["errors"])
    assert any("needs answers" in e for e in r["errors"])


def test_draw_is_deterministic_and_respects_exclusions():
    p = pool("M1")
    a = exams.draw([p], [1.0], {"MC": 3, "ESS": 2}, seed=7, exclude=set())
    b = exams.draw([p], [1.0], {"MC": 3, "ESS": 2}, seed=7, exclude=set())
    assert [q["id"] for q in a] == [q["id"] for q in b]
    used = {q["id"] for q in a}
    c = exams.draw([p], [1.0], {"MC": 3, "ESS": 2}, seed=7, exclude=used)
    assert not used & {q["id"] for q in c}


def test_full_exam_takes_half_from_each_pool():
    p1, p2 = pool("M1", prefix="X"), pool("M2", prefix="Y")
    d = exams.draw([p1, p2], [0.5, 0.5], {"MC": 8, "TF": 2, "ESS": 4}, seed=3, exclude=set())
    by_pool = {}
    for q in d:
        by_pool[q["_pool"]] = by_pool.get(q["_pool"], 0) + 1
    assert by_pool == {"M1": 7, "M2": 7}


def test_difficulty_mix_is_honoured_when_possible():
    p = pool("M1")
    d = exams.draw([p], [1.0], {"ESS": 3}, seed=1, exclude=set(), difficulty_mix={"ESS": {"challenging": 1, "medium": 2}})
    assert sorted(q["difficulty"] for q in d) == ["challenging", "medium", "medium"]


def test_too_few_candidates_is_a_clear_error():
    with pytest.raises(BlackboardError, match="only 3 available"):
        exams.draw([pool("M1")], [1.0], {"TF": 4}, seed=1, exclude=set())


def test_build_exam_writes_upload_key_and_manifest(tmp_path):
    p = pool("M1"); p["_path"] = "m1.json"
    r = exams.build_exam("mock", tmp_path, [p], [1.0], {"MC": 2, "TF": 1, "ESS": 2}, 5, set(), 1.5, "Mock", "sub", "note")
    assert r["questions"] == 5 and r["total"] == 7.5
    lines = (tmp_path / "mock-upload.txt").read_text().splitlines()
    assert [ln.split("\t")[0] for ln in lines] == ["MC", "MC", "TF", "ESS", "ESS"]      # closed first, open last
    m = json.loads((tmp_path / "mock.json").read_text())
    assert [q["id"] for q in m["questions"]] == r["ids"]
    key = (tmp_path / "mock-key.qmd").read_text()
    assert "1.5 points" in key and "**✓**" in key and "*Partial credit:*" in key
    assert exams.ids_of_manifests([str(tmp_path / "mock.json")]) == set(r["ids"])


def test_build_exam_with_fixed_ids_keeps_the_order(tmp_path):
    p = pool("M1")
    r = exams.build_exam("fixed", tmp_path, [p], [1.0], {}, 1, set(), 2.0, "F", "", "", ids=["PC1", "PA0", "PB0"])
    assert r["ids"] == ["PC1", "PA0", "PB0"]
    with pytest.raises(BlackboardError, match="not in the pools"):
        exams.build_exam("bad", tmp_path, [p], [1.0], {}, 1, set(), 2.0, "F", "", "", ids=["ZZ"])
