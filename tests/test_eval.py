import json
from types import SimpleNamespace as NS
from pathlib import Path

import pytest

from eval.grading import extract_numbers, grade, number_matches
from eval.run_eval import report, run_one
from analyst.agent import Analyst
from tests.test_analyst import FakeClient, db, msg, text  # noqa: F401  (db is a fixture)
from tests.test_server import path  # noqa: F401

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


# ---- the benchmark runner: live progress, incremental saving, graceful Ctrl+C ----
def _run_main(monkeypatch, tmp_path, path, ids, outcomes):
    import sys
    from eval import run_eval
    calls = iter(outcomes)

    def fake_run_one(agent, q, db):
        r = next(calls)
        if r == "interrupt":
            raise KeyboardInterrupt
        return dict(id=q["id"], difficulty=q["difficulty"], question=q["question"], passed=r, reason="ok" if r else "wrong_number (expected ~1)",
                    latency_s=1.0, steps=2, in_tok=10, out_tok=5, sql_calls=1, sql_errors=0, stop="answered", answer="x")
    monkeypatch.setattr(run_eval, "run_one", fake_run_one)
    monkeypatch.setattr(run_eval, "make_client", lambda *a, **k: (NS(model_used="fallback-model"), "default-model"))
    monkeypatch.setattr(sys, "argv", ["run_eval", "--db", path, "--ids", ids, "--out", str(tmp_path / "out"), "--provider", "groq"])
    run_eval.main()
    return tmp_path / "out"


def test_interrupted_benchmark_still_saves_a_report_of_what_finished(monkeypatch, tmp_path, path, capsys):
    out = _run_main(monkeypatch, tmp_path, path, "q01,q02,q03", [True, "interrupt"])
    files = sorted(p.name for p in out.iterdir())
    assert any(f.endswith(".md") for f in files) and any(f.endswith(".json") and "partial" not in f for f in files)
    assert not any("partial" in f for f in files)                                      # the temporary file is cleaned up
    md = next(p for p in out.iterdir() if p.suffix == ".md").read_text()
    assert "Partial run: 1 of 3" in md and "groq/fallback-model" in md and "(1/1)" in md
    txt = capsys.readouterr().out
    assert "[1/3] running q01" in txt and "[2/3] running q02" in txt and "Stopped after 1 of 3" in txt


def test_a_benchmark_stopped_before_any_question_finishes_does_not_crash(monkeypatch, tmp_path, path, capsys):
    out = _run_main(monkeypatch, tmp_path, path, "q01", ["interrupt"])
    assert "nothing to report" in capsys.readouterr().out and not [p for p in out.iterdir() if p.suffix == ".md"]


def test_completed_benchmark_reports_the_model_that_was_actually_used(monkeypatch, tmp_path, path):
    out = _run_main(monkeypatch, tmp_path, path, "q01,q02", [True, False])
    md = next(p for p in out.iterdir() if p.suffix == ".md").read_text()
    assert "groq/fallback-model" in md and "50%" in md and "Partial run" not in md
