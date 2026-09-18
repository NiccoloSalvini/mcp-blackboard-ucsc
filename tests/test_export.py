import pytest

from bb_mcp import export
from bb_mcp.client import BlackboardError


def test_mc_line_has_marker_text_and_answer_pairs():
    q = {"type": "MC", "text": "2+2?", "answers": [{"text": "4", "correct": True}, {"text": "5"}]}
    assert export.question_line(q, 1) == "MC\t2+2?\t4\tcorrect\t5\tincorrect"


def test_mc_needs_exactly_one_correct():
    q = {"type": "MC", "text": "x", "answers": [{"text": "a"}, {"text": "b"}]}
    with pytest.raises(BlackboardError, match="exactly one correct"):
        export.question_line(q, 3)


def test_tabs_and_newlines_are_flattened():
    q = {"type": "ESS", "text": "line\tone\nline two"}
    assert export.question_line(q, 1) == "ESS\tline one line two"


def test_write_test_caps_at_250(tmp_path):
    qs = [{"type": "TF", "text": f"q{i}", "correct": True} for i in range(251)]
    with pytest.raises(BlackboardError, match="250"):
        export.write_test(qs, tmp_path / "t.txt")


def test_write_test_forces_txt_and_counts(tmp_path):
    qs = [{"type": "TF", "text": "a", "correct": True}, {"type": "ESS", "text": "b"}]
    out = export.write_test(qs, tmp_path / "exam.csv")
    assert out["path"].endswith("exam.txt")
    assert out["by_type"] == {"TF": 1, "ESS": 1}
    assert (tmp_path / "exam.txt").read_text() == "TF\ta\ttrue\nESS\tb\n"
