"""Prediction module: short-term forecasts and test-level risk models for the lab dataset.

Honest by construction:
  * Risk models are validated on a time split (train on the earlier 75% of completions, test on the later 25%), never on shuffled rows.
  * Features only use information available when a test starts (no result values, no completion data of the test itself).
    Instrument history features look strictly backwards (completions before the test's start date).
  * Forecasts pick the best of three simple models by one-step rolling-origin backtesting and report 80% intervals from backtest errors.
    With only ~9 months of history these intervals are wide on purpose.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler

from .db import Database

SEED = 7
HORIZON = 3
SPLIT_Q = 0.75
CAT = ["method", "instrument_type", "instrument_id", "lab", "team", "priority", "sample_type", "service_line"]
NUM_COMMON = ["run_hours", "dow", "inst_oos_30d", "inst_load_7d"]
NUM_LATE = ["queue_days", "inst_down"]
FEATURE_LABEL = {
    "method": "Test method", "instrument_type": "Instrument type", "instrument_id": "Instrument", "lab": "Lab", "team": "Analyst team",
    "priority": "Priority", "sample_type": "Sample type", "service_line": "Service line", "run_hours": "Run time (h)", "dow": "Day of week",
    "inst_oos_30d": "Instrument OOS rate, prior 30 d", "inst_load_7d": "Instrument load, prior 7 d", "queue_days": "Days waiting before start",
    "inst_down": "Instrument was in a breakdown",
}
# A test-level OOS classifier was tried first: on a time split it scored ROC AUC ~0.46 (no better than chance), because OOS results are
# rare (<2%) and almost entirely explained by one instrument drift that cannot be learned from the earlier months. It is replaced by
# the drift monitor below (an EWMA control chart), which does detect that incident. Late delivery, by contrast, is clearly predictable.
TARGETS = {
    "late": {"label": "Report delivered after its due date", "num": NUM_COMMON + NUM_LATE},
}

_SQL = """
SELECT t.test_id, t.method, t.instrument_id, i.instrument_type, i.lab, a.team, t.start_date, t.completed_date, t.status, t.run_hours,
       s.priority, s.sample_type, s.received_date, s.due_date, p.service_line
FROM tests t
JOIN samples s USING (sample_id)
JOIN projects p USING (project_id)
LEFT JOIN instruments i USING (instrument_id)
LEFT JOIN analysts a ON a.analyst_id = t.analyst_id
WHERE t.start_date IS NOT NULL AND t.completed_date IS NOT NULL AND t.status IS NOT NULL
"""


def _rolling(df: pd.DataFrame, flag: pd.Series, window: int, rate: bool, prior: float = 0.0, strength: float = 10.0) -> np.ndarray:
    """For each row: stats over the same instrument's completions in the `window` days *before* the row's start date."""
    out = np.zeros(len(df))
    days0 = df["completed_date"].min().normalize() - pd.Timedelta(days=window + 2)
    for inst, idx in df.groupby("instrument_id").indices.items():
        sub = df.iloc[idx]
        n_days = int((df["completed_date"].max() - days0).days) + 3
        pos = (sub["completed_date"] - days0).dt.days.to_numpy()
        n = np.bincount(pos, minlength=n_days).astype(float)
        f = np.bincount(pos, weights=flag.iloc[idx].to_numpy(float), minlength=n_days)
        cn, cf = np.concatenate([[0], np.cumsum(n)]), np.concatenate([[0], np.cumsum(f)])
        s = (sub["start_date"] - days0).dt.days.to_numpy()          # window = completions on days [s-window, s-1]
        lo, hi = np.clip(s - window, 0, n_days), np.clip(s, 0, n_days)
        cnt, bad = cn[hi] - cn[lo], cf[hi] - cf[lo]
        out[idx] = (bad + prior * strength) / (cnt + strength) if rate else cnt
    return out


def build_frame(db: Database) -> pd.DataFrame:
    df = db.con.cursor().execute(_SQL).df()
    for c in ("start_date", "completed_date", "received_date", "due_date"):
        df[c] = pd.to_datetime(df[c])
    df = df.drop_duplicates("test_id").sort_values("completed_date").reset_index(drop=True)
    df = df[df["completed_date"] >= df["start_date"]].reset_index(drop=True)             # impossible dates are a data-review finding, not a training signal
    df["oos"] = (df["status"] == "OOS").astype(int)
    df["late"] = (df["completed_date"] > df["due_date"]).astype(int)
    df["dow"] = df["start_date"].dt.dayofweek
    df["queue_days"] = (df["start_date"] - df["received_date"]).dt.days.clip(lower=0).fillna(0)
    df["run_hours"] = df["run_hours"].fillna(df["run_hours"].median())
    base = float(df["oos"].mean())
    df["inst_oos_30d"] = _rolling(df, df["oos"], 30, True, prior=base)
    df["inst_load_7d"] = _rolling(df, df["oos"], 7, False)
    ev = db.con.cursor().execute("SELECT instrument_id, start_date, end_date FROM instrument_events WHERE lower(event_type) LIKE '%break%'").df()
    df["inst_down"] = 0
    for r in ev.itertuples():
        m = (df["instrument_id"] == r.instrument_id) & (df["start_date"] >= pd.Timestamp(r.start_date)) & (df["start_date"] <= pd.Timestamp(r.end_date))
        df.loc[m, "inst_down"] = 1
    for c in CAT:
        df[c] = df[c].fillna("Unknown").astype(str)
    return df


def _pipe(kind: str, num: list[str]) -> Pipeline:
    if kind == "gbm":
        pre = ColumnTransformer([("c", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1), CAT)], remainder="passthrough")
        cat_mask = [True] * len(CAT) + [False] * len(num)
        return Pipeline([("pre", pre), ("m", HistGradientBoostingClassifier(max_depth=4, learning_rate=0.06, max_iter=160, categorical_features=cat_mask, random_state=SEED))])
    pre = ColumnTransformer([("c", OneHotEncoder(handle_unknown="ignore"), CAT), ("n", StandardScaler(), num)])
    return Pipeline([("pre", pre), ("m", LogisticRegression(max_iter=400))])


def _fit_eval(df: pd.DataFrame, target: str) -> dict:
    num = TARGETS[target]["num"]
    X, y = df[CAT + num], df[target]
    cut = df["completed_date"].quantile(SPLIT_Q)
    tr, te = (df["completed_date"] <= cut).to_numpy(), (df["completed_date"] > cut).to_numpy()
    out = {"target": target, "label": TARGETS[target]["label"], "train_rows": int(tr.sum()), "test_rows": int(te.sum()),
           "train_until": str(cut.date()), "base_rate": round(float(y[te].mean()), 4), "models": {}}
    best, best_ap, best_model = None, -1.0, None
    for kind, name in (("logit", "Logistic regression"), ("gbm", "Gradient boosting")):
        m = _pipe(kind, num).fit(X[tr], y[tr])
        p = m.predict_proba(X[te])[:, 1]
        auc = float(roc_auc_score(y[te], p)) if y[te].nunique() > 1 else float("nan")
        ap = float(average_precision_score(y[te], p)) if y[te].nunique() > 1 else float("nan")
        k = max(1, len(p) // 10)
        top = float(y[te].to_numpy()[np.argsort(-p)[:k]].mean())
        out["models"][name] = {"roc_auc": round(auc, 3), "pr_auc": round(ap, 3), "top_decile_rate": round(top, 3),
                               "lift": round(top / max(out["base_rate"], 1e-9), 1)}
        if ap > best_ap:
            best, best_ap, best_model = name, ap, m
    out["best"] = best
    # which inputs matter: permutation importance of the winning model on the held-out period
    rng = np.random.default_rng(SEED)
    sub = rng.choice(np.flatnonzero(te), size=min(3000, int(te.sum())), replace=False)
    imp = permutation_importance(best_model, X.iloc[sub], y.iloc[sub], scoring="average_precision", n_repeats=3, random_state=SEED)
    feats = CAT + num
    rows = sorted(({"feature": FEATURE_LABEL[f], "importance": round(float(v), 4)} for f, v in zip(feats, imp.importances_mean)), key=lambda r: -r["importance"])
    out["importance"] = [r for r in rows if r["importance"] > 0][:8]
    final = _pipe("gbm" if best == "Gradient boosting" else "logit", num).fit(X, y)       # refit on everything for scoring the present
    return out, final


@dataclass
class Bundle:
    frame: pd.DataFrame
    models: dict
    info: dict


def train(db: Database) -> Bundle:
    df = build_frame(db)
    info, models = {}, {}
    for t in TARGETS:
        info[t], models[t] = _fit_eval(df, t)
    last = df["completed_date"].max()
    recent = df[df["completed_date"] > last - pd.Timedelta(days=30)]
    for t in TARGETS:
        X = recent[CAT + TARGETS[t]["num"]]
        p = models[t].predict_proba(X)[:, 1]
        g = recent.assign(p=p).groupby(["instrument_id", "lab", "instrument_type"]).agg(risk=("p", "mean"), tests=("p", "size"), actual=(t, "mean")).reset_index()
        info[t]["instruments"] = [{"instrument": r.instrument_id, "lab": r.lab, "type": r.instrument_type, "risk": round(float(r.risk), 4), "actual": round(float(r.actual), 4), "tests": int(r.tests)}
                                  for r in g.sort_values("risk", ascending=False).itertuples()]
    info["as_of"] = str(last.date())
    info["options"] = {"methods": sorted(df["method"].unique()), "instruments": sorted(df["instrument_id"].unique()), "priorities": sorted(df["priority"].unique())}
    return Bundle(df, models, info)


def score(bundle: Bundle, method: str, instrument: str, priority: str, queue_days: float = 1.0) -> dict:
    """Probability for a hypothetical test starting now: typical values for everything else, current instrument state."""
    o = bundle.info["options"]
    if method not in o["methods"] or instrument not in o["instruments"] or priority not in o["priorities"]:
        raise ValueError("unknown method, instrument or priority")
    df = bundle.frame
    last = df["completed_date"].max()
    inst = df[df["instrument_id"] == instrument]
    recent = inst[inst["completed_date"] > last - pd.Timedelta(days=30)]
    row = {c: (inst[c].mode().iat[0] if len(inst) else df[c].mode().iat[0]) for c in CAT}
    row.update(method=method, instrument_id=instrument, priority=priority)
    row["run_hours"] = float(df.loc[df["method"] == method, "run_hours"].median())
    row["queue_days"] = float(max(0.0, min(queue_days, 60.0)))
    row.update(dow=2, inst_down=0, inst_oos_30d=float(recent["oos"].mean()) if len(recent) else float(df["oos"].mean()),
               inst_load_7d=float(len(inst[inst["completed_date"] > last - pd.Timedelta(days=7)])))
    out = {}
    for t in TARGETS:
        X = pd.DataFrame([row])[CAT + TARGETS[t]["num"]]
        out[t] = round(float(bundle.models[t].predict_proba(X)[:, 1][0]), 4)
    out["baseline"] = {t: round(float(df[t].mean()), 4) for t in TARGETS}
    return out


# ------------------------------------------------------------------ drift early warning (EWMA control chart)
LAMBDA, SIGMA_LIMIT, MIN_TESTS, MIN_RUN = 0.1, 3.0, 60, 8
LIMIT = SIGMA_LIMIT * np.sqrt(LAMBDA / (2 - LAMBDA))                   # steady-state EWMA control limit, in robust standard deviations


def drift(db: Database) -> dict:
    """EWMA control chart per instrument and method on standardised results, in completion order.
    A sustained shift of the running average away from zero is flagged long before it turns into a run of OOS results."""
    df = db.con.cursor().execute("""SELECT t.instrument_id, t.method, t.completed_date AS d, t.test_id, t.result_value AS v FROM tests t
                                    WHERE t.result_value IS NOT NULL AND t.completed_date IS NOT NULL AND t.instrument_id IS NOT NULL""").df()
    df["d"] = pd.to_datetime(df["d"])
    df = df.drop_duplicates("test_id").sort_values(["d", "test_id"])
    med = df.groupby("method")["v"].transform("median")
    mad = df.groupby("method")["v"].transform(lambda s: 1.4826 * float(np.median(np.abs(s - s.median()))) or 1.0)
    df["z"] = ((df["v"] - med) / mad).clip(-4, 4)                      # robust and clipped, so unit-scale data errors cannot dominate
    last = df["d"].max()
    found = []
    for (inst, method), g in df.groupby(["instrument_id", "method"]):
        if len(g) < MIN_TESTS:
            continue
        z = g["z"].to_numpy()
        e = np.empty(len(z)); prev = 0.0
        for i, x in enumerate(z):
            prev = LAMBDA * x + (1 - LAMBDA) * prev; e[i] = prev
        flag = np.abs(e) > LIMIT
        idx = np.flatnonzero(flag)
        if not len(idx):
            continue
        runs = [r for r in np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1) if len(r) >= MIN_RUN]
        if not runs:
            continue
        run = max(runs, key=lambda r: np.abs(e[r]).max())
        peak = float(e[run][np.abs(e[run]).argmax()])
        days = g["d"].to_numpy()
        found.append(({"instrument": inst, "method": method, "first_alarm": str(pd.Timestamp(days[run[0]]).date()), "last_alarm": str(pd.Timestamp(days[run[-1]]).date()),
                       "flagged_tests": int(len(run)), "peak_shift_sd": round(peak, 2), "direction": "up" if peak > 0 else "down",
                       "active": bool((last - pd.Timestamp(days[run[-1]])).days <= 7), "tests": int(len(g))}, g["d"], e))
    found.sort(key=lambda t: -abs(t[0]["peak_shift_sd"]))
    chart = None
    if found:
        a, d, e = found[0]
        step = max(1, len(e) // 160)
        chart = {"instrument": a["instrument"], "method": a["method"], "limit": round(float(LIMIT), 3),
                 "points": [{"d": str(pd.Timestamp(d.iloc[i]).date()), "ewma": round(float(e[i]), 3)} for i in range(0, len(e), step)]}
    return {"alarms": [a for a, _, _ in found[:10]], "chart": chart, "settings": {"lambda": LAMBDA, "sigma_limit": SIGMA_LIMIT, "min_tests": MIN_TESTS, "min_run": MIN_RUN}}


# ------------------------------------------------------------------ forecasting
def _series(df: pd.DataFrame) -> pd.DataFrame:
    d = df.assign(month=df["completed_date"].dt.to_period("M"))
    g = d.groupby("month").agg(tests=("test_id", "size"), oos_rate=("oos", "mean"), on_time=("late", lambda s: 1 - s.mean()),
                               tat=("completed_date", "size"))
    d["tat_days"] = (d["completed_date"] - d["received_date"]).dt.days
    g["tat"] = d.groupby("month")["tat_days"].mean()
    g["oos_rate"] *= 100
    g["on_time"] *= 100
    last = df["completed_date"].max()
    thin = len(g) > 4 and g["tests"].iloc[-1] < 0.75 * g["tests"].iloc[-4:-1].median()
    if (last + pd.offsets.MonthEnd(0) - last).days > 5 or thin:         # drop a final month that is clearly incomplete (ends early or far less volume)
        g = g.iloc[:-1]
    return g


def _predict(kind: str, y: np.ndarray, steps: int) -> np.ndarray:
    n = len(y)
    if kind == "naive":
        return np.repeat(y[-1], steps)
    if kind == "mean":
        return np.repeat(y.mean(), steps)
    k = min(6, n)                                                      # linear trend on the most recent months
    b, a = np.polyfit(np.arange(k), y[-k:], 1)
    return a + b * np.arange(k, k + steps)


def forecast_series(y: list[float], steps: int = HORIZON) -> dict:
    y = np.asarray(y, float)
    kinds = ["naive", "mean", "trend"]
    start = max(4, len(y) // 2)
    err = {k: [] for k in kinds}
    for i in range(start, len(y)):
        for k in kinds:
            err[k].append(y[i] - _predict(k, y[:i], 1)[0])
    mae = {k: float(np.mean(np.abs(e))) if e else float("nan") for k, e in err.items()}
    best = min(kinds, key=lambda k: mae[k] if mae[k] == mae[k] else 1e18)
    sd = float(np.std(err[best], ddof=1)) if len(err[best]) > 2 else float(np.std(y, ddof=1))
    fc = _predict(best, y, steps)
    z = 1.28                                                           # 80% interval
    return {"model": {"naive": "Last value", "mean": "Average", "trend": "Linear trend (last 6 months)"}[best],
            "backtest_mae": round(mae[best], 3), "naive_mae": round(mae["naive"], 3),
            "forecast": [{"value": round(float(v), 3), "lo": round(float(v - z * sd * np.sqrt(1 + i * .25)), 3), "hi": round(float(v + z * sd * np.sqrt(1 + i * .25)), 3)}
                         for i, v in enumerate(fc)]}


def forecast(df: pd.DataFrame, steps: int = HORIZON) -> dict:
    g = _series(df)
    months = [str(m) for m in g.index]
    nxt = [str(g.index[-1] + i + 1) for i in range(steps)]
    meta = {"tests": ("Tests completed per month", "count", 0), "oos_rate": ("OOS rate", "%", 0), "on_time": ("On-time delivery", "%", 0), "tat": ("Average turnaround", "days", 0)}
    out = {"months": months, "future": nxt, "series": {}}
    for k, (label, unit, _) in meta.items():
        y = [float(v) for v in g[k]]
        f = forecast_series(y, steps)
        if unit == "%":
            for p in f["forecast"]:
                p["lo"], p["hi"] = max(0.0, p["lo"]), min(100.0, p["hi"])
                p["value"] = min(100.0, max(0.0, p["value"]))
        out["series"][k] = {"label": label, "unit": unit, "history": [round(v, 3) for v in y], **f}
    return out


class Cache:
    """Trains once per database and reuses the result; the lock stops two requests training at the same time."""
    def __init__(self):
        self._lock, self._items = threading.Lock(), {}

    def get(self, key, db: Database) -> Bundle:
        with self._lock:
            if key not in self._items:
                self._items[key] = train(db)
            return self._items[key]
