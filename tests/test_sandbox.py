import os
import subprocess
from types import SimpleNamespace as NS

import pytest

from analyst import sandbox as sb
from analyst.agent import Analyst
from analyst.sandbox import DockerSandbox, LocalSandbox
from tests.test_analyst import FakeClient, db, msg, text, tool  # noqa: F401


@pytest.fixture(scope="session")
def dbpath(db, tmp_path_factory):
    return str(db.con.execute("PRAGMA database_list").fetchall()[0][2])


@pytest.fixture
def local(dbpath, tmp_path):
    return LocalSandbox(dbpath, artifacts_dir=str(tmp_path / "art"))


def test_runs_code_and_queries_db(local):
    r = local.run("print(con.sql('select count(*) c from orders').df().c[0] > 1000)")
    assert r.stdout.strip() == "True" and not r.is_error


def test_saves_chart_artifact(local):
    r = local.run("plt.plot([1,2,3]); plt.savefig(os.path.join(OUT_DIR,'c.png'))")
    assert len(r.files) == 1 and r.files[0].suffix == ".png" and r.files[0].stat().st_size > 500
    assert "Saved files" in r.to_text()


def test_errors_are_reported_not_raised(local):
    r = local.run("1/0")
    assert r.is_error and "ZeroDivisionError" in r.to_text()


def test_timeout(local, monkeypatch):
    monkeypatch.setattr(sb, "TIMEOUT_S", 1)
    r = local.run("import time; time.sleep(5)")
    assert r.timed_out and r.is_error


def test_secrets_not_inherited(local, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    r = local.run("print(os.environ.get('ANTHROPIC_API_KEY'))")
    assert r.stdout.strip() == "None"


def test_db_handle_is_readonly(local):
    r = local.run("con.execute('DROP TABLE orders')")
    assert r.is_error


def test_docker_command_is_locked_down(dbpath, tmp_path):
    cmd = DockerSandbox(dbpath).build_cmd("n", tmp_path)
    s = " ".join(cmd)
    for flag in ["--network none", "--read-only", "--cap-drop ALL", "no-new-privileges",
                 "--user 65534:65534", "--memory 512m", "--pids-limit 64", ":/data/db.duckdb:ro"]:
        assert flag in s, flag
    assert "--privileged" not in s and "ANTHROPIC" not in s


@pytest.mark.skipif(not sb.docker_available(), reason="docker daemon/image not available")
def test_docker_integration_no_network(dbpath, tmp_path):
    d = DockerSandbox(dbpath, artifacts_dir=str(tmp_path))
    assert d.run("print(con.sql('select 1').fetchone()[0])").stdout.strip() == "1"
    r = d.run("import urllib.request; urllib.request.urlopen('http://example.com', timeout=3)")
    assert r.is_error


def test_agent_uses_python_tool(db, local):
    client = FakeClient([
        msg("tool_use", tool("1", "run_python", code="print(6*7)")),
        msg("end_turn", text("42")),
    ])
    agent = Analyst(db, client=client, sandbox=local)
    events = list(agent.run("q"))
    assert agent.last_stats.python_calls == 1
    assert any(e.kind == "tool_result" and e.data["text"].strip() == "42" for e in events)


def test_python_tool_hidden_without_sandbox(db):
    assert "run_python" not in [t["name"] for t in Analyst(db, client=object()).tools]
