import pytest
from fastapi.testclient import TestClient

from analyst import dashboard as d
from analyst.server import create_app
from tests.test_analyst import db  # noqa: F401
from tests.test_server import path  # noqa: F401


def kv(out):
    return {k["key"]: k for k in out["kpis"]}


def test_kpis_match_direct_sql(db):
    k = kv(d.build(db, "2025-03"))
    n = db.run("SELECT count(*) FROM tests WHERE strftime(completed_date,'%Y-%m')='2025-03'").rows[0][0]
    oos = db.run("SELECT 100.0*avg((status='OOS')::int) FROM tests WHERE strftime(completed_date,'%Y-%m')='2025-03'").rows[0][0]
    on_time = db.run("""SELECT 100.0*avg((completed_date<=due_date)::int) FROM tests JOIN samples USING(sample_id)
        WHERE completed_date>=start_date AND due_date>=received_date AND strftime(completed_date,'%Y-%m')='2025-03'""").rows[0][0]
    assert k["tests"]["value"] == n
    assert k["oos_rate"]["value"] == pytest.approx(float(oos), abs=0.01)
    assert k["on_time"]["value"] == pytest.approx(float(on_time), abs=0.01)
    assert k["oos_rate"]["delta_kind"] == "pp" and k["oos_rate"]["good"] == "down"


def test_march_oos_spike_and_insight(db):
    out = d.build(db, "2025-03")
    assert kv(out)["oos_rate"]["delta"] > 1.5                      # percentage points vs February
    inst = {i["instrument"]: i for i in out["instruments"]}
    assert inst["HPLC-03"]["oos_rate"] > 15 and inst["HPLC-04"]["oos_rate"] < 6
    bad = [i for i in out["insights"] if i["level"] == "bad"]
    assert bad and "HPLC-03" in bad[0]["text"] and "HPLC-03" in bad[0]["ask"]


def test_february_on_time_collapse_and_insights(db):
    out = d.build(db, "2025-02")
    assert kv(out)["on_time"]["value"] < 92 and kv(out)["on_time"]["delta"] < -3
    text = " ".join(i["text"] for i in out["insights"])
    assert "Related Substances" in text and "LCMS-02" in text
    m = {x["label"]: x for x in out["methods"]}
    assert m["Related Substances"]["on_time"] < 80 < m["Assay"]["on_time"]
    assert {i["instrument"]: i["utilization"] for i in out["instruments"]}["LCMS-02"] < 60


def test_documentation_surge_insight(db):
    out = d.build(db, "2025-04")
    text = " ".join(i["text"] for i in out["insights"])
    assert "Documentation deviations rose" in text


def test_quiet_month_has_only_the_data_quality_note(db):
    out = d.build(db, "2025-05")
    assert [i["level"] for i in out["insights"] if i["level"] != "info"] == []


def test_filters_narrow_the_numbers(db):
    allv, lab_b = d.build(db, "2025-03"), d.build(db, "2025-03", lab="Lab B")
    assert kv(lab_b)["tests"]["value"] < kv(allv)["tests"]["value"]
    assert {i["lab"] for i in lab_b["instruments"]} == {"Lab B"}
    assert kv(lab_b)["oos_rate"]["value"] > kv(allv)["oos_rate"]["value"]   # HPLC-03 lives in Lab B
    svc = d.build(db, "2025-03", service_line="Stability Studies")
    assert 0 < kv(svc)["tests"]["value"] < kv(allv)["tests"]["value"]


def test_first_month_has_no_previous(db):
    out = d.build(db, "2024-10")
    assert out["period"]["prev_label"] is None and all(k["prev"] is None and k["delta"] is None for k in out["kpis"])


def test_shapes_and_heatmap(db):
    out = d.build(db, "2025-03")
    t = out["trend"]
    n = len(t["months"])
    assert len(t["tests"]) == len(t["oos_rate"]) == len(t["on_time"]) == len(t["tat"]) == n and t["months"][t["selected"]] == "2025-03"
    h = {r["instrument"]: [c["rate"] for c in r["cells"]] for r in out["heatmap"]["rows"]}
    assert len(out["heatmap"]["months"]) == 6 and h["HPLC-03"][-1] > 15 and h["HPLC-03"][-2] < 6


@pytest.mark.parametrize("kw", [{"month": "2099-01"}, {"lab": "Lab Z"}, {"service_line": "x' OR '1'='1"}, {"month": "2025-03'; DROP TABLE tests;--"}])
def test_rejects_unknown_slicer_values(db, kw):
    with pytest.raises(ValueError):
        d.build(db, **{"month": "2025-03", **kw})


def test_endpoints(path, tmp_path):
    c = TestClient(create_app(path, demo=True, artifacts_dir=str(tmp_path)))
    r = c.get("/api/dashboard")
    assert r.status_code == 200 and r.json()["period"]["label"] == "Mar 2025"
    assert c.get("/api/dashboard", params={"month": "2025-05", "lab": "Lab C"}).json()["filters"]["lab"] == "Lab C"
    assert c.get("/api/dashboard", params={"lab": "Mars"}).status_code == 400
    assert c.get("/api/dashboard", params={"month": "", "lab": ""}).status_code == 200      # blank slicer == 'All'
    s = c.get("/api/dq").json()
    assert s["flagged_tests"] == 240 and len(s["rules"]) == 10
    assert c.get("/api/dq/DQ-003").json()["total"] == 60 and c.get("/api/dq/DQ-999").status_code == 404
