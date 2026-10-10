"""Turn a guarded SQL result plus a model-chosen chart request into a validated, JSON-safe chart spec.

The model never passes data, only the SQL and column names, so a chart can't show numbers that
didn't come from the database. The browser renders the spec (Chart.js); the model is told what was drawn.
"""
from datetime import date, datetime
from decimal import Decimal

from .db import QueryResult

TYPES = ["bar", "hbar", "line", "area", "pie", "doughnut", "scatter", "kpi"]
MAX_SLICES, MAX_SERIES, MAX_KPI = 12, 8, 8


class ChartError(Exception):
    pass


def _num(v, col):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float, Decimal)):
        raise ChartError(f"column '{col}' must be numeric but contains {type(v).__name__} values like {v!r}")
    return float(v)


def _label(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:10] if isinstance(v, date) and not isinstance(v, datetime) else v.isoformat(sep=" ")
    return "(null)" if v is None else str(v)


def _cols(result: QueryResult, names, what):
    idx = {c: i for i, c in enumerate(result.columns)}
    out = []
    for n in names:
        if n not in idx:
            raise ChartError(f"{what} column '{n}' is not in the query result. Available columns: {', '.join(result.columns)}")
        out.append(idx[n])
    return out


def build_chart(result: QueryResult, args: dict) -> dict:
    ctype = args.get("type")
    if ctype not in TYPES:
        raise ChartError(f"type must be one of {TYPES}")
    if result.truncated:
        raise ChartError("query returned more than 200 rows; aggregate or add LIMIT so the chart shows complete data")
    if not result.rows:
        raise ChartError("query returned no rows; nothing to chart")
    title = str(args.get("title") or "").strip() or ctype

    if ctype == "kpi":
        row = result.rows[0]
        cards = []
        for c, v in list(zip(result.columns, row))[:MAX_KPI]:
            cards.append({"label": c.replace("_", " "), "value": float(v) if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool) else _label(v)})
        return {"type": "kpi", "title": title, "cards": cards}

    x = args.get("x")
    ys = args.get("y")
    ys = [c.strip() for c in ys.split(",") if c.strip()] if isinstance(ys, str) else list(ys or [])
    series = args.get("series")
    if not x or not ys:
        raise ChartError("'x' (category/time column) and 'y' (numeric column or list of columns) are required")
    (xi,) = _cols(result, [x], "x")
    yis = _cols(result, ys, "y")

    if ctype == "scatter":
        pts = [{"x": _num(r[xi], x), "y": _num(r[yis[0]], ys[0])} for r in result.rows]
        return {"type": "scatter", "title": title, "xlabel": x, "ylabel": ys[0],
                "datasets": [{"label": title, "data": [p for p in pts if p["x"] is not None and p["y"] is not None]}]}

    if ctype in ("pie", "doughnut"):
        if len(ys) != 1 or series:
            raise ChartError("pie/doughnut charts take exactly one y column and no series")
        if len(result.rows) > MAX_SLICES:
            raise ChartError(f"too many slices ({len(result.rows)} > {MAX_SLICES}); group the tail into 'Other' or use a bar chart")
        vals = [_num(r[yis[0]], ys[0]) or 0.0 for r in result.rows]
        if any(v < 0 for v in vals):
            raise ChartError("pie/doughnut values must be non-negative; use a bar chart")
        return {"type": ctype, "title": title, "labels": [_label(r[xi]) for r in result.rows],
                "datasets": [{"label": ys[0], "data": vals}]}

    # bar / hbar / line / area
    if series:
        if len(ys) != 1:
            raise ChartError("with 'series', give exactly one y column")
        (si,) = _cols(result, [series], "series")
        labels, groups = [], {}
        for r in result.rows:
            lab, s = _label(r[xi]), _label(r[si])
            if lab not in labels:
                labels.append(lab)
            if s not in groups and len(groups) >= MAX_SERIES:
                raise ChartError(f"more than {MAX_SERIES} series; filter to the top ones in SQL")
            cell = groups.setdefault(s, {})
            if lab in cell:
                raise ChartError(f"multiple rows for x='{lab}' and series='{s}'; aggregate in SQL first")
            cell[lab] = _num(r[yis[0]], ys[0])
        datasets = [{"label": s, "data": [cell.get(l) for l in labels]} for s, cell in groups.items()]
    else:
        if len(ys) > MAX_SERIES:
            raise ChartError(f"at most {MAX_SERIES} y columns")
        labels = [_label(r[xi]) for r in result.rows]
        datasets = [{"label": y, "data": [_num(r[i], y) for r in result.rows]} for y, i in zip(ys, yis)]
    return {"type": ctype, "title": title, "xlabel": x, "ylabel": ys[0] if len(ys) == 1 else "",
            "labels": labels, "datasets": datasets}
