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
    assert "Autonomous Data Analyst" in c.get("/").text
    assert c.get("/api/config").json()["demo"] is True


def test_stream_with_fake_client(path, tmp_path):
    fc = FakeClient([msg("tool_use", tool("1", "run_sql", sql="SELECT count(*) n FROM orders")),
                     msg("end_turn", text("done"))])
    c = TestClient(create_app(path, client=fc, artifacts_dir=str(tmp_path)))
    ev = events(c.get("/api/ask", params={"q": "how many?"}))
    assert [e["kind"] for e in ev] == ["tool_call", "tool_result", "answer", "done"]
    assert ev[-1]["sql_calls"] == 1 and ev[-1]["stop"] == "answered"


def test_demo_mode_end_to_end_with_chart(path, tmp_path):
    c = TestClient(create_app(path, demo=True, sandbox="local", artifacts_dir=str(tmp_path)))
    ev = events(c.get("/api/ask", params={"q": "Why did revenue drop in March 2025?"}))
    kinds = [e["kind"] for e in ev]
    assert kinds[-1] == "done" and "answer" in kinds
    ans = next(e for e in ev if e["kind"] == "answer")["text"]
    assert "mobile_app" in ans and "West" in ans and "East" in ans
    chart = next(e for e in ev if e["kind"] == "tool_result" and e.get("files"))
    assert c.get(f"/artifacts/{chart['files'][0]}").headers["content-type"] == "image/png"
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
