"""Deterministic BI dashboard: slicers (month / region / channel) -> KPI cards, gauges, trend + projection,
combo chart, breakdowns and a channel x region change heatmap. Plain parameterised SQL, no LLM involved."""
import calendar
from datetime import date

from .db import Database

DEFAULT_MONTH = "2025-03"  # the dataset's featured period (see scripts/make_data.py)

_BASE = """
WITH items AS (SELECT order_id, sum(quantity*unit_price) AS amt, sum(quantity) AS units FROM order_items GROUP BY 1),
f AS (
  SELECT o.order_id, o.order_date, o.status, o.channel, c.region, o.customer_id, i.amt, i.units
  FROM orders o JOIN customers c USING(customer_id) JOIN items i USING(order_id)
  WHERE (?::VARCHAR IS NULL OR c.region = ?) AND (?::VARCHAR IS NULL OR o.channel = ?)
)
"""


def _f(v) -> float:
    return float(v or 0)


def _label(m: str) -> str:
    y, mo = m.split("-")
    return f"{calendar.month_abbr[int(mo)]} {y}"


def _next_months(m: str, n: int) -> list[str]:
    y, mo = map(int, m.split("-"))
    out = []
    for _ in range(n):
        mo += 1
        if mo > 12:
            y, mo = y + 1, 1
        out.append(f"{y:04d}-{mo:02d}")
    return out


def linreg(ys: list[float]) -> tuple[float, float]:
    """Least-squares slope/intercept of ys against 0..n-1."""
    n = len(ys)
    if n < 2:
        return 0.0, (ys[0] if ys else 0.0)
    mx, my = (n - 1) / 2, sum(ys) / n
    sxx = sum((i - mx) ** 2 for i in range(n))
    slope = sum((i - mx) * (y - my) for i, y in enumerate(ys)) / sxx
    return slope, my - slope * mx


def _delta(cur: float, prev: float | None, kind: str):
    if prev is None:
        return None
    if kind == "pp":
        return round(cur - prev, 2)
    return None if prev == 0 else round((cur - prev) / prev * 100, 2)


def options(db: Database) -> dict:
    col = lambda sql: [r[0] for r in db.run(sql).rows]
    return {
        "months": col("SELECT DISTINCT strftime(order_date,'%Y-%m') FROM orders ORDER BY 1"),
        "regions": col("SELECT DISTINCT region FROM customers ORDER BY 1"),
        "channels": col("SELECT DISTINCT channel FROM orders ORDER BY 1"),
    }


def build(db: Database, month: str | None = None, region: str | None = None, channel: str | None = None) -> dict:
    opt = options(db)
    month = month or (DEFAULT_MONTH if DEFAULT_MONTH in opt["months"] else opt["months"][-1])
    for val, allowed, name in [(month, opt["months"], "month"), (region, opt["regions"], "region"), (channel, opt["channels"], "channel")]:
        if val is not None and val not in allowed:
            raise ValueError(f"unknown {name} '{val}'")
    fp = [region, region, channel, channel]
    run = lambda sql, extra=(): db.run(_BASE + sql, fp + list(extra)).rows

    monthly = {}
    for m, orders, completed, cancelled, returned, revenue, customers, units in run("""
        SELECT strftime(order_date,'%Y-%m') m, count(*), count(*) FILTER (status='completed'),
               count(*) FILTER (status='cancelled'), count(*) FILTER (status='returned'),
               coalesce(sum(amt) FILTER (status='completed'),0), count(DISTINCT customer_id),
               coalesce(sum(units) FILTER (status='completed'),0)
        FROM f GROUP BY 1 ORDER BY 1"""):
        monthly[m] = dict(orders=orders, completed=completed, cancelled=cancelled, returned=returned,
                          revenue=_f(revenue), customers=customers, units=_f(units))
    months = opt["months"]
    idx = months.index(month)
    prev_m = months[idx - 1] if idx > 0 else None
    zero = dict(orders=0, completed=0, cancelled=0, returned=0, revenue=0.0, customers=0, units=0.0)
    cur, prev = monthly.get(month, zero), (monthly.get(prev_m, zero) if prev_m else None)

    def metrics(d, m):
        days = calendar.monthrange(int(m[:4]), int(m[5:]))[1]
        o, c = d["orders"], d["completed"]
        return dict(revenue=d["revenue"], orders=o, aov=d["revenue"] / c if c else 0.0, customers=d["customers"],
                    units=d["units"], revenue_per_day=d["revenue"] / days,
                    cancel_rate=100 * d["cancelled"] / o if o else 0.0, return_rate=100 * d["returned"] / o if o else 0.0,
                    completion_rate=100 * c / o if o else 0.0, loss_rate=100 * (d["cancelled"] + d["returned"]) / o if o else 0.0)

    mc, mp = metrics(cur, month), (metrics(prev, prev_m) if prev is not None else None)
    spec = [("revenue", "Revenue", "money", "up", "pct"), ("orders", "Orders", "count", "up", "pct"),
            ("aov", "Avg order value", "money", "up", "pct"), ("customers", "Active customers", "count", "up", "pct"),
            ("units", "Units sold", "count", "up", "pct"), ("revenue_per_day", "Revenue / day", "money", "up", "pct"),
            ("cancel_rate", "Cancellation rate", "pct", "down", "pp"), ("return_rate", "Return rate", "pct", "down", "pp")]
    kpis = [dict(key=k, label=lbl, fmt=fmt, good=good, value=round(mc[k], 2), prev=None if mp is None else round(mp[k], 2),
                 delta=_delta(mc[k], None if mp is None else mp[k], dk), delta_kind=dk) for k, lbl, fmt, good, dk in spec]
    gauges = [dict(key=k, label=lbl, value=round(mc[k], 1), good=good,
                   prev=None if mp is None else round(mp[k], 1)) for k, lbl, good in
              [("completion_rate", "Completion rate", "up"), ("loss_rate", "Cancelled + returned", "down")]]

    # trend + illustrative linear projection
    ms = [m for m in months if m in monthly]
    rev = [monthly[m]["revenue"] for m in ms]
    slope, icpt = linreg(rev)
    proj = _next_months(ms[-1], 2)
    trend = dict(months=ms, labels=[_label(m) for m in ms], revenue=[round(v) for v in rev],
                 orders=[monthly[m]["orders"] for m in ms],
                 aov=[round(monthly[m]["revenue"] / monthly[m]["completed"], 2) if monthly[m]["completed"] else None for m in ms],
                 trendline=[round(icpt + slope * i) for i in range(len(ms) + len(proj))],
                 projection_labels=[_label(m) for m in proj], selected=ms.index(month) if month in ms else None)

    cat = run("""SELECT p.category, sum(oi.quantity*oi.unit_price) r FROM f JOIN order_items oi USING(order_id) JOIN products p USING(product_id)
                 WHERE f.status='completed' AND strftime(f.order_date,'%Y-%m')=? GROUP BY 1 ORDER BY 2 DESC""", [month])
    chan = run("""SELECT channel, sum(amt) FROM f WHERE status='completed' AND strftime(order_date,'%Y-%m')=? GROUP BY 1 ORDER BY 2 DESC""", [month])
    reg_cur = {r: _f(v) for r, v in run("SELECT region, sum(amt) FROM f WHERE status='completed' AND strftime(order_date,'%Y-%m')=? GROUP BY 1", [month])}
    reg_prev = {r: _f(v) for r, v in run("SELECT region, sum(amt) FROM f WHERE status='completed' AND strftime(order_date,'%Y-%m')=? GROUP BY 1", [prev_m])} if prev_m else {}
    total = sum(reg_cur.values()) or 1.0
    regions = sorted(({"region": r, "revenue": v, "share": round(100 * v / total, 1),
                       "mom": _delta(v, reg_prev.get(r), "pct") if prev_m else None} for r, v in reg_cur.items()),
                     key=lambda x: -x["revenue"])

    # channel x region order change vs previous month (ignores slicers on purpose: shows where a change is concentrated)
    heat = []
    if prev_m:
        both = db.run("""SELECT o.channel, c.region, strftime(o.order_date,'%Y-%m') m, count(*) FROM orders o JOIN customers c USING(customer_id)
                         WHERE strftime(o.order_date,'%Y-%m') IN (?, ?) GROUP BY 1,2,3""", [month, prev_m]).rows
        counts = {(ch, rg, m): n for ch, rg, m, n in both}
        for ch in opt["channels"]:
            heat.append({"channel": ch, "cells": [
                {"region": rg, "cur": counts.get((ch, rg, month), 0), "prev": counts.get((ch, rg, prev_m), 0),
                 "change": _delta(counts.get((ch, rg, month), 0), counts.get((ch, rg, prev_m), 0), "pct")} for rg in opt["regions"]]})

    return dict(filters=dict(month=month, region=region, channel=channel), options=opt,
                period=dict(label=_label(month), prev_label=_label(prev_m) if prev_m else None),
                kpis=kpis, gauges=gauges, trend=trend,
                categories=[{"label": c, "value": _f(v)} for c, v in cat], channels=[{"label": c, "value": _f(v)} for c, v in chan],
                regions=regions, heatmap=dict(regions=opt["regions"], rows=heat))
