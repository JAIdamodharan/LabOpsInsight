from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from analyst.agent import Analyst
from analyst.charts import ChartError, build_chart
from analyst.db import QueryResult
from analyst.server import create_app
from tests.test_analyst import FakeClient, db, msg, text, tool  # noqa: F401
from tests.test_server import events, path  # noqa: F401


def R(cols, rows, truncated=False):
    return QueryResult(cols, rows, truncated)


def test_bar_with_decimal_and_dates():
    spec = build_chart(R(["d", "rev"], [(date(2025, 1, 1), Decimal("10.5")), (date(2025, 2, 1), Decimal("7"))]),
                       {"type": "bar", "title": "T", "x": "d", "y": "rev"})
    assert spec["labels"] == ["2025-01-01", "2025-02-01"] and spec["datasets"][0]["data"] == [10.5, 7.0]


def test_multi_y_comma_separated():
    spec = build_chart(R(["c", "a", "b"], [("x", 1, 2), ("y", 3, 4)]), {"type": "line", "title": "t", "x": "c", "y": "a, b"})
    assert [d["label"] for d in spec["datasets"]] == ["a", "b"]


def test_series_pivot_fills_gaps():
    spec = build_chart(R(["m", "ch", "v"], [("jan", "web", 1), ("jan", "app", 2), ("feb", "web", 3)]),
                       {"type": "line", "title": "t", "x": "m", "y": "v", "series": "ch"})
    assert spec["labels"] == ["jan", "feb"]
    assert {d["label"]: d["data"] for d in spec["datasets"]} == {"web": [1.0, 3.0], "app": [2.0, None]}


def test_series_duplicates_rejected():
    with pytest.raises(ChartError, match="aggregate"):
        build_chart(R(["m", "ch", "v"], [("jan", "web", 1), ("jan", "web", 2)]),
                    {"type": "bar", "title": "t", "x": "m", "y": "v", "series": "ch"})


def test_pie_limits_and_negatives():
    many = R(["c", "v"], [(str(i), 1) for i in range(13)])
    with pytest.raises(ChartError, match="too many slices"):
        build_chart(many, {"type": "pie", "title": "t", "x": "c", "y": "v"})
    with pytest.raises(ChartError, match="non-negative"):
        build_chart(R(["c", "v"], [("a", -1)]), {"type": "pie", "title": "t", "x": "c", "y": "v"})


def test_kpi_cards():
    spec = build_chart(R(["total_revenue", "orders", "top"], [(Decimal("1420207"), 12, "web")]), {"type": "kpi", "title": "March"})
    assert spec["cards"] == [{"label": "total revenue", "value": 1420207.0}, {"label": "orders", "value": 12.0}, {"label": "top", "value": "web"}]


def test_scatter():
    spec = build_chart(R(["a", "b"], [(1, 2), (3, 4)]), {"type": "scatter", "title": "t", "x": "a", "y": "b"})
    assert spec["datasets"][0]["data"] == [{"x": 1.0, "y": 2.0}, {"x": 3.0, "y": 4.0}]


@pytest.mark.parametrize("res,args,msg_", [
    (R(["a", "b"], [("x", "oops")]), {"type": "bar", "title": "t", "x": "a", "y": "b"}, "must be numeric"),
    (R(["a", "b"], [("x", 1)]), {"type": "bar", "title": "t", "x": "a", "y": "zzz"}, "Available columns: a, b"),
    (R(["a"], []), {"type": "bar", "title": "t", "x": "a", "y": "a"}, "no rows"),
    (R(["a", "b"], [("x", 1)], truncated=True), {"type": "bar", "title": "t", "x": "a", "y": "b"}, "200 rows"),
    (R(["a", "b"], [("x", 1)]), {"type": "radar", "title": "t", "x": "a", "y": "b"}, "type must be"),
    (R(["a", "b"], [("x", 1)]), {"type": "bar", "title": "t"}, "required"),
])
def test_errors_are_actionable(res, args, msg_):
    with pytest.raises(ChartError, match=msg_):
        build_chart(res, args)


def test_agent_chart_tool_emits_spec_and_table(db):
    client = FakeClient([
        msg("tool_use", tool("1", "run_sql", sql="SELECT channel, count(*) n FROM orders GROUP BY 1 ORDER BY 1")),
        msg("tool_use", tool("2", "chart", type="bar", title="Orders by channel", x="channel", y="n",
                             sql="SELECT channel, count(*) n FROM orders GROUP BY 1 ORDER BY 1")),
        msg("end_turn", text("done")),
    ])
    agent = Analyst(db, client=client)
    ev = list(agent.run("q"))
    sql_ev, chart_ev = [e for e in ev if e.kind == "tool_result"]
    assert sql_ev.data["table"]["columns"] == ["channel", "n"] and len(sql_ev.data["table"]["rows"]) == 3
    assert chart_ev.data["chart"]["labels"] == ["marketplace", "mobile_app", "web"]
    assert agent.last_stats.chart_calls == 1 and len(agent.last_stats.charts) == 1


def test_agent_chart_error_is_fed_back_not_raised(db):
    client = FakeClient([
        msg("tool_use", tool("1", "chart", type="bar", title="t", x="nope", y="n", sql="SELECT 1 n")),
        msg("tool_use", tool("2", "chart", type="bar", title="t", x="n", y="n", sql="DROP TABLE orders")),
        msg("end_turn", text("sorry")), msg("end_turn", text("sorry")), msg("end_turn", text("sorry")),  # nudged twice, then accepted
    ])
    agent = Analyst(db, client=client)
    res = [e for e in agent.run("q") if e.kind == "tool_result"]
    assert all(e.data["is_error"] for e in res) and "Available columns" in res[0].data["text"]
    assert agent.last_stats.nudges == 2


# ---- table browser API ------------------------------------------------------

def test_tables_api_lists_and_pages(path, tmp_path):
    c = TestClient(create_app(path, demo=True, artifacts_dir=str(tmp_path)))
    names = {t["name"]: t for t in c.get("/api/tables").json()}
    assert set(names) == {"customers", "order_items", "orders", "products"} and names["products"]["rows"] == 200
    r = c.get("/api/table/products", params={"limit": 5, "sort": "price", "desc": "true"}).json()
    assert len(r["rows"]) == 5 and r["rows"][0][2] >= r["rows"][4][2] and r["total"] == 200
    assert c.get("/api/table/products", params={"limit": 99999}).json()["limit"] == 200


@pytest.mark.parametrize("url", [
    "/api/table/nope",
    '/api/table/orders;DROP TABLE orders',
    '/api/table/orders?sort=order_id;DROP TABLE orders',
    "/api/table/orders?sort=%22%20OR%201=1",
])
def test_tables_api_blocks_injection(path, tmp_path, url):
    c = TestClient(create_app(path, demo=True, artifacts_dir=str(tmp_path)))
    assert c.get(url).status_code in (400, 404)
    assert c.get("/api/table/orders", params={"limit": 1}).status_code == 200   # still intact
