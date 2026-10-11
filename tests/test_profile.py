import datetime as dt
import io

import pytest
from openpyxl import Workbook

from analyst import ingest, profile
from analyst.db import Database
from tests.test_analyst import db  # noqa: F401  (lab database fixture)


def make_db(tmp_path, sheets):
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    ingest.ingest("b.xlsx", buf.getvalue(), tmp_path / "p.duckdb")
    return Database(str(tmp_path / "p.duckdb"))


def by_rule(p, rule, column=None):
    return [i for i in p["issues"] if i["id"] == rule and (column is None or i["column"] == column)]


@pytest.fixture
def messy(tmp_path):
    rows = [["Date", "Region", "Units", "Price", "Paid Text", "Note", "Const"]]
    for i in range(60):
        rows.append([dt.datetime(2025, 1 + i // 20, 1 + i % 20), ["North", "South", "East"][i % 3], (i % 7) + 1, 10.0 + i * 0.5, str(i), "ok" if i % 2 else None, "same"])
    rows[5][1] = "north "                       # spelling / spacing variant of North
    rows[6][2] = -3                             # negative quantity
    rows[7][3] = 9000.0                         # outlier
    rows[8][4] = "N/A"                          # text in a numeric-looking column
    rows.append(rows[1][:])                     # exact duplicate of the first data row
    rows.append(rows[1][:])                     # and another
    return make_db(tmp_path, {"Sales": rows})


def test_summary_and_column_stats(messy):
    p = profile.profile(messy)
    s = p["summary"]
    assert s["tables"] == 1 and s["rows"] == 62 and s["columns"] == 7 and s["duplicate_rows"] == 2
    cols = {c["name"]: c for c in p["tables"][0]["columns"]}
    assert cols["units"]["kind"] == "numeric" and cols["units"]["min"] == -3 and cols["units"]["max"] == 7
    assert cols["date"]["kind"] == "date" and cols["date"]["min"].startswith("2025-01-01")
    assert cols["region"]["top"][0][0] in ("North", "South", "East") and cols["note"]["null_pct"] == pytest.approx(100 * 32 / 62, abs=1)
    assert s["completeness_pct"] < 100


def test_each_generic_rule_finds_what_was_planted(messy):
    p = profile.profile(messy)
    assert by_rule(p, "GQ-002")[0]["count"] == 2 and by_rule(p, "GQ-002")[0]["column"] is None
    t = by_rule(p, "GQ-004", "paid_text")[0]
    assert t["count"] == 61 and t["examples"] == ["N/A"] and t["severity"] == "Major"
    assert by_rule(p, "GQ-005", "region")[0]["count"] == 1 and "north " in by_rule(p, "GQ-005", "region")[0]["examples"][0]
    assert by_rule(p, "GQ-009", "region")[0]["count"] == 1
    assert by_rule(p, "GQ-008", "units")[0]["count"] == 1
    assert by_rule(p, "GQ-006", "price")[0]["count"] == 1
    assert by_rule(p, "GQ-003", "const")[0]["count"] == 62
    assert by_rule(p, "GQ-001", "note")[0]["severity"] == "Critical"        # 51.6% missing (>= 50%)
    assert not by_rule(p, "GQ-003", "note")                                # blank-vs-ok is information, not constant


def test_clean_data_has_no_findings(tmp_path):
    rows = [["id", "city", "qty"]] + [[i, ["A", "B", "C"][i % 3], i % 9 + 1] for i in range(1, 40)]
    p = profile.profile(make_db(tmp_path, {"T": rows}))
    assert p["issues"] == [] and p["summary"]["completeness_pct"] == 100.0


def test_issues_sorted_by_severity(messy):
    sev = [i["severity"] for i in profile.profile(messy)["issues"]]
    order = {"Critical": 0, "Major": 1, "Minor": 2}
    assert sev == sorted(sev, key=order.get)


def test_chart_specs_are_valid_and_cover_types(messy):
    p = profile.profile(messy)
    kinds = {c["type"] for c in p["charts"]}
    assert {"line", "hbar", "bar"} <= kinds
    from analyst.charts import TYPES
    for c in p["charts"]:
        assert c["type"] in TYPES and len(c["labels"]) == len(c["datasets"][0]["data"]) and c["title"]
    hist = next(c for c in p["charts"] if "distribution" in c["title"])
    assert len(hist["labels"]) == 12 and sum(hist["datasets"][0]["data"]) == 62


def test_empty_and_tiny_tables_do_not_break(tmp_path):
    p = profile.profile(make_db(tmp_path, {"A": [["x"], [1]], "B": [["y", "z"], ["a", None], ["b", None]]}))
    assert p["summary"]["tables"] == 2 and p["charts"] == []


def test_hostile_column_names_are_quoted(tmp_path):
    p = profile.profile(make_db(tmp_path, {"S": [['we"ird; DROP TABLE s;--', "b"], [1, 2], [3, 4]]}))
    assert p["summary"]["rows"] == 2 and p["tables"][0]["columns"][0]["name"].startswith("we_ird")


def test_agent_text_report(messy):
    t = profile.as_text(messy)
    assert "62 rows" in t and "GQ-004" in t and "N/A" in t


def test_profile_works_on_the_lab_database_too(db):
    p = profile.profile(db)
    assert p["summary"]["tables"] == 7 and p["summary"]["rows"] > 17000
    assert not by_rule(p, "GQ-002")      # planted duplicates differ in test_id, so only the lab-specific DQ-001 can see them
    assert {t["name"] for t in p["tables"]} >= {"tests", "instruments"}
