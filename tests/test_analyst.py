from types import SimpleNamespace as NS

import pytest

from analyst.agent import Analyst
from analyst.db import Database, QueryRejected
from scripts.make_data import build


@pytest.fixture(scope="session")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("d") / "shop.duckdb"
    build(path)
    return Database(str(path))


# ---- guardrails -------------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "DROP TABLE orders",
    "DELETE FROM orders",
    "INSERT INTO orders VALUES (1,1,'2025-01-01','web','completed')",
    "SELECT 1; DROP TABLE orders",
    "COPY orders TO '/tmp/x.csv'",
    "ATTACH '/tmp/other.db'",
    "",
])
def test_rejects_non_readonly(db, sql):
    with pytest.raises(QueryRejected):
        db.run(sql)


def test_cannot_read_filesystem(db):
    with pytest.raises(Exception):
        db.run("SELECT * FROM read_csv('/etc/passwd')")


def test_truncates_rows(db):
    r = db.run("SELECT * FROM order_items")
    assert len(r.rows) == 200 and r.truncated


def test_timeout(db, monkeypatch):
    monkeypatch.setattr("analyst.db.TIMEOUT_S", 0.5)
    with pytest.raises(QueryRejected, match="timeout"):
        db.run("SELECT count(*) FROM range(10000000000) a")


def test_planted_incident_is_visible(db):
    rows = db.run("""
        SELECT strftime(order_date,'%Y-%m') m, count(*) FROM orders
        WHERE channel='mobile_app' AND customer_id IN (SELECT customer_id FROM customers WHERE region IN ('West','East'))
        GROUP BY 1 ORDER BY 1""").rows
    by_month = dict(rows)
    assert by_month["2025-03"] < 0.3 * by_month["2025-02"]


# ---- agent loop with a scripted fake model ----------------------------------

def msg(stop, *blocks, tin=100, tout=50):
    return NS(stop_reason=stop, content=list(blocks), usage=NS(input_tokens=tin, output_tokens=tout))


def tool(id_, name, **inp):
    return NS(type="tool_use", id=id_, name=name, input=inp)


def text(t):
    return NS(type="text", text=t)


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        return self.script.pop(0)


def test_agent_recovers_from_sql_error_then_answers(db):
    client = FakeClient([
        msg("tool_use", tool("1", "get_schema")),
        msg("tool_use", tool("2", "run_sql", sql="SELECT nope FROM orders")),
        msg("tool_use", tool("3", "run_sql", sql="SELECT count(*) FROM orders")),
        msg("end_turn", text("done")),
    ])
    agent = Analyst(db, client=client)
    events = list(agent.run("q"))
    s = agent.last_stats
    assert s.stop_reason == "answered" and s.answer == "done"
    assert s.sql_calls == 2 and s.sql_errors == 1
    assert any(e.kind == "tool_result" and e.data["is_error"] for e in events)


def test_agent_stops_at_step_limit(db):
    client = FakeClient([msg("tool_use", tool(str(i), "get_schema")) for i in range(10)])
    agent = Analyst(db, client=client, max_steps=3)
    list(agent.run("q"))
    assert agent.last_stats.stop_reason == "max_steps" and agent.last_stats.steps == 3


def test_agent_stops_at_token_budget(db):
    client = FakeClient([msg("tool_use", tool(str(i), "get_schema"), tin=500, tout=500) for i in range(10)])
    agent = Analyst(db, client=client, max_tokens_budget=1500)
    list(agent.run("q"))
    assert agent.last_stats.stop_reason == "token_budget"


def test_agent_nudges_instead_of_answering_over_an_error(db):
    client = FakeClient([
        msg("tool_use", tool("1", "run_sql", sql="SELECT nope FROM orders")),
        msg("end_turn", text("here is a fixed query in prose")),       # premature answer -> nudged
        msg("tool_use", tool("2", "run_sql", sql="SELECT count(*) FROM orders")),
        msg("end_turn", text("done")),
    ])
    agent = Analyst(db, client=client)
    list(agent.run("q"))
    s = agent.last_stats
    assert s.nudges == 1 and s.answer == "done" and s.sql_calls == 2


def test_nudges_are_capped(db):
    client = FakeClient([msg("tool_use", tool("1", "run_sql", sql="SELECT nope FROM orders"))] +
                        [msg("end_turn", text("prose"))] * 3)
    agent = Analyst(db, client=client)
    list(agent.run("q"))
    assert agent.last_stats.nudges == 2 and agent.last_stats.answer == "prose"
