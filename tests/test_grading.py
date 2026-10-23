from bb_mcp import grading


MODEL = "t.test(mpg ~ am, data = mtcars). Means 17.15 (automatic) vs 24.39 (manual); difference 7.2 mpg; 95% CI 3.2 to 11.3; p = 0.001. Manual cars do about 7 mpg more, at least 3."


def test_key_numbers_skip_trivial_integers():
    assert 17.15 in grading.key_numbers(MODEL) and 95 in grading.key_numbers(MODEL) and 1 not in grading.key_numbers(MODEL)


def test_full_answer_gets_full_points():
    a = "<p>t.test(mpg ~ am, data = mtcars) gives means 17.1 and 24.4, difference 7.24 with interval 3.2 to 11.3, p = 0.001. Manual cars use about seven mpg more and the interval excludes zero, so the difference is real.</p>"
    r = grading.assess(a, MODEL, 2)
    assert r["proposed"] == 2 and "t.test" in r["functions_used"] and 7.2 in r["numbers_found"]


def test_numbers_without_sentence():
    r = grading.assess("t.test(mpg ~ am, data = mtcars): 17.15 vs 24.39, p = 0.001", MODEL, 2)
    assert r["proposed"] == 1.5 and r["sentence_words"] < 8


def test_function_only_and_blank():
    assert grading.assess("t.test(mpg ~ am)", MODEL, 2)["proposed"] == 1
    assert grading.assess("<p></p>", MODEL, 2)["proposed"] == 0
    assert grading.assess("I would compare the two groups", MODEL, 1.5)["proposed"] == 0.375


def test_scale_follows_points_possible():
    r = grading.assess("t.test(mpg ~ am, data = mtcars): 17.15 vs 24.39, p = 0.001", MODEL, 1.5)
    assert r["proposed"] == 1.125 and r["of"] == 1.5
