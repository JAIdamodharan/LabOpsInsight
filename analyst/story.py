"""Facts for the 'Why LabOps' story page. Every number is computed from the data, never typed in, so the story cannot drift from the dashboard."""
from __future__ import annotations

from . import dq
from .db import Database


def _one(db: Database, sql: str):
    return db.run(sql).rows


def build(db: Database, drift: dict | None = None) -> dict:
    tests, instruments, analysts = (_one(db, "SELECT count(*) FROM tests")[0][0], _one(db, "SELECT count(*) FROM instruments")[0][0], _one(db, "SELECT count(*) FROM analysts")[0][0])
    first, last = _one(db, "SELECT strftime(min(completed_date),'%b %Y'), strftime(max(completed_date),'%b %Y') FROM tests")[0]
    oos = _one(db, """SELECT round(100.0*avg((status='OOS')::int) FILTER (instrument_id='HPLC-03'),1), round(100.0*avg((status='OOS')::int) FILTER (instrument_id<>'HPLC-03'),1)
                      FROM tests WHERE method='Assay' AND strftime(completed_date,'%Y-%m')='2025-03' AND instrument_id LIKE 'HPLC-%'""")[0]
    ot = dict(_one(db, """SELECT strftime(completed_date,'%Y-%m'), round(100.0*avg((completed_date<=due_date)::int),1) FROM tests JOIN samples USING(sample_id)
                          WHERE completed_date>=start_date AND due_date>=received_date AND strftime(completed_date,'%Y-%m') IN ('2025-01','2025-02') GROUP BY 1"""))
    down = _one(db, "SELECT instrument_id, strftime(start_date,'%d %b'), strftime(end_date,'%d %b') FROM instrument_events WHERE event_type='Breakdown' AND instrument_id='LCMS-02' AND start_date>='2025-02-01' AND start_date<'2025-03-01' ORDER BY start_date LIMIT 1")
    coh = {r[0]: (r[1], r[2], r[3]) for r in _one(db, """SELECT cohort, analysts, documentation_deviations, per_analyst FROM (
        SELECT cohort, count(*) AS analysts, sum(n) AS documentation_deviations, round(avg(n),1) AS per_analyst FROM (
          SELECT a.analyst_id, CASE WHEN a.join_date>='2025-01-01' THEN 'new' ELSE 'earlier' END AS cohort, count(d.deviation_id) AS n
          FROM analysts a LEFT JOIN deviations d ON d.analyst_id=a.analyst_id AND d.category='Documentation' AND d.opened_date>='2025-04-01' GROUP BY 1,2) GROUP BY 1)""")}
    s = dq.summary(db)
    alarm = (drift or {}).get("alarms", [None])[0] if (drift or {}).get("alarms") else None
    return {
        "scope": {"tests": int(tests), "instruments": int(instruments), "analysts": int(analysts), "from": first, "to": last},
        "review": {"flagged": int(s["flagged_tests"]), "total": int(s["total_tests"]), "rules": int(sum(1 for r in s["rules"] if r["count"]))},
        "drift": {"oos_hplc03": oos[0], "oos_others": oos[1], "alarm_date": alarm["first_alarm"] if alarm else None, "instrument": alarm["instrument"] if alarm else None},
        "delivery": {"jan": ot.get("2025-01"), "feb": ot.get("2025-02"), "down": list(down[0]) if down else None},
        "training": {"new_per_analyst": coh.get("new", (0, 0, None))[2], "earlier_per_analyst": coh.get("earlier", (0, 0, None))[2], "new_analysts": coh.get("new", (0,))[0]},
    }
