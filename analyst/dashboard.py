"""Lab-operations dashboard for an analytical/QC lab: slicers (month / lab / service line) -> KPI cards, gauges,
trends, breakdowns, instrument table, OOS heatmap and rule-based auto-insights. Parameterised SQL only; no LLM."""
import calendar

from . import dq
from .db import Database

DEFAULT_MONTH = "2025-03"
HOURS_PER_DAY = 20  # two shifts of 10 h; "available hours" = HOURS_PER_DAY x weekdays in month

_BASE = f"""
WITH flagged AS ({dq.flagged_tests_sql()}),
f AS (
  SELECT t.test_id, t.method, t.instrument_id, i.lab, t.analyst_id, t.start_date, t.completed_date, t.status, t.run_hours,
         s.received_date, s.due_date, p.service_line, strftime(t.completed_date,'%Y-%m') AS m,
         (t.completed_date >= t.start_date AND s.due_date >= s.received_date) AS valid,
         (t.test_id IN (SELECT test_id FROM flagged)) AS flag
  FROM tests t JOIN instruments i USING(instrument_id) JOIN samples s USING(sample_id) JOIN projects p USING(project_id)
  WHERE (?::VARCHAR IS NULL OR i.lab = ?) AND (?::VARCHAR IS NULL OR p.service_line = ?)
)
"""


def _f(v) -> float:
    return float(v or 0)


def _label(m: str) -> str:
    y, mo = m.split("-")
    return f"{calendar.month_abbr[int(mo)]} {y}"


def _short(m: str) -> str:
    return f"{calendar.month_abbr[int(m[5:])]} {m[2:4]}"


def _weekdays(m: str) -> int:
    y, mo = int(m[:4]), int(m[5:])
    return sum(1 for d in range(1, calendar.monthrange(y, mo)[1] + 1) if calendar.weekday(y, mo, d) < 5)


def _bounds(m: str) -> tuple[str, str]:
    y, mo = int(m[:4]), int(m[5:])
    return f"{m}-01", f"{m}-{calendar.monthrange(y, mo)[1]:02d}"


def _delta(cur: float, prev: float | None, kind: str):
    if prev is None:
        return None
    if kind == "pp":
        return round(cur - prev, 2)
    return None if prev == 0 else round((cur - prev) / prev * 100, 2)


def options(db: Database) -> dict:
    col = lambda sql: [r[0] for r in db.run(sql).rows]
    return {
        "months": col("SELECT DISTINCT strftime(completed_date,'%Y-%m') FROM tests ORDER BY 1"),
        "labs": col("SELECT DISTINCT lab FROM instruments ORDER BY 1"),
        "service_lines": col("SELECT DISTINCT service_line FROM projects ORDER BY 1"),
    }


def build(db: Database, month: str | None = None, lab: str | None = None, service_line: str | None = None) -> dict:
    opt = options(db)
    month = month or (DEFAULT_MONTH if DEFAULT_MONTH in opt["months"] else opt["months"][-1])
    for val, allowed, name in [(month, opt["months"], "month"), (lab, opt["labs"], "lab"), (service_line, opt["service_lines"], "service_line")]:
        if val is not None and val not in allowed:
            raise ValueError(f"unknown {name} '{val}'")
    fp = [lab, lab, service_line, service_line]
    run = lambda sql, extra=(): db.run(_BASE + sql, fp + list(extra)).rows

    monthly = {}
    for m, tests, oos, valid, on_time, tat, hours, flagged in run("""
        SELECT m, count(*), count(*) FILTER (status='OOS'), count(*) FILTER (valid), count(*) FILTER (valid AND completed_date <= due_date),
               coalesce(sum(completed_date - received_date) FILTER (valid), 0), coalesce(sum(run_hours), 0), count(*) FILTER (flag)
        FROM f GROUP BY 1 ORDER BY 1"""):
        monthly[m] = dict(tests=tests, oos=oos, valid=valid, on_time=on_time, tat=_f(tat), hours=_f(hours), flagged=flagged)
    dev_open_month = {m: n for m, n in db.run("""
        SELECT strftime(d.opened_date,'%Y-%m'), count(*) FROM deviations d JOIN projects p USING(project_id)
        WHERE (?::VARCHAR IS NULL OR d.lab = ?) AND (?::VARCHAR IS NULL OR p.service_line = ?) GROUP BY 1""", fp).rows}

    def open_at(end: str) -> int:
        return int(db.run("""SELECT count(*) FROM deviations d JOIN projects p USING(project_id)
            WHERE (?::VARCHAR IS NULL OR d.lab = ?) AND (?::VARCHAR IS NULL OR p.service_line = ?)
              AND d.opened_date <= ?::DATE AND (d.closed_date IS NULL OR d.closed_date > ?::DATE)""", fp + [end, end]).rows[0][0])

    months = opt["months"]
    idx = months.index(month)
    prev_m = months[idx - 1] if idx > 0 else None
    n_inst = int(db.run("SELECT count(*) FROM instruments WHERE (?::VARCHAR IS NULL OR lab = ?)", [lab, lab]).rows[0][0])
    zero = dict(tests=0, oos=0, valid=0, on_time=0, tat=0.0, hours=0.0, flagged=0)

    def metrics(m):
        d = monthly.get(m, zero)
        t, v = d["tests"], d["valid"]
        return dict(tests=t, oos_rate=100 * d["oos"] / t if t else 0.0, on_time=100 * d["on_time"] / v if v else 0.0,
                    tat=d["tat"] / v if v else 0.0, utilization=min(100.0, 100 * d["hours"] / (HOURS_PER_DAY * _weekdays(m) * n_inst)) if n_inst else 0.0,
                    deviations=dev_open_month.get(m, 0), open_deviations=open_at(_bounds(m)[1]),
                    dq_rate=100 * d["flagged"] / t if t else 0.0, clean_rate=100 - (100 * d["flagged"] / t if t else 0.0))

    mc, mp = metrics(month), (metrics(prev_m) if prev_m else None)
    spec = [("tests", "Tests completed", "count", "up", "pct"), ("oos_rate", "OOS rate", "pct", "down", "pp"),
            ("on_time", "On-time delivery", "pct", "up", "pp"), ("tat", "Avg turnaround", "days", "down", "pct"),
            ("utilization", "Instrument utilization", "pct", "up", "pp"), ("deviations", "Deviations opened", "count", "down", "pct"),
            ("open_deviations", "Open deviations", "count", "down", "pct"), ("dq_rate", "Records with DQ issues", "pct", "down", "pp")]
    kpis = [dict(key=k, label=lbl, fmt=fmt, good=good, value=round(mc[k], 2), prev=None if mp is None else round(mp[k], 2),
                 delta=_delta(mc[k], None if mp is None else mp[k], dk), delta_kind=dk) for k, lbl, fmt, good, dk in spec]
    gauges = [dict(key=k, label=lbl, value=round(mc[k], 1), good="up", prev=None if mp is None else round(mp[k], 1))
              for k, lbl in [("on_time", "On-time delivery"), ("clean_rate", "Clean records")]]

    ms = [m for m in months if m in monthly]
    series = {m: metrics(m) for m in ms}
    trend = dict(months=ms, labels=[_label(m) for m in ms], tests=[series[m]["tests"] for m in ms],
                 oos_rate=[round(series[m]["oos_rate"], 2) for m in ms], on_time=[round(series[m]["on_time"], 1) for m in ms],
                 tat=[round(series[m]["tat"], 2) for m in ms], selected=ms.index(month) if month in ms else None)

    start, end = _bounds(month)
    devcat = run_dev = db.run("""SELECT d.category, count(*) FROM deviations d JOIN projects p USING(project_id)
        WHERE (?::VARCHAR IS NULL OR d.lab = ?) AND (?::VARCHAR IS NULL OR p.service_line = ?) AND d.opened_date BETWEEN ?::DATE AND ?::DATE
        GROUP BY 1 ORDER BY 2 DESC""", fp + [start, end]).rows
    prev_dev = {}
    if prev_m:
        ps, pe = _bounds(prev_m)
        prev_dev = dict(db.run("""SELECT d.category, count(*) FROM deviations d JOIN projects p USING(project_id)
            WHERE (?::VARCHAR IS NULL OR d.lab = ?) AND (?::VARCHAR IS NULL OR p.service_line = ?) AND d.opened_date BETWEEN ?::DATE AND ?::DATE
            GROUP BY 1""", fp + [ps, pe]).rows)
    by_method = run("""SELECT method, count(*), coalesce(avg(completed_date - received_date) FILTER (valid), 0),
                              100.0 * count(*) FILTER (valid AND completed_date <= due_date) / nullif(count(*) FILTER (valid), 0)
                       FROM f WHERE m = ? GROUP BY 1 ORDER BY 3 DESC""", [month])

    prev_oos = {}
    if prev_m:
        prev_oos = {i: (n, o) for i, n, o in run("SELECT instrument_id, count(*), count(*) FILTER (status='OOS') FROM f WHERE m = ? GROUP BY 1", [prev_m])}
    inst_rows = db.run(_BASE + """
        SELECT i.instrument_id, i.lab, i.instrument_type, coalesce(a.tests, 0), coalesce(a.hours, 0), coalesce(a.oos, 0)
        FROM instruments i LEFT JOIN (SELECT instrument_id, count(*) AS tests, sum(run_hours) AS hours,
                                             count(*) FILTER (status='OOS') AS oos FROM f WHERE m = ? GROUP BY 1) a USING (instrument_id)
        WHERE (?::VARCHAR IS NULL OR i.lab = ?) ORDER BY i.instrument_id""", fp + [month, lab, lab]).rows
    wd = _weekdays(month)
    instruments = []
    for iid, ilab, itype, n, hrs, oos in inst_rows:
        rate = 100 * oos / n if n else 0.0
        pn, po = prev_oos.get(iid, (0, 0))
        instruments.append(dict(instrument=iid, lab=ilab, type=itype, tests=n, utilization=round(min(100.0, 100 * _f(hrs) / (HOURS_PER_DAY * wd)), 1),
                                oos_rate=round(rate, 1), oos_delta=round(rate - 100 * po / pn, 1) if (prev_m and pn and n) else None))

    # instrument x month OOS heatmap, last 6 months ending at the selected month (ignores slicers on purpose)
    hm = months[max(0, idx - 5): idx + 1]
    ph = ",".join("?" * len(hm))
    cnt = {(i, m): (n, o) for i, m, n, o in db.run(
        f"SELECT instrument_id, strftime(completed_date,'%Y-%m'), count(*), count(*) FILTER (status='OOS') FROM tests "
        f"WHERE strftime(completed_date,'%Y-%m') IN ({ph}) GROUP BY 1,2", hm).rows}
    heat_rows = []
    for iid, ilab, _, _, _, _ in inst_rows if not lab else db.run("SELECT instrument_id, lab, 0,0,0,0 FROM instruments ORDER BY 1").rows:
        cells = []
        for m in hm:
            n, o = cnt.get((iid, m), (0, 0))
            cells.append(dict(month=_short(m), tests=n, oos=o, rate=round(100 * o / n, 1) if n else None))
        heat_rows.append(dict(instrument=iid, lab=ilab, cells=cells))

    insights = _insights(db, month, prev_m, lab, instruments, by_method, devcat, prev_dev, mc, start, end)
    return dict(filters=dict(month=month, lab=lab, service_line=service_line), options=opt,
                period=dict(label=_label(month), prev_label=_label(prev_m) if prev_m else None),
                kpis=kpis, gauges=gauges, trend=trend,
                deviation_categories=[{"label": c, "value": n, "prev": prev_dev.get(c, 0)} for c, n in devcat],
                methods=[dict(label=m, tests=n, tat=round(_f(t), 2), on_time=None if o is None else round(_f(o), 1)) for m, n, t, o in by_method],
                instruments=instruments, heatmap=dict(months=[_short(m) for m in hm], rows=heat_rows), insights=insights)


def _insights(db, month, prev_m, lab, instruments, by_method, devcat, prev_dev, mc, start, end) -> list[dict]:
    out = []
    fleet = mc["oos_rate"]
    for i in sorted(instruments, key=lambda x: -x["oos_rate"]):
        if i["tests"] >= 30 and i["oos_rate"] >= max(10.0, 4 * fleet):
            out.append(dict(level="bad", text=f"{i['instrument']} ({i['lab']}) OOS rate is {i['oos_rate']}% vs {fleet:.1f}% overall: check calibration and system suitability.",
                            ask=f"Why was the OOS rate on {i['instrument']} so high in {_label(month)}?"))
            break
    for m, n, tat, ot in by_method:
        if ot is not None and n >= 50 and _f(ot) < 85:
            out.append(dict(level="bad", text=f"{m} on-time delivery is only {_f(ot):.0f}% ({n} tests, avg turnaround {_f(tat):.1f} days).",
                            ask=f"Why did {m} on-time delivery drop in {_label(month)}?"))
            break
    for r in db.run("""SELECT instrument_id, start_date, end_date FROM instrument_events WHERE event_type = 'Breakdown'
                       AND start_date <= ?::DATE AND end_date >= ?::DATE AND (end_date - start_date) >= 3""", [end, start]).rows:
        out.append(dict(level="warn", text=f"{r[0]} was down from {r[1]} to {r[2]} ({(r[2] - r[1]).days + 1} days): expect queueing on its work.",
                        ask=f"What was the impact of the {r[0]} breakdown in {_label(month)}?"))
    for c, n in devcat:
        p = prev_dev.get(c, 0)
        if prev_m and n >= 8 and n >= 2 * max(p, 1):
            out.append(dict(level="warn", text=f"{c} deviations rose from {p} to {n} vs {_label(prev_m)}.",
                            ask=f"Why did {c} deviations increase in {_label(month)}, and who raised them?"))
            break
    s = dq.summary(db)
    crit = s["issues_by_severity"]["Critical"]
    if s["flagged_tests"]:
        out.append(dict(level="info", text=f"{s['flagged_tests']} test records ({100 - s['clean_pct']:.1f}%) need data review, {crit} critical issues. Open the Data Review tab.", ask=None))
    return out
