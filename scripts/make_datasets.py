"""Generate a set of synthetic, anonymised workbooks/CSVs for testing uploads (no real data).

    .venv/bin/python scripts/make_datasets.py            # writes sample_data/*.xlsx and *.csv

Every file is deterministic (fixed seed). Shapes vary on purpose: multi-sheet, wide, long, messy (numbers stored as text, duplicates,
blank cells, inconsistent spellings, outliers), plus several lab and non-lab domains. Each stays inside the app's upload limits.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent.parent / "sample_data"
RNG = np.random.default_rng(2025)


def dates(n, start="2024-01-01", days=540):
    return pd.to_datetime(start) + pd.to_timedelta(RNG.integers(0, days, n), unit="D")


def pick(options, n, p=None):
    return RNG.choice(options, n, p=p)


def save(name, sheets):
    OUT.mkdir(exist_ok=True)
    path = OUT / name
    if name.endswith(".csv"):
        next(iter(sheets.values())).to_csv(path, index=False)
    else:
        with pd.ExcelWriter(path, engine="openpyxl") as w:
            for sheet, df in sheets.items():
                df.to_excel(w, sheet_name=sheet, index=False)
    return path


def stability_study():
    batches = pd.DataFrame({"batch_id": [f"B{2400 + i}" for i in range(12)], "product": pick(["Drug A 10mg", "Drug A 25mg", "Drug B 5mg"], 12),
                            "site": pick(["Site 1", "Site 2"], 12), "manufactured": dates(12, "2023-06-01", 300).date})
    rows = []
    for b in batches.itertuples():
        for tp in (0, 1, 3, 6, 9, 12, 18, 24):
            for cond in ("25C/60RH", "40C/75RH"):
                decay = tp * (0.05 if cond == "25C/60RH" else 0.14)
                rows.append((b.batch_id, tp, cond, round(100.2 - decay + RNG.normal(0, .5), 2), round(0.08 + tp * (0.012 if cond == "25C/60RH" else 0.03) + abs(RNG.normal(0, .02)), 3),
                             round(94 - tp * .05 + RNG.normal(0, 2), 1)))
    res = pd.DataFrame(rows, columns=["batch_id", "month", "condition", "assay_pct", "total_impurities_pct", "dissolution_pct"])
    return {"batches": batches, "results": res}


def hplc_run_log():
    n = 3000
    inst = pick(["HPLC-A1", "HPLC-A2", "HPLC-B1", "HPLC-B2"], n)
    d = pd.DataFrame({"run_id": np.arange(1, n + 1), "run_date": dates(n).date, "instrument": inst, "column_lot": pick(["CL-101", "CL-102", "CL-103", "CL-104"], n),
                      "analyst": pick(["AN-01", "AN-02", "AN-03", "AN-04", "AN-05"], n),
                      "retention_min": np.round(RNG.normal(6.2, 0.12, n), 3), "plate_count": RNG.integers(7000, 12000, n),
                      "tailing": np.round(RNG.normal(1.15, 0.08, n), 2), "area": np.round(RNG.normal(1_250_000, 40_000, n)),
                      "sst_pass": pick(["Yes", "No"], n, [.96, .04])})
    d.loc[d["instrument"] == "HPLC-B2", "tailing"] += 0.35 * (d["run_date"] > pd.Timestamp("2025-02-01").date())[d["instrument"] == "HPLC-B2"]
    return {"runs": d}


def sales_orders():
    n = 6000
    cat = pick(["Reagents", "Consumables", "Columns", "Standards", "Glassware"], n, [.3, .3, .15, .15, .1])
    price = {"Reagents": 120, "Consumables": 45, "Columns": 480, "Standards": 220, "Glassware": 60}
    qty = RNG.integers(1, 20, n)
    d = pd.DataFrame({"order_id": 100000 + np.arange(n), "order_date": dates(n).date, "region": pick(["North", "South", "East", "West"], n),
                      "category": cat, "quantity": qty, "unit_price": [round(price[c] * RNG.uniform(.85, 1.15), 2) for c in cat],
                      "customer_type": pick(["Academic", "Biotech", "Pharma", "CRO"], n, [.2, .3, .3, .2])})
    d["revenue"] = (d["quantity"] * d["unit_price"]).round(2)
    return {"orders": d, "targets": pd.DataFrame({"region": ["North", "South", "East", "West"], "annual_target": [2.1e6, 1.6e6, 1.9e6, 1.4e6]})}


def hr_attrition():
    n = 900
    d = pd.DataFrame({"employee_id": [f"E{1000 + i}" for i in range(n)], "department": pick(["QC", "R&D", "Manufacturing", "QA", "Admin"], n),
                      "age": RNG.integers(22, 62, n), "tenure_years": np.round(RNG.gamma(2.2, 2.0, n), 1),
                      "salary_band": pick(["B1", "B2", "B3", "B4"], n, [.3, .35, .25, .1]), "overtime_hours_month": RNG.integers(0, 40, n),
                      "satisfaction": RNG.integers(1, 6, n)})
    risk = 0.05 + 0.04 * (d["overtime_hours_month"] > 25) + 0.06 * (d["satisfaction"] <= 2) + 0.03 * (d["tenure_years"] < 1.5)
    d["left_company"] = np.where(RNG.random(n) < risk, "Yes", "No")
    return {"employees": d}


def clinical_vitals():
    n = 400
    pts = pd.DataFrame({"patient_id": [f"P{i:04d}" for i in range(n)], "arm": pick(["Placebo", "Low dose", "High dose"], n), "age": RNG.integers(18, 80, n),
                        "sex": pick(["F", "M"], n), "site": pick(["S01", "S02", "S03", "S04"], n)})
    rows = []
    for p in pts.itertuples():
        eff = {"Placebo": 0, "Low dose": -4, "High dose": -9}[p.arm]
        for wk in (0, 4, 8, 12):
            rows.append((p.patient_id, wk, round(148 + eff * wk / 12 + RNG.normal(0, 8), 1), round(92 + eff * wk / 24 + RNG.normal(0, 5), 1), int(RNG.normal(74, 8))))
    return {"patients": pts, "visits": pd.DataFrame(rows, columns=["patient_id", "week", "systolic_bp", "diastolic_bp", "heart_rate"])}


def reagent_inventory():
    n = 500
    rec = dates(n, "2023-01-01", 800)
    d = pd.DataFrame({"item_code": [f"R{i:04d}" for i in range(n)], "item": pick(["Acetonitrile", "Methanol", "Formic acid", "Buffer salt", "Reference std", "TFA", "Water HPLC"], n),
                      "lot": [f"L{RNG.integers(1000, 9999)}" for _ in range(n)], "received": rec.date, "expiry": (rec + pd.to_timedelta(RNG.integers(90, 900, n), unit="D")).date,
                      "qty_on_hand": np.round(RNG.gamma(2, 3, n), 1), "unit": pick(["L", "g", "mL"], n), "storage": pick(["Ambient", "2-8C", "-20C"], n, [.5, .35, .15])})
    return {"inventory": d}


def calibration_records():
    n = 800
    d = pd.DataFrame({"record_id": np.arange(n), "instrument": pick([f"BAL-{i:02d}" for i in range(1, 9)], n), "calibrated_on": dates(n).date,
                      "standard_mg": pick([10, 100, 1000], n), "measured_mg": np.nan, "tolerance_mg": pick([0.02, 0.05, 0.1], n)})
    d["measured_mg"] = (d["standard_mg"] + RNG.normal(0, d["tolerance_mg"] / 2.6, n)).round(3)
    d["within_tolerance"] = np.where((d["measured_mg"] - d["standard_mg"]).abs() <= d["tolerance_mg"], "Pass", "Fail")
    return {"calibration": d}


def customer_feedback():
    n = 1500
    d = pd.DataFrame({"ticket_id": np.arange(n), "opened": dates(n).date, "channel": pick(["Email", "Phone", "Portal"], n), "topic": pick(["Delay", "Report error", "Billing", "Sample query", "Other"], n),
                      "resolution_days": np.round(RNG.gamma(2, 1.8, n), 1), "csat": RNG.integers(1, 6, n)})
    return {"feedback": d}


def bioassay_plates():
    rows = []
    for plate in range(1, 31):
        for r in "ABCDEFGH":
            for c in range(1, 13):
                conc = 10 ** (-(c - 1) / 2)
                rows.append((f"PL{plate:02d}", r, c, round(conc, 5), round(100 / (1 + (0.01 / conc) ** 1.1) + RNG.normal(0, 3), 2)))
    return {"plates": pd.DataFrame(rows, columns=["plate", "row", "col", "concentration", "response_pct"])}


def environmental_monitoring():
    n = 2500
    d = pd.DataFrame({"sample_time": pd.to_datetime("2025-01-01") + pd.to_timedelta(np.sort(RNG.integers(0, 150 * 24 * 60, n)), unit="m"),
                      "room": pick(["Cleanroom A", "Cleanroom B", "Filling", "Weighing"], n), "particles_0_5um": RNG.poisson(2800, n), "particles_5um": RNG.poisson(12, n),
                      "temp_c": np.round(RNG.normal(21, .6, n), 1), "humidity_pct": np.round(RNG.normal(45, 3, n), 1)})
    d.loc[d.sample(40, random_state=1).index, "particles_0_5um"] *= 4
    return {"readings": d}


def batch_release():
    n = 700
    d = pd.DataFrame({"batch": [f"BR{i:04d}" for i in range(n)], "product": pick(["Tablet 10mg", "Tablet 25mg", "Capsule 50mg", "Injection 1mL"], n), "site": pick(["Plant 1", "Plant 2"], n),
                      "yield_pct": np.round(RNG.normal(96, 2.5, n), 1), "release_days": RNG.integers(3, 30, n), "deviations": RNG.poisson(0.6, n), "released": pick(["Released", "Rejected", "On hold"], n, [.93, .03, .04])})
    return {"batches": d}


def deviation_capa():
    n = 600
    opened = dates(n)
    d = pd.DataFrame({"dev_id": [f"DEV-{i:04d}" for i in range(n)], "opened": opened.date, "category": pick(["Documentation", "Equipment", "Process", "Material", "Environment"], n),
                      "severity": pick(["Minor", "Major", "Critical"], n, [.6, .3, .1]), "owner_dept": pick(["QC", "QA", "Production", "Warehouse"], n)})
    d["days_to_close"] = np.round(RNG.gamma(2.5, 8, n) * d["severity"].map({"Minor": .6, "Major": 1.2, "Critical": 1.8}), 0)
    d["capa_required"] = np.where(d["severity"] != "Minor", "Yes", pick(["Yes", "No"], n, [.15, .85]))
    return {"deviations": d}


def messy_defects():
    d = sales_orders()["orders"].head(1500).copy()
    d["quantity"] = d["quantity"].astype(object)
    d.loc[d.sample(60, random_state=2).index, "quantity"] = "N/A"
    d["unit_price"] = d["unit_price"].astype(str)                                        # numbers stored as text
    d.loc[d.sample(120, random_state=3).index, "region"] = pick(["north", "NORTH ", "Nroth", "N."], 120)  # inconsistent spellings
    d.loc[d.sample(150, random_state=4).index, "customer_type"] = None                   # blanks
    d.loc[d.sample(8, random_state=5).index, "revenue"] = d["revenue"].max() * 50        # outliers
    d = pd.concat([d, d.sample(40, random_state=6)], ignore_index=True)                  # duplicate rows
    return {"messy_orders": d}


def large_timeseries():
    n = 60_000
    t = pd.date_range("2025-01-01", periods=n, freq="min")
    d = pd.DataFrame({"timestamp": t, "sensor": pick(["S1", "S2", "S3"], n), "value": np.round(20 + 3 * np.sin(np.arange(n) / 600) + RNG.normal(0, .4, n), 3)})
    return {"sensor": d}


def wide_columns():
    n, k = 300, 80
    d = pd.DataFrame(RNG.normal(0, 1, (n, k)).round(3), columns=[f"feature_{i:02d}" for i in range(k)])
    d.insert(0, "sample", [f"W{i:03d}" for i in range(n)])
    d["group"] = pick(["Control", "Treated"], n)
    return {"wide": d}


FILES = {
    "stability_study.xlsx": stability_study, "hplc_run_log.xlsx": hplc_run_log, "sales_orders.xlsx": sales_orders, "hr_attrition.xlsx": hr_attrition,
    "clinical_vitals.xlsx": clinical_vitals, "reagent_inventory.xlsx": reagent_inventory, "balance_calibration.xlsx": calibration_records,
    "customer_feedback.csv": customer_feedback, "bioassay_plates.xlsx": bioassay_plates, "environmental_monitoring.xlsx": environmental_monitoring,
    "batch_release.xlsx": batch_release, "deviation_capa.xlsx": deviation_capa, "messy_orders.xlsx": messy_defects, "sensor_60k_rows.csv": large_timeseries,
    "wide_80_features.xlsx": wide_columns,
}


def main(argv=None) -> None:
    for name, fn in FILES.items():
        p = save(name, fn())
        print(f"{name:34s} {p.stat().st_size / 1e3:9.0f} KB")
    print(f"\nWrote {len(FILES)} files to {OUT}")


if __name__ == "__main__":
    main(sys.argv[1:])
