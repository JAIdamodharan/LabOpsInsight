import json

import pytest
from fastapi.testclient import TestClient

from analyst.server import create_app
from tests.test_analyst import FakeClient, db, msg, text, tool  # noqa: F401


@pytest.fixture
def path(db):
    return str(db.con.execute("PRAGMA database_list").fetchall()[0][2])


def events(resp):
    return [json.loads(l[6:]) for l in resp.text.splitlines() if l.startswith("data: ")]


def test_index_and_config(path, tmp_path):
    c = TestClient(create_app(path, demo=True, artifacts_dir=str(tmp_path)))
    assert "LabOps Insight" in c.get("/").text
    assert c.get("/api/config").json()["demo"] is True


def test_stream_with_fake_client(path, tmp_path):
    fc = FakeClient([msg("tool_use", tool("1", "run_sql", sql="SELECT count(*) n FROM tests")),
                     msg("end_turn", text("done"))])
    c = TestClient(create_app(path, client=fc, artifacts_dir=str(tmp_path)))
    ev = events(c.get("/api/ask", params={"q": "how many?"}))
    assert [e["kind"] for e in ev] == ["tool_call", "tool_result", "answer", "done"]
    assert ev[-1]["sql_calls"] == 1 and ev[-1]["stop"] == "answered"


def test_demo_mode_end_to_end(path, tmp_path):
    c = TestClient(create_app(path, demo=True, sandbox="local", artifacts_dir=str(tmp_path)))
    ev = events(c.get("/api/ask", params={"q": "Why was the OOS rate on HPLC-03 so high in Mar 2025?"}))
    kinds = [e["kind"] for e in ev]
    assert kinds[-1] == "done" and "answer" in kinds
    ans = next(e for e in ev if e["kind"] == "answer")["text"]
    assert "HPLC-03" in ans and "Lab B" in ans and "calibration" in ans.lower()
    assert any(e.get("chart") for e in ev if e["kind"] == "tool_result")
    assert any(e["kind"] == "tool_call" and e["input"] == {} and e["name"] == "data_quality" for e in ev)
    assert not any(e.get("is_error") for e in ev if e["kind"] == "tool_result")


def test_demo_mode_declines_off_topic(path, tmp_path):
    c = TestClient(create_app(path, demo=True, artifacts_dir=str(tmp_path)))
    ev = events(c.get("/api/ask", params={"q": "What is the weather today?"}))
    assert "four scripted investigations" in next(e for e in ev if e["kind"] == "answer")["text"]


@pytest.mark.parametrize("q,needle", [
    ("Why did Related Substances on-time delivery drop in Feb 2025?", "LCMS-02"),
    ("Why did Documentation deviations increase in Apr 2025, and who raised them?", "January 2025"),
    ("Which records need data review?", "DQ-003"),
])
def test_demo_scenarios(path, tmp_path, q, needle):
    c = TestClient(create_app(path, demo=True, artifacts_dir=str(tmp_path)))
    ev = events(c.get("/api/ask", params={"q": q}))
    assert needle in next(e for e in ev if e["kind"] == "answer")["text"]
    assert not any(e.get("is_error") for e in ev if e["kind"] == "tool_result")


@pytest.mark.parametrize("q", ["", "   ", "x" * 501])
def test_rejects_bad_questions(path, tmp_path, q):
    c = TestClient(create_app(path, demo=True, artifacts_dir=str(tmp_path)))
    assert c.get("/api/ask", params={"q": q}).status_code == 400


def test_agent_exception_becomes_error_event(path, tmp_path):
    class Boom:
        class messages:
            @staticmethod
            def create(**kw): raise RuntimeError("api down")
    c = TestClient(create_app(path, client=Boom(), artifacts_dir=str(tmp_path)))
    ev = events(c.get("/api/ask", params={"q": "hi"}))
    assert ev[-1]["kind"] == "error" and "api down" in ev[-1]["message"]


def test_artifacts_path_traversal_blocked(path, tmp_path):
    (tmp_path / "ok.png").write_bytes(b"x")
    c = TestClient(create_app(path, demo=True, artifacts_dir=str(tmp_path / "sub")))
    assert c.get("/artifacts/../server.py").status_code in (404, 400)


@pytest.mark.parametrize("q", [
    "What happened with HPLC-01 in Mar 2025? Check its OOS rate and downtime.",
    "What happened with LCMS-01 in Feb 2025? Check its OOS rate and downtime.",
    "Why did Equipment deviations increase in Mar 2025, and who raised them?",
    "Why did instrument utilization change in Mar 2025 compared with Feb 2025?",
    "Why did tests completed change in Mar 2025 compared with Feb 2025?",
])
def test_demo_declines_questions_outside_its_scripted_scope(path, tmp_path, q):
    c = TestClient(create_app(path, demo=True, artifacts_dir=str(tmp_path)))
    ans = next(e for e in events(c.get("/api/ask", params={"q": q})) if e["kind"] == "answer")["text"]
    assert "four scripted investigations" in ans and "HPLC-03 in the" not in ans


@pytest.mark.parametrize("q,scope", [
    ("Why did oos rate change in Mar 2025 compared with Feb 2025?", "HPLC-03 in March 2025"),
    ("What happened with HPLC-03 in Mar 2025? Check its OOS rate and downtime.", "HPLC-03 in March 2025"),
    ("Why did on-time delivery change in Mar 2025 compared with Feb 2025?", "February 2025"),
    ("Why did Documentation deviations increase in Apr 2025, and who raised them?", "Documentation deviations from April 2025"),
])
def test_demo_answers_state_their_scope(path, tmp_path, q, scope):
    c = TestClient(create_app(path, demo=True, artifacts_dir=str(tmp_path)))
    ans = next(e for e in events(c.get("/api/ask", params={"q": q})) if e["kind"] == "answer")["text"]
    assert f"Scope of this walkthrough: {scope}" in ans
