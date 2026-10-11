import io
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from analyst import datasets, ingest
from analyst.datasets import LAB_ID, DatasetError, Registry
from analyst.db import QueryRejected
from analyst.example import build_example_workbook
from analyst.server import create_app
from tests.test_analyst import FakeClient, db, msg, text, tool  # noqa: F401
from tests.test_ingest import xlsx
from tests.test_server import events, path  # noqa: F401

H = {"X-Requested-With": "labops"}


@pytest.fixture
def app(path, tmp_path):
    return create_app(path, demo=False, client=FakeClient([]), artifacts_dir=str(tmp_path / "art"), upload_dir=str(tmp_path / "up"))


@pytest.fixture
def c(app):
    return TestClient(app)


def upload(c, data, name="orders.xlsx"):
    return c.post("/api/datasets", content=data, headers={"X-Filename": quote(name), **H})


def test_upload_requires_the_same_origin_header(c):
    r = c.post("/api/datasets", content=SALES, headers={"X-Filename": "a.xlsx"})          # what a cross-site form could not add
    assert r.status_code == 403 and [d["id"] for d in c.get("/api/datasets").json()] == [LAB_ID]


SALES = xlsx({"Orders": [["Order ID", "Region", "Units", "Price"]] + [[i, ["North", "South"][i % 2], i % 5 + 1, 10 + i] for i in range(1, 31)]})


def test_upload_list_query_delete_roundtrip(c):
    r = upload(c, SALES, "My Sales 2025.xlsx")
    assert r.status_code == 201
    meta = r.json()
    assert meta["name"] == "My Sales 2025" and meta["kind"] == "custom" and meta["tables"] == [dict(name="orders", sheet="Orders", rows=30, columns=4)]
    assert meta["examples"] and any("orders" in e for e in meta["examples"])
    ids = [d["id"] for d in c.get("/api/datasets").json()]
    assert ids == [LAB_ID, meta["id"]]
    ds = meta["id"]
    t = c.get("/api/tables", params={"dataset": ds}).json()
    assert t == [dict(name="orders", rows=30, columns=["order_id", "region", "units", "price"])]
    page = c.get("/api/table/orders", params={"dataset": ds, "sort": "price", "desc": "true", "limit": 3}).json()
    assert page["total"] == 30 and page["rows"][0][3] == 40
    assert "orders" in c.get("/api/schema", params={"dataset": ds}).json()["schema"]
    p = c.get("/api/profile", params={"dataset": ds}).json()
    assert p["summary"]["rows"] == 30 and p["summary"]["tables"] == 1
    assert c.delete(f"/api/datasets/{ds}", headers=H).status_code == 200
    assert [d["id"] for d in c.get("/api/datasets").json()] == [LAB_ID]
    assert c.get("/api/tables", params={"dataset": ds}).status_code == 404


def test_lab_dataset_is_listed_and_cannot_be_deleted(c):
    lab = c.get("/api/datasets").json()[0]
    assert lab["id"] == LAB_ID and lab["kind"] == "lab" and {t["name"] for t in lab["tables"]} >= {"tests", "instruments"}
    assert c.delete(f"/api/datasets/{LAB_ID}", headers=H).status_code == 400


def test_delete_needs_the_custom_header(c):
    ds = upload(c, SALES).json()["id"]
    assert c.delete(f"/api/datasets/{ds}").status_code == 403                  # what a cross-site form could send
    assert c.get("/api/tables", params={"dataset": ds}).status_code == 200


@pytest.mark.parametrize("bad", ["../../etc/passwd", "lab/../x", "..", "%2e%2e%2fsecret", "ABCDEF123456", "abcdef12345", "abcdef1234567", "x" * 200, "'; DROP TABLE tests;--"])
def test_dataset_ids_can_never_be_paths(c, bad):
    for url in ("/api/tables", "/api/profile", "/api/schema"):
        assert c.get(url, params={"dataset": bad}).status_code == 404
    assert c.delete(f"/api/datasets/{quote(bad, safe='')}", headers=H).status_code in (404, 405)


def test_upload_errors_are_friendly_and_leave_nothing_behind(c, tmp_path):
    assert upload(c, b"x", "old.xls").json()["detail"].startswith("Legacy .xls")
    assert upload(c, b"not a zip", "bad.xlsx").status_code == 400
    assert upload(c, b"", "empty.xlsx").status_code == 400
    assert c.post("/api/datasets", content=SALES, headers=H).status_code == 400            # no X-Filename
    assert upload(c, SALES, "macro.xlsm").status_code == 400
    assert [d["id"] for d in c.get("/api/datasets").json()] == [LAB_ID]
    left = [p.name for p in (tmp_path / "up").iterdir()]
    assert left == [], left


def test_size_limit_enforced_while_streaming(c, monkeypatch):
    monkeypatch.setattr(ingest, "MAX_BYTES", 1000)
    r = upload(c, b"a" * 5000, "big.csv")
    assert r.status_code == 413 and "limit" in r.json()["detail"]


def test_dataset_count_and_storage_quotas(c, monkeypatch):
    monkeypatch.setattr(datasets, "MAX_DATASETS", 2)
    assert upload(c, SALES, "a.xlsx").status_code == 201 and upload(c, SALES, "b.xlsx").status_code == 201
    r = upload(c, SALES, "c.xlsx")
    assert r.status_code == 409 and "Delete one first" in r.json()["detail"]
    monkeypatch.setattr(datasets, "MAX_DATASETS", 10)
    monkeypatch.setattr(datasets, "MAX_TOTAL_BYTES", 10)
    assert upload(c, SALES, "d.xlsx").status_code == 409


def test_host_allowlist_blocks_dns_rebinding(app):
    evil = TestClient(app, base_url="http://attacker.example")
    assert evil.get("/api/datasets").status_code == 400


def test_dashboard_and_lab_rules_are_lab_only(c):
    ds = upload(c, SALES).json()["id"]
    for url in ("/api/dashboard", "/api/dq", "/api/dq/DQ-001"):
        r = c.get(url, params={"dataset": ds})
        assert r.status_code == 400 and "built-in lab dataset" in r.json()["detail"]
    assert c.get("/api/dashboard").status_code == 200


def test_agent_on_uploaded_data_uses_generic_prompt_and_its_tables(path, tmp_path):
    seen = {}

    class Spy(FakeClient):
        def _create(self, **kw):
            seen["system"] = kw["system"]
            return super()._create(**kw)
    fc = Spy([msg("tool_use", tool("1", "run_sql", sql="SELECT sum(units) s FROM orders")), msg("end_turn", text("done"))])
    c = TestClient(create_app(path, client=fc, artifacts_dir=str(tmp_path / "a"), upload_dir=str(tmp_path / "up")))
    ds = upload(c, SALES).json()["id"]
    ev = events(c.get("/api/ask", params={"q": "total units?", "dataset": ds}))
    res = next(e for e in ev if e["kind"] == "tool_result")
    assert not res["is_error"] and res["table"]["rows"] == [[sum(i % 5 + 1 for i in range(1, 31))]]
    assert "uploaded their own workbook" in seen["system"] and "OOS" not in seen["system"]
    fc2 = Spy([msg("end_turn", text("x"))])
    c2 = TestClient(create_app(path, client=fc2, artifacts_dir=str(tmp_path / "a2"), upload_dir=str(tmp_path / "up2")))
    events(c2.get("/api/ask", params={"q": "hi"}))
    assert "OOS" in seen["system"] and "analytical / QC laboratory" in seen["system"]


def test_data_quality_tool_uses_generic_review_for_uploads(path, tmp_path):
    fc = FakeClient([msg("tool_use", tool("1", "data_quality")), msg("end_turn", text("ok"))])
    c = TestClient(create_app(path, client=fc, artifacts_dir=str(tmp_path / "a"), upload_dir=str(tmp_path / "up")))
    ds = upload(c, build_example_workbook(), "ex.xlsx").json()["id"]
    ev = events(c.get("/api/ask", params={"q": "check quality", "dataset": ds}))
    out = next(e for e in ev if e["kind"] == "tool_result")["text"]
    assert "GQ-" in out and "DQ-" not in out


def test_demo_mode_declines_custom_datasets_honestly(path, tmp_path):
    c = TestClient(create_app(path, demo=True, artifacts_dir=str(tmp_path / "a"), upload_dir=str(tmp_path / "up")))
    ds = upload(c, SALES).json()["id"]
    ev = events(c.get("/api/ask", params={"q": "total units?", "dataset": ds}))
    assert "Demo mode only works with the built-in lab data" in next(e for e in ev if e["kind"] == "answer")["text"] and ev[-1]["kind"] == "done"
    assert c.get("/api/ask", params={"q": "x", "dataset": "nope"}).status_code == 404


def test_example_workbook_endpoint_and_findings(c):
    r = c.get("/api/example.xlsx")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxmlformats")
    meta = upload(c, r.content, "example_orders.xlsx").json()
    assert {t["name"] for t in meta["tables"]} == {"orders", "targets"}
    p = c.get("/api/profile", params={"dataset": meta["id"]}).json()
    found = {(i["id"], i["column"]) for i in p["issues"]}
    assert ("GQ-002", None) in found and ("GQ-004", "discount") in found and ("GQ-005", "region") in found and ("GQ-008", "units") in found and ("GQ-006", "unit_price") in found
    assert next(i for i in p["issues"] if i["id"] == "GQ-002")["count"] == 2
    assert p["charts"]


def test_uploads_survive_a_restart(path, tmp_path):
    up = tmp_path / "persist"
    a = TestClient(create_app(path, artifacts_dir=str(tmp_path / "a"), upload_dir=str(up)))
    ds = upload(a, SALES, "keep.xlsx").json()["id"]
    b = TestClient(create_app(path, artifacts_dir=str(tmp_path / "a"), upload_dir=str(up)))
    assert [d["id"] for d in b.get("/api/datasets").json()] == [LAB_ID, ds]
    assert b.get("/api/table/orders", params={"dataset": ds}).json()["total"] == 30


def test_uploaded_database_is_read_only_for_the_agent(tmp_path):
    reg = Registry(str(tmp_path / "x.duckdb"), tmp_path / "up")
    meta = reg.add("a.xlsx", SALES)
    db = reg.db(meta["id"])
    for sql in ("DROP TABLE orders", "DELETE FROM orders", "COPY orders TO '/tmp/leak.csv'", "SELECT * FROM read_csv('/etc/passwd')"):
        with pytest.raises((QueryRejected, Exception)):
            db.run(sql)
    assert db.run("SELECT count(*) FROM orders").rows[0][0] == 30
    with pytest.raises(DatasetError):
        reg.delete(LAB_ID)


def test_display_name_never_contains_path_or_control_chars(tmp_path):
    reg = Registry(str(tmp_path / "x.duckdb"), tmp_path / "up")
    assert reg.add("C:\\Users\\bob\\..\\secret\\Q1\x00 report.xlsx", SALES)["name"] == "Q1 report"
    assert reg.add("../../etc/evil.xlsx", SALES)["filename"] == "evil.xlsx"
