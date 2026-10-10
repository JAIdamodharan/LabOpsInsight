import pytest
from fastapi.testclient import TestClient

from analyst import dashboard as d
from analyst.server import create_app
from tests.test_analyst import db  # noqa: F401
from tests.test_server import path  # noqa: F401


def test_linreg_exact_line_and_degenerate():
    slope, icpt = d.linreg([1.0, 3.0, 5.0, 7.0])
    assert (round(slope, 6), round(icpt, 6)) == (2.0, 1.0)
    assert d.linreg([5.0]) == (0.0, 5.0) and d.linreg([]) == (0.0, 0.0)


def test_next_months_rolls_year():
    assert d._next_months("2025-11", 3) == ["2025-12", "2026-01", "2026-02"]


def test_kpis_match_direct_sql(db):
    out = d.build(db, "2025-03")
    k = {x["key"]: x for x in out["kpis"]}
    rev = lambda m: float(db.run(f"SELECT sum(quantity*unit_price) FROM orders JOIN order_items USING(order_id) WHERE status='completed' AND strftime(order_date,'%Y-%m')='{m}'").rows[0][0])
    assert k["revenue"]["value"] == pytest.approx(rev("2025-03"), abs=0.01)
    assert k["revenue"]["prev"] == pytest.approx(rev("2025-02"), abs=0.01)
    assert k["revenue"]["delta"] == pytest.approx(100 * (rev("2025-03") / rev("2025-02") - 1), abs=0.01)
    n = db.run("SELECT count(*) FROM orders WHERE strftime(order_date,'%Y-%m')='2025-03'").rows[0][0]
    assert k["orders"]["value"] == n
    assert k["cancel_rate"]["delta_kind"] == "pp" and k["cancel_rate"]["good"] == "down"


def test_heatmap_exposes_planted_incident(db):
    heat = {r["channel"]: {c["region"]: c["change"] for c in r["cells"]} for r in d.build(db, "2025-03")["heatmap"]["rows"]}
    assert heat["mobile_app"]["West"] < -70 and heat["mobile_app"]["East"] < -70
    assert heat["web"]["West"] > -20 and heat["mobile_app"]["Central"] > -30


def test_filters_narrow_the_numbers(db):
    allv = d.build(db, "2025-03")
    west = d.build(db, "2025-03", region="West")
    app = d.build(db, "2025-03", channel="mobile_app")
    both = d.build(db, "2025-03", region="West", channel="mobile_app")
    val = lambda o: {k["key"]: k["value"] for k in o["kpis"]}["revenue"]
    assert val(both) < val(west) < val(allv) and val(both) < val(app) < val(allv)
    assert [r["region"] for r in west["regions"]] == ["West"]
    assert west["heatmap"]["rows"] == allv["heatmap"]["rows"]            # heatmap deliberately ignores slicers


def test_first_month_has_no_previous(db):
    out = d.build(db, "2024-10")
    assert out["period"]["prev_label"] is None and all(k["prev"] is None and k["delta"] is None for k in out["kpis"])
    assert out["heatmap"]["rows"] == []


def test_trend_and_projection_shapes(db):
    t = d.build(db, "2025-03")["trend"]
    n = len(t["months"])
    assert len(t["revenue"]) == len(t["orders"]) == len(t["aov"]) == n
    assert len(t["trendline"]) == n + 2 and t["projection_labels"] == ["Jul 2025", "Aug 2025"]
    assert t["months"][t["selected"]] == "2025-03"


@pytest.mark.parametrize("kw", [{"month": "2099-01"}, {"region": "Mars"}, {"channel": "x' OR '1'='1"}, {"month": "2025-03'; DROP TABLE orders;--"}])
def test_rejects_unknown_slicer_values(db, kw):
    with pytest.raises(ValueError):
        d.build(db, **{"month": "2025-03", **kw})


def test_endpoint(path, tmp_path):
    c = TestClient(create_app(path, demo=True, artifacts_dir=str(tmp_path)))
    r = c.get("/api/dashboard")
    assert r.status_code == 200 and r.json()["period"]["label"] == "Mar 2025"
    assert c.get("/api/dashboard", params={"month": "2025-05", "region": "East"}).json()["filters"]["region"] == "East"
    assert c.get("/api/dashboard", params={"region": "Mars"}).status_code == 400
    assert c.get("/api/dashboard", params={"month": "", "region": ""}).status_code == 200   # blank slicer == 'All'
