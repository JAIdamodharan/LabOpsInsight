"""Generate a synthetic, anonymised analytical/QC lab-operations DuckDB (no real company data).

Planted operational root causes (ground truth for the eval harness and tests):
  1. OOS spike: HPLC-03 (Lab B) drifts +4.5 points on Assay results 2025-03-03..2025-03-23 until it is
     re-calibrated on 2025-03-24. Assay OOS rate on that instrument jumps from ~1% to ~40%.
  2. TAT / on-time collapse: LCMS-02 (Lab C) breaks down 2025-02-10..2025-02-24. LC-MS work queues on LCMS-01,
     so Related Substances tests start 4-8 days late and on-time delivery drops in February.
  3. Training gap: from 2025-04-01 'Documentation' deviations jump, almost all raised against a cohort of
     six analysts (QC-Team-2) who joined in January 2025.

Planted data-quality defects (exact counts are written to ground_truth.json and checked by the DQ engine):
  duplicates 40, completed<start 35, missing analyst 60, unit-scale errors 25, status/result mismatch 30
  (20 OOS reported as Pass, 10 Pass reported as OOS), missing result 30, test on a broken-down instrument 20,
  sample due-before-received 15.
"""
import csv
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import duckdb

SEED = 11
START, LAST_RECEIVE, END = date(2024, 10, 1), date(2025, 6, 20), date(2025, 6, 30)

INSTRUMENTS = [
    ("HPLC-01", "HPLC", "Lab A"), ("HPLC-02", "HPLC", "Lab A"), ("HPLC-03", "HPLC", "Lab B"),
    ("HPLC-04", "HPLC", "Lab B"), ("HPLC-05", "HPLC", "Lab C"), ("LCMS-01", "LC-MS", "Lab C"),
    ("LCMS-02", "LC-MS", "Lab C"), ("GC-01", "GC", "Lab D"), ("GC-02", "GC", "Lab D"),
    ("DISS-01", "Dissolution", "Lab A"), ("DISS-02", "Dissolution", "Lab D"),
    ("KF-01", "KF Titrator", "Lab B"), ("KF-02", "KF Titrator", "Lab C"),
]
# method: (instrument types with weights, unit, spec_low, spec_high, mean, sd, run_hours range, decimals, sample weight)
METHODS = {
    "Assay": ([("HPLC", 1)], "%", 95.0, 105.0, 100.0, 1.9, (1.0, 2.5), 2, 0.35),
    "Related Substances": ([("LC-MS", .6), ("HPLC", .4)], "%", 0.0, 0.5, 0.15, 0.07, (1.5, 3.0), 3, 0.25),
    "Dissolution": ([("Dissolution", 1)], "%", 80.0, 110.0, 94.0, 4.0, (1.5, 3.5), 1, 0.15),
    "Water Content": ([("KF Titrator", 1)], "% w/w", 0.0, 2.0, 0.9, 0.35, (0.3, 0.8), 2, 0.15),
    "Residual Solvents": ([("GC", 1)], "ppm", 0.0, 5000.0, 1200.0, 600.0, (1.0, 2.5), 0, 0.10),
}
SERVICE_LINES = ["Analytical Development", "QC Release Testing", "Stability Studies", "Bioanalytical"]
DRIFT = (date(2025, 3, 3), date(2025, 3, 23))
CALIBRATION_FIX = (date(2025, 3, 24), date(2025, 3, 25))
BREAKDOWN = (date(2025, 2, 10), date(2025, 2, 24))
DOC_SURGE_FROM = date(2025, 4, 1)
DEFECTS = dict(duplicates=40, completed_before_start=35, missing_analyst=60, unit_scale_error=25,
               oos_reported_as_pass=20, pass_reported_as_oos=10, missing_result=30,
               test_on_broken_instrument=20, sample_due_before_received=15)


def weekdays(a: date, b: date):
    d = a
    while d <= b:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def pick(rnd, pairs):
    return rnd.choices([p[0] for p in pairs], [p[1] for p in pairs])[0]


def status_for(v, lo, hi):
    return "OOS" if v is not None and (v < lo or v > hi) else "Pass"


def generate(rnd: random.Random):
    projects = [(i, f"CLT-{(i - 1) % 12 + 1:02d}", SERVICE_LINES[(i - 1) % 4], START - timedelta(days=rnd.randint(30, 400)))
                for i in range(1, 31)]
    analysts = []
    for i in range(1, 25):
        team = ["QC-Team-1", "QC-Team-2", "AD-Team"][i % 3]
        analysts.append((f"AN-{i:03d}", team, date(2019, 1, 1) + timedelta(days=rnd.randint(0, 2100))))
    new_cohort = []
    for i in range(25, 31):
        aid = f"AN-{i:03d}"
        analysts.append((aid, "QC-Team-2", date(2025, 1, 6) + timedelta(days=rnd.randint(0, 14))))
        new_cohort.append(aid)
    joined = {a[0]: a[2] for a in analysts}

    events = []  # (instrument, type, start, end)
    for iid, _, _ in INSTRUMENTS:
        for m in range(10, 22):  # one PM day per instrument-month
            y, mo = 2024 + (m - 1) // 12, (m - 1) % 12 + 1
            days = [d for d in weekdays(date(y, mo, 1), date(y, mo, 28))]
            d = rnd.choice(days)
            if START <= d <= END:
                events.append((iid, "Preventive Maintenance", d, d))
    for iid in [i for i, t, _ in INSTRUMENTS if t != "LC-MS" and i != "HPLC-03"]:
        if rnd.random() < .5:
            d = rnd.choice(list(weekdays(date(2024, 11, 1), date(2025, 5, 30))))
            events.append((iid, "Calibration", d, d))
    for iid in rnd.sample([i for i, t, _ in INSTRUMENTS if t not in ("LC-MS",) and i != "HPLC-03"], 3):
        d = rnd.choice(list(weekdays(date(2024, 11, 1), date(2025, 5, 30))))
        events.append((iid, "Breakdown", d, d + timedelta(days=rnd.randint(1, 2))))
    events.append(("LCMS-02", "Breakdown", *BREAKDOWN))
    events.append(("HPLC-03", "Calibration", *CALIBRATION_FIX))
    blocked = {}  # (instrument, date) -> event type, days on which routine work is not scheduled
    for iid, et, a, b in events:
        d = a
        while d <= b:
            blocked[(iid, d)] = et
            d += timedelta(days=1)

    by_type = {}
    for iid, t, lab in INSTRUMENTS:
        by_type.setdefault(t, []).append(iid)
    lab_of = {i: lab for i, _, lab in INSTRUMENTS}

    samples, tests = [], []
    sid = tid = 0
    for r in weekdays(START, LAST_RECEIVE):
        for _ in range(rnd.randint(34, 44)):
            sid += 1
            urgent = rnd.random() < .15
            due = r + timedelta(days=3 if urgent else 5)
            samples.append([sid, rnd.randint(1, 30), rnd.choice(["Raw material", "In-process", "Finished product", "Stability"]),
                            r, due, "Urgent" if urgent else "Routine"])
            names = list(METHODS)
            n_methods = rnd.choice([2, 2, 3])
            chosen = []
            while len(chosen) < n_methods:
                m = rnd.choices(names, [METHODS[x][8] for x in names])[0]
                if m not in chosen:
                    chosen.append(m)
            for m in chosen:
                types, unit, lo, hi, mean, sd, (h0, h1), dec, _ = METHODS[m]
                itype = pick(rnd, types)
                lag = pick(rnd, [(0, .7), (1, .3)] if urgent else [(0, .5), (1, .35), (2, .15)])
                start = r + timedelta(days=lag)
                dur = pick(rnd, [(1, .5), (2, .35), (3, .15)])
                if itype == "LC-MS" and BREAKDOWN[0] <= start <= BREAKDOWN[1] and rnd.random() < .9:
                    start += timedelta(days=rnd.randint(4, 8))  # queue behind the single working LC-MS
                if rnd.random() < .06:
                    start += timedelta(days=rnd.randint(2, 5))  # ordinary random lateness

                def available(day):
                    c = [i for i in by_type[itype] if (i, day) not in blocked]
                    if itype == "LC-MS" and BREAKDOWN[0] <= day <= BREAKDOWN[1]:
                        c = [i for i in c if i != "LCMS-02"]
                    return c
                cands = available(start)
                while not cands:  # never schedule routine work on a blocked instrument
                    start += timedelta(days=1)
                    cands = available(start)
                inst = rnd.choice(cands)
                comp = start + timedelta(days=dur)
                if comp > END:
                    continue
                shift = 4.5 if (m == "Assay" and inst == "HPLC-03" and DRIFT[0] <= start <= DRIFT[1]) else 0.0
                v = max(0.0, rnd.gauss(mean + shift, sd))
                if rnd.random() < .012:  # background OOS rate
                    v = hi * (1 + rnd.uniform(.02, .15)) if (lo == 0 or rnd.random() < .5) else lo * (1 - rnd.uniform(.02, .1))
                v = round(v, dec)
                tid += 1
                avail = [a for a, j in joined.items() if j <= start]
                tests.append(dict(test_id=tid, sample_id=sid, method=m, instrument_id=inst, analyst_id=rnd.choice(avail),
                                  start_date=start, completed_date=comp, result_value=v, result_unit=unit, spec_low=lo,
                                  spec_high=hi, status=status_for(v, lo, hi), run_hours=round(rnd.uniform(h0, h1), 1)))

    devs, did = [], 0
    labs = sorted({l for _, _, l in INSTRUMENTS})
    cats = ["Documentation", "Equipment", "Sample Handling", "Process", "Data Integrity"]
    catw = [.30, .25, .20, .20, .05]

    def add_dev(d, cat, sev, analyst, lab):
        nonlocal did
        did += 1
        span = {"Minor": (5, 15), "Major": (10, 30), "Critical": (20, 45)}[sev]
        closed = d + timedelta(days=rnd.randint(*span))
        devs.append((did, rnd.randint(1, 30), analyst, lab, d, closed if closed <= END else None, cat, sev))

    for d in weekdays(START, END):
        for _ in range(pick(rnd, [(0, .45), (1, .35), (2, .15), (3, .05)])):
            avail = [a for a, j in joined.items() if j <= d]
            add_dev(d, rnd.choices(cats, catw)[0], pick(rnd, [("Minor", .7), ("Major", .25), ("Critical", .05)]),
                    rnd.choice(avail), rnd.choice(labs))
        if d >= DOC_SURGE_FROM and rnd.random() < .55:
            add_dev(d, "Documentation", pick(rnd, [("Minor", .8), ("Major", .2)]), rnd.choice(new_cohort), rnd.choice(labs))

    return projects, analysts, events, samples, tests, devs, new_cohort, lab_of


def inject_defects(rnd, tests, samples, truth):
    idx = list(range(len(tests)))
    rnd.shuffle(idx)
    take = lambda n: [idx.pop() for _ in range(n)]
    by_id = {t["test_id"]: t for t in tests}

    for i in take(DEFECTS["missing_analyst"]):
        tests[i]["analyst_id"] = None
    for i in take(DEFECTS["completed_before_start"]):
        tests[i]["completed_date"] = tests[i]["start_date"] - timedelta(days=rnd.randint(1, 3))
    # keep out rows where x1000 would still sit under the DQ-004 threshold (3x the upper limit): a documented blind spot
    zero_idx = [i for i in idx if not (tests[i]["result_value"] and tests[i]["result_value"] * 1000 > 3 * tests[i]["spec_high"])]
    zs = set(zero_idx)
    idx = zero_idx + [i for i in idx if i not in zs]  # take() pops from the end, so zeros are taken last
    for i in take(DEFECTS["unit_scale_error"]):
        t = tests[i]
        t["result_value"] = round(t["result_value"] * 1000, 2)
        t["status"] = status_for(t["result_value"], t["spec_low"], t["spec_high"])
    for i in take(DEFECTS["missing_result"]):
        tests[i]["result_value"] = None
        tests[i]["status"] = "Pass"
    # status/result mismatches: only on rows that are otherwise clean
    oos_pool = [i for i in idx if tests[i]["status"] == "OOS" and tests[i]["instrument_id"] != "HPLC-03"]
    for i in oos_pool[:DEFECTS["oos_reported_as_pass"]]:
        tests[i]["status"] = "Pass"
        idx.remove(i)
    pass_pool = [i for i in idx if tests[i]["status"] == "Pass" and tests[i]["result_value"] is not None]
    for i in pass_pool[:DEFECTS["pass_reported_as_oos"]]:
        tests[i]["status"] = "OOS"
        idx.remove(i)
    # tests recorded against the broken-down instrument
    cands = [i for i in idx if tests[i]["instrument_id"] == "LCMS-01" and BREAKDOWN[0] <= tests[i]["start_date"] <= BREAKDOWN[1]
             and tests[i]["completed_date"] <= BREAKDOWN[1]]
    chosen = cands[:DEFECTS["test_on_broken_instrument"]]
    for i in chosen:
        tests[i]["instrument_id"] = "LCMS-02"
        idx.remove(i)
    # exact duplicate rows
    nxt = max(t["test_id"] for t in tests)
    for i in idx[:DEFECTS["duplicates"]]:
        nxt += 1
        tests.append({**tests[i], "test_id": nxt})
    for s in rnd.sample(samples, DEFECTS["sample_due_before_received"]):
        s[4] = s[3] - timedelta(days=rnd.randint(1, 3))
    truth["defects"] = DEFECTS


def build(db_path: Path) -> None:
    rnd = random.Random(SEED)
    projects, analysts, events, samples, tests, devs, new_cohort, lab_of = generate(rnd)
    truth = {
        "incidents": {
            "oos_spike": {"instrument": "HPLC-03", "lab": "Lab B", "method": "Assay", "window": [str(DRIFT[0]), str(DRIFT[1])], "fixed_by_calibration": str(CALIBRATION_FIX[0])},
            "tat_collapse": {"instrument": "LCMS-02", "lab": "Lab C", "method": "Related Substances", "breakdown": [str(BREAKDOWN[0]), str(BREAKDOWN[1])], "month": "2025-02"},
            "training_gap": {"category": "Documentation", "from": str(DOC_SURGE_FROM), "team": "QC-Team-2", "analysts": new_cohort},
        }
    }
    inject_defects(rnd, tests, samples, truth)

    tmp = db_path.parent / "_tmp"
    tmp.mkdir(parents=True, exist_ok=True)

    def dump(name, header, rows):
        with open(tmp / f"{name}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            for r in rows:
                w.writerow(["\\N" if v is None else v for v in r])

    dump("projects", ["project_id", "client_code", "service_line", "start_date"], projects)
    dump("instruments", ["instrument_id", "instrument_type", "lab", "install_date"],
         [(i, t, l, date(2018, 1, 1) + timedelta(days=rnd.randint(0, 1800))) for i, t, l in INSTRUMENTS])
    dump("analysts", ["analyst_id", "team", "join_date"], analysts)
    dump("samples", ["sample_id", "project_id", "sample_type", "received_date", "due_date", "priority"], samples)
    cols = ["test_id", "sample_id", "method", "instrument_id", "analyst_id", "start_date", "completed_date", "result_value",
            "result_unit", "spec_low", "spec_high", "status", "run_hours"]
    dump("tests", cols, [[t[c] for c in cols] for t in sorted(tests, key=lambda t: t["test_id"])])
    dump("deviations", ["deviation_id", "project_id", "analyst_id", "lab", "opened_date", "closed_date", "category", "severity"], devs)
    dump("instrument_events", ["event_id", "instrument_id", "event_type", "start_date", "end_date"],
         [(i + 1, *e) for i, e in enumerate(sorted(events, key=lambda e: e[2]))])

    db_path.unlink(missing_ok=True)
    con = duckdb.connect(str(db_path))
    con.execute("""
      CREATE TABLE projects(project_id INTEGER PRIMARY KEY, client_code VARCHAR, service_line VARCHAR, start_date DATE);
      CREATE TABLE instruments(instrument_id VARCHAR PRIMARY KEY, instrument_type VARCHAR, lab VARCHAR, install_date DATE);
      CREATE TABLE analysts(analyst_id VARCHAR PRIMARY KEY, team VARCHAR, join_date DATE);
      CREATE TABLE samples(sample_id INTEGER PRIMARY KEY, project_id INTEGER, sample_type VARCHAR, received_date DATE, due_date DATE, priority VARCHAR);
      CREATE TABLE tests(test_id INTEGER PRIMARY KEY, sample_id INTEGER, method VARCHAR, instrument_id VARCHAR, analyst_id VARCHAR,
                         start_date DATE, completed_date DATE, result_value DOUBLE, result_unit VARCHAR, spec_low DOUBLE, spec_high DOUBLE,
                         status VARCHAR, run_hours DOUBLE);
      CREATE TABLE deviations(deviation_id INTEGER PRIMARY KEY, project_id INTEGER, analyst_id VARCHAR, lab VARCHAR, opened_date DATE,
                              closed_date DATE, category VARCHAR, severity VARCHAR);
      CREATE TABLE instrument_events(event_id INTEGER PRIMARY KEY, instrument_id VARCHAR, event_type VARCHAR, start_date DATE, end_date DATE);
    """)
    for name in ["projects", "instruments", "analysts", "samples", "tests", "deviations", "instrument_events"]:
        con.execute(f"COPY {name} FROM '{tmp / (name + '.csv')}' (HEADER, NULLSTR '\\N')")
    n_tests = con.execute("SELECT count(*) FROM tests").fetchone()[0]
    con.close()
    for f in tmp.iterdir():
        f.unlink()
    tmp.rmdir()
    (db_path.parent / "ground_truth.json").write_text(json.dumps(truth, indent=2))
    print(f"wrote {db_path}: {n_tests} tests, {len(samples)} samples, {len(devs)} deviations")


if __name__ == "__main__":
    build(Path(sys.argv[1] if len(sys.argv) > 1 else "data/labops.duckdb"))
