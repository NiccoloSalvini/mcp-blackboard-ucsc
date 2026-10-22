import pytest

from bb_mcp import ultra
from bb_mcp.client import BlackboardError


def test_essay_json_carries_model_answer_and_tags():
    q = {"id": "C1", "type": "ESS", "topic": "tests", "difficulty": "medium", "text": "Q?", "example": "model"}
    j = ultra.question_json("_1_1", "_2_1", q, 1.5)
    assert j["questionType"] == "essay" and j["isAutoGraded"] is False
    assert j["answerText"]["rawText"] == "model" and j["points"] == 1.5
    assert j["topicTags"] == [] and j["levelOfDifficultyTags"] == [] and j["keywordTags"] == []


def test_true_false_json():
    j = ultra.question_json("_1_1", "_2_1", {"id": "B1", "type": "TF", "text": "T?", "correct": False}, 1.0)
    assert j["questionType"] == "eitherOr" and j["answer"] is False and j["answerType"] == "true_false"


def test_multiple_choice_json_marks_the_correct_answer():
    q = {"id": "A1", "type": "MC", "text": "M?", "answers": [{"text": "yes", "correct": True}, {"text": "no", "correct": False}]}
    j = ultra.question_json("_1_1", "_2_1", q, 1.0)
    assert j["questionType"] == "multiplechoice" and j["singleCorrectAnswer"] is True
    assert [a["correctAnswer"] for a in j["answers"]] == [True, False]
    assert j["answers"][0]["answerText"]["rawText"] == "<p>yes</p>" and j["answers"][0]["id"].startswith("new_")
    assert j["answersCount"] == 2 and j["answerSelectionLimit"] == 1


def test_multiple_answer_json_counts_the_correct_ones():
    q = {"id": "A2", "type": "MA", "text": "M?", "answers": [{"text": "a", "correct": True}, {"text": "b", "correct": True}, {"text": "c", "correct": False}]}
    j = ultra.question_json("_1_1", "_2_1", q, 1.0)
    assert j["questionType"] == "multipleanswer" and j["singleCorrectAnswer"] is False and j["answerSelectionLimit"] == 2


def test_unknown_type_is_refused():
    with pytest.raises(BlackboardError, match="not supported"):
        ultra.question_json("_1_1", "_2_1", {"id": "X", "type": "NUM", "text": "n", "answer": 1}, 1.0)


def test_client_without_session_is_refused():
    ultra._session.clear()
    with pytest.raises(BlackboardError, match="bb_ultra_connect"):
        ultra._client()
