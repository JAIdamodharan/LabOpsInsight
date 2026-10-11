import json
from pathlib import Path

import pytest

from eval.grading import extract_numbers, grade, number_matches
from eval.run_eval import report, run_one
from analyst.agent import Analyst
from tests.test_analyst import FakeClient, db, msg, text  # noqa: F401  (db is a fixture)

QS = json.loads(Path("eval/questions.json").read_text())


@pytest.mark.parametrize("answer,exp,ok", [
    ("Revenue was $1,420,207 in March.", 1420207, True),
    ("About $1.42M.", 1420207, True),
    ("1.42 million", 1420207, True),
    ("roughly 1.2M", 1420207, False),
    ("It dropped 5.9%.", -5.9, False),   # sign matters unless absolute
    ("42 tests", 42, True),
])
def test_number_matching(answer, exp, ok):
    assert number_matches(answer, exp) is ok


def test_absolute_sign_insensitive():
    assert number_matches("down 5.9%", -5.9, absolute=True)
    assert number_matches("-5.9%", 5.9, absolute=True)


def test_extract_ignores_words():
    assert extract_numbers("5 months and 42 tests") == [5.0, 42.0]


@pytest.mark.parametrize("q", QS, ids=[q["id"] for q in QS])
def test_every_question_is_well_formed_and_golden_sql_runs(q, db):
    checks = {"number_sql", "any_of_sql", "contains_all", "contains_any"} & q.keys()
    assert checks, "question has no grading criteria"
    for key in ("number_sql", "any_of_sql"):
        if key in q:
            assert db.run(q[key]).rows, f"golden sql returned nothing: {key}"


def test_golden_answer_passes_its_own_grader(db):
    q = next(q for q in QS if q["id"] == "q01")
    exp = int(db.run(q["number_sql"]).rows[0][0])
    assert grade(q, f"There are {exp:,} tests.", db)[0]
    assert not grade(q, "There are 12 tests.", db)[0]


def test_diagnosis_grader_requires_all_concepts(db):
    q = next(q for q in QS if q["id"] == "q18")
    assert grade(q, "HPLC-03 Assay results drifted high.", db)[0]
    assert not grade(q, "HPLC-03 had a problem.", db)[0]
    assert not grade(q, "Seasonality caused it.", db)[0]


def test_trap_questions_reject_confident_hallucination(db):
    q = next(q for q in QS if q["id"] == "q25")
    assert not grade(q, "The average patient age was 54 in stability studies.", db)[0]
    assert grade(q, "The database has no such column, so I cannot compute it.", db)[0]


def test_run_one_and_report(db):
    q = next(q for q in QS if q["id"] == "q01")
    exp = int(db.run(q["number_sql"]).rows[0][0])
    agent = Analyst(db, client=FakeClient([msg("end_turn", text(f"{exp} tests"))]))
    row = run_one(agent, q, db)
    assert row["passed"] and row["stop"] == "answered"
    bad = dict(row, id="x", passed=False, reason="wrong_number (expected ~1)")
    md = report([row, bad], "m", 3.0, 15.0)
    assert "50%" in md and "wrong_number" in md and "cost per correct" in md


def test_run_one_survives_api_exception(db):
    class Boom:
        class messages:
            @staticmethod
            def create(**kw): raise RuntimeError("api down")
    row = run_one(Analyst(db, client=Boom()), QS[0], db)
    assert not row["passed"] and row["reason"].startswith("exception")
