import json
from pathlib import Path

import pytest

from analyst import dq
from scripts.make_data import DEFECTS
from tests.test_analyst import db  # noqa: F401

EXPECTED = {"DQ-001": DEFECTS["duplicates"], "DQ-002": DEFECTS["completed_before_start"], "DQ-003": DEFECTS["missing_analyst"],
            "DQ-004": DEFECTS["unit_scale_error"], "DQ-005": DEFECTS["oos_reported_as_pass"] + DEFECTS["pass_reported_as_oos"],
            "DQ-006": DEFECTS["missing_result"], "DQ-007": DEFECTS["test_on_broken_instrument"], "DQ-008": DEFECTS["sample_due_before_received"],
            "DQ-009": 0, "DQ-010": 0}


def test_every_rule_finds_exactly_the_planted_defects(db):
    got = {r["id"]: r["count"] for r in dq.summary(db)["rules"]}
    assert got == EXPECTED


def test_summary_totals(db):
    s = dq.summary(db)
    assert s["flagged_tests"] == sum(v for k, v in EXPECTED.items() if k != "DQ-008")     # planted defects never overlap
    assert s["clean_pct"] == pytest.approx(100 * (1 - s["flagged_tests"] / s["total_tests"]), abs=0.01)
    assert s["open_rules"] == 8 and {r["status"] for r in s["rules"] if r["id"] in ("DQ-009", "DQ-010")} == {"Pass"}
    assert s["issues_by_severity"]["Critical"] == 60 + 35 + 30 + 20


def test_issue_listing_and_paging(db):
    a = dq.issues(db, "DQ-003", limit=25)
    assert a["total"] == 60 and len(a["rows"]) == 25 and a["columns"] == ["record_id", "record_date", "detail"]
    b = dq.issues(db, "DQ-003", limit=25, offset=50)
    assert len(b["rows"]) == 10 and not ({r[0] for r in a["rows"]} & {r[0] for r in b["rows"]})
    assert dq.issues(db, "DQ-003", limit=10**6)["limit"] == 200
    with pytest.raises(KeyError):
        dq.issues(db, "DQ-999")


def test_listed_records_really_are_defective(db):
    for r in dq.issues(db, "DQ-005")["rows"]:
        t = db.run(f"SELECT result_value, spec_low, spec_high, status FROM tests WHERE test_id={r[0]}").rows[0]
        assert t[3] != ("OOS" if t[0] < t[1] or t[0] > t[2] else "Pass")
    for r in dq.issues(db, "DQ-001")["rows"]:
        first = int(r[2].split()[-1])
        assert first < r[0]


def test_the_planted_oos_incident_is_not_a_data_quality_issue(db):
    flagged = f"SELECT count(*) FROM tests WHERE instrument_id='HPLC-03' AND method='Assay' AND completed_date BETWEEN '2025-03-03' AND '2025-03-23' AND test_id IN ({dq.flagged_tests_sql()})"
    n = db.run(flagged).rows[0][0]
    assert n < 5                                      # a real drift must stay visible as a real OOS spike, not be "cleaned"


def test_ground_truth_file_matches_generator():
    truth = json.loads(Path("data/ground_truth.json").read_text())
    assert truth["defects"] == DEFECTS and truth["incidents"]["oos_spike"]["instrument"] == "HPLC-03"


def test_agent_report_text(db):
    t = dq.as_text(db)
    assert "240 of 17297" in t and "DQ-003" in t and "Rules with no issues: DQ-009, DQ-010" in t
