"""Rule-based data review for lab records: the analytics equivalent of clinical-data-management edit checks.

Each rule is one read-only SQL query returning (record_id, record_date, detail) for every offending record.
Rules are tagged with the ALCOA+ data-integrity attribute they protect. Nothing is modified: the output is a
query log (what to raise, with whom, and how severe), never an automatic "fix".
"""
from dataclasses import dataclass

from .db import Database

PARTITION = "sample_id, method, instrument_id, start_date, completed_date, result_value"


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    table: str
    severity: str  # Critical | Major | Minor
    alcoa: str
    description: str
    sql: str

    @property
    def normalized(self) -> str:
        """Rule SQL with its output columns forced to (record_id, record_date, detail)."""
        return f"SELECT * FROM ({self.sql}) AS x(record_id, record_date, detail)"


RULES = [
    Rule("DQ-001", "Duplicate test records", "tests", "Major", "Original",
         "Identical test (same sample, method, instrument, dates and result) recorded more than once; later copies flagged.",
         f"""SELECT test_id AS record_id, completed_date AS record_date,
                    'Duplicate of test ' || first_id AS detail
             FROM (SELECT test_id, completed_date,
                          min(test_id) OVER (PARTITION BY {PARTITION}) AS first_id,
                          row_number() OVER (PARTITION BY {PARTITION} ORDER BY test_id) AS rn FROM tests) WHERE rn > 1"""),
    Rule("DQ-002", "Completed before it started", "tests", "Critical", "Contemporaneous",
         "completed_date is earlier than start_date: impossible sequence, TAT cannot be trusted.",
         """SELECT test_id, completed_date, 'Completed ' || (start_date - completed_date) || ' day(s) before start'
            FROM tests WHERE completed_date < start_date"""),
    Rule("DQ-003", "Missing analyst (not attributable)", "tests", "Critical", "Attributable",
         "No analyst recorded for a completed test: the result cannot be attributed to a person.",
         "SELECT test_id, completed_date, 'analyst_id is empty' FROM tests WHERE analyst_id IS NULL"),
    Rule("DQ-004", "Implausible result (unit / scale error)", "tests", "Major", "Accurate",
         "Result is more than 3x the upper specification limit (genuine OOS results sit just above it), typical of a unit or decimal-place entry error. Cannot see scale errors on near-zero values.",
         """SELECT test_id, completed_date, method || ': ' || result_value || ' ' || result_unit || ' vs limit ' || spec_high
            FROM tests WHERE spec_high > 0 AND result_value > 3 * spec_high"""),
    Rule("DQ-005", "Status contradicts result vs specification", "tests", "Critical", "Accurate",
         "Recorded Pass/OOS status disagrees with the result and its specification limits (e.g. OOS reported as Pass).",
         """SELECT test_id, completed_date, 'Status ' || status || ' but result ' || result_value || ' vs spec ['
                   || spec_low || ', ' || spec_high || ']'
            FROM tests WHERE result_value IS NOT NULL AND status <>
                 CASE WHEN result_value < spec_low OR result_value > spec_high THEN 'OOS' ELSE 'Pass' END"""),
    Rule("DQ-006", "Completed test without a result", "tests", "Major", "Complete",
         "Test is closed with a status but no result value.",
         "SELECT test_id, completed_date, 'result_value is empty, status ' || status FROM tests WHERE result_value IS NULL"),
    Rule("DQ-007", "Test started on an instrument that was down", "tests", "Critical", "Consistent",
         "Test start date falls inside a logged Breakdown event for the recorded instrument: wrong instrument ID or an unlogged recovery.",
         """SELECT t.test_id, t.completed_date, t.instrument_id || ' broken down ' || e.start_date || ' to ' || e.end_date
            FROM tests t JOIN instrument_events e ON e.instrument_id = t.instrument_id AND e.event_type = 'Breakdown'
                 AND t.start_date BETWEEN e.start_date AND e.end_date"""),
    Rule("DQ-008", "Sample due date before received date", "samples", "Minor", "Consistent",
         "due_date precedes received_date, which makes on-time metrics meaningless for the sample.",
         "SELECT sample_id, received_date, 'Due ' || (received_date - due_date) || ' day(s) before received' FROM samples WHERE due_date < received_date"),
    Rule("DQ-009", "Orphan references", "tests", "Critical", "Consistent",
         "Test points at a sample or instrument that does not exist.",
         """SELECT t.test_id, t.completed_date, 'Unknown sample or instrument' FROM tests t
            WHERE t.sample_id NOT IN (SELECT sample_id FROM samples) OR t.instrument_id NOT IN (SELECT instrument_id FROM instruments)"""),
    Rule("DQ-010", "Future-dated record", "tests", "Major", "Contemporaneous",
         "Test dated after today, which cannot have happened yet.",
         "SELECT test_id, completed_date, 'Dated in the future' FROM tests WHERE completed_date > current_date"),
]
BY_ID = {r.id: r for r in RULES}
TEST_RULES = [r for r in RULES if r.table == "tests"]


def flagged_tests_sql() -> str:
    """One row per (test_id) with at least one issue, for use inside other queries."""
    return "SELECT DISTINCT record_id AS test_id FROM (" + " UNION ALL ".join(f"({r.normalized})" for r in TEST_RULES) + ")"


def _count(db: Database, sql: str) -> int:
    return int(db.run(f"SELECT count(*) FROM ({sql})").rows[0][0])


def summary(db: Database) -> dict:
    totals = {"tests": int(db.run("SELECT count(*) FROM tests").rows[0][0]), "samples": int(db.run("SELECT count(*) FROM samples").rows[0][0])}
    rules = []
    for r in RULES:
        n = _count(db, r.normalized)
        rules.append(dict(id=r.id, title=r.title, table=r.table, severity=r.severity, alcoa=r.alcoa, description=r.description,
                          count=n, total=totals[r.table], pct=round(100 * n / totals[r.table], 2), status="Fail" if n else "Pass"))
    flagged = _count(db, flagged_tests_sql())
    by_sev = {s: sum(x["count"] for x in rules if x["severity"] == s) for s in ("Critical", "Major", "Minor")}
    return dict(rules=rules, total_tests=totals["tests"], flagged_tests=flagged,
                clean_pct=round(100 * (1 - flagged / totals["tests"]), 2), issues_by_severity=by_sev,
                open_rules=sum(1 for x in rules if x["count"]))


def issues(db: Database, rule_id: str, limit: int = 200, offset: int = 0) -> dict:
    if rule_id not in BY_ID:
        raise KeyError(rule_id)
    r = BY_ID[rule_id]
    limit, offset = max(1, min(int(limit), 200)), max(0, int(offset))
    total = _count(db, r.normalized)
    res = db.run(f"SELECT * FROM ({r.normalized}) ORDER BY record_id LIMIT {limit} OFFSET {offset}")
    return dict(rule=dict(id=r.id, title=r.title, severity=r.severity, alcoa=r.alcoa, table=r.table),
                total=total, offset=offset, limit=limit, columns=["record_id", "record_date", "detail"],
                rows=[[str(v) if v is not None and not isinstance(v, (int, float, str)) else v for v in row] for row in res.rows])


def as_text(db: Database) -> str:
    """Compact report for the agent's data_quality tool."""
    s = summary(db)
    lines = [f"{s['flagged_tests']} of {s['total_tests']} test records ({100 - s['clean_pct']:.2f}%) have at least one data-quality issue."]
    for r in s["rules"]:
        if r["count"]:
            lines.append(f"{r['id']} [{r['severity']}] {r['title']}: {r['count']} {r['table']} records ({r['pct']}%)")
    lines.append("Rules with no issues: " + ", ".join(r["id"] for r in s["rules"] if not r["count"]))
    return "\n".join(lines)
