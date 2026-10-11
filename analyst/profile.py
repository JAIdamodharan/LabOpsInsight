"""Dataset-agnostic profiling and data review: per-column statistics, auto-generated chart specs and generic quality checks.

Works on any DuckDB catalog (uploaded workbooks or the built-in lab data). Identifiers come from the catalog and are quoted;
values are never interpolated into SQL. Read-only: it reports issues, it never changes data.
"""
import re
from datetime import date, datetime
from decimal import Decimal

from .db import Database

NUMERIC = {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT", "FLOAT", "DOUBLE", "REAL"}
QUANTITY_LIKE = re.compile(r"(qty|quantity|price|amount|units|count|age|revenue|cost|total|salary|weight)", re.I)
PROFILE_TIMEOUT = 60
MAX_TOP = 5


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def kind_of(dtype: str) -> str:
    d = dtype.upper()
    if d in NUMERIC or d.startswith("DECIMAL"):
        return "numeric"
    if d == "BOOLEAN":
        return "bool"
    if d == "DATE" or d.startswith("TIMESTAMP"):
        return "date"
    return "text"


def _py(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:19]
    return v


def _round(v, n=4):
    return None if v is None else round(float(v), n)


def _run(db: Database, sql: str, params=None):
    return db.run(sql, params, timeout=PROFILE_TIMEOUT).rows


def _tables(db: Database) -> list[str]:
    return [r[0] for r in _run(db, "SELECT table_name FROM information_schema.tables WHERE table_schema='main' ORDER BY 1")]


def _table_profile(db: Database, t: str) -> dict:
    cols = _run(db, "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ? ORDER BY ordinal_position", [t])
    n = int(_run(db, f"SELECT count(*) FROM {q(t)}")[0][0])
    exprs, plan = [], []
    for c, d in cols:
        k = kind_of(d)
        e = [f"count({q(c)})", f"count(DISTINCT {q(c)})"]
        if k == "numeric":
            e += [f"min({q(c)})", f"max({q(c)})", f"avg({q(c)})", f"quantile_cont({q(c)}, 0.25)", f"median({q(c)})", f"quantile_cont({q(c)}, 0.75)"]
        elif k == "date":
            e += [f"min({q(c)})", f"max({q(c)})"]
        plan.append((c, d, k, len(exprs), len(e)))
        exprs += e
    row = _run(db, f"SELECT {', '.join(exprs)} FROM {q(t)}")[0] if exprs else ()
    columns = []
    for c, d, k, off, ln in plan:
        v = row[off:off + ln]
        col = dict(name=c, type=d, kind=k, non_null=int(v[0]), null_pct=round(100 * (n - int(v[0])) / n, 2) if n else 0.0, distinct=int(v[1]))
        if k == "numeric":
            col.update(min=_round(v[2]), max=_round(v[3]), mean=_round(v[4]), q1=_round(v[5]), median=_round(v[6]), q3=_round(v[7]))
        elif k == "date":
            col.update(min=_py(v[2]), max=_py(v[3]))
        if k in ("text", "bool") and 1 <= col["distinct"] <= 30:
            col["top"] = [[_py(a), int(b)] for a, b in _run(db, f"SELECT {q(c)}, count(*) FROM {q(t)} WHERE {q(c)} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {MAX_TOP}")]
        columns.append(col)
    dup = int(_run(db, f'SELECT coalesce(sum("__n" - 1), 0) FROM (SELECT *, count(*) AS "__n" FROM {q(t)} GROUP BY ALL HAVING count(*) > 1)')[0][0]) if cols else 0
    return dict(name=t, rows=n, columns=columns, duplicate_rows=dup)


def _issue(table, column, rule, severity, title, detail, count, total, examples=None):
    return dict(id=rule, severity=severity, table=table, column=column, title=title, detail=detail, count=int(count),
                pct=round(100 * count / total, 2) if total else 0.0, examples=examples or [])


def _issues(db: Database, tp: dict) -> list[dict]:
    t, n, out = tp["name"], tp["rows"], []
    if not n:
        return out
    for c in tp["columns"]:
        cn, k = c["name"], c["kind"]
        missing = n - c["non_null"]
        if c["null_pct"] >= 5:
            sev = "Critical" if c["null_pct"] >= 50 else "Major" if c["null_pct"] >= 20 else "Minor"
            out.append(_issue(t, cn, "GQ-001", sev, "Missing values", f"{c['null_pct']}% of rows have no value.", missing, n))
        if c["distinct"] == 1 and c["non_null"] == n and n >= 2:
            only = _run(db, f"SELECT {q(cn)} FROM {q(t)} WHERE {q(cn)} IS NOT NULL LIMIT 1")[0][0]
            out.append(_issue(t, cn, "GQ-003", "Minor", "Constant column", "Every non-empty value is identical, so it carries no information.", c["non_null"], n, [str(_py(only))]))
        if k == "text" and c["non_null"] >= 5:
            numeric_like, total = _run(db, f"SELECT count(*) FILTER (TRY_CAST({q(cn)} AS DOUBLE) IS NOT NULL), count({q(cn)}) FROM {q(t)}")[0]
            if numeric_like and numeric_like / total >= 0.9:
                bad = [str(r[0]) for r in _run(db, f"SELECT DISTINCT {q(cn)} FROM {q(t)} WHERE {q(cn)} IS NOT NULL AND TRY_CAST({q(cn)} AS DOUBLE) IS NULL LIMIT 5")]
                bad_n = total - numeric_like
                extra = f" {bad_n} value{' is' if bad_n == 1 else 's are'} not numeric." if bad_n else ""
                out.append(_issue(t, cn, "GQ-004", "Major", "Numbers stored as text", f"{numeric_like} values look numeric but are stored as text, so sums and averages will not work.{extra}", numeric_like, n, bad))
            if c["distinct"] <= 1000:
                dv, dn = _run(db, f"SELECT count(DISTINCT {q(cn)}), count(DISTINCT lower(trim({q(cn)}))) FROM {q(t)}")[0]
                if dv > dn:
                    ex = [" | ".join(map(str, r[0])) for r in _run(db, f"SELECT list(DISTINCT {q(cn)}) FROM {q(t)} WHERE {q(cn)} IS NOT NULL GROUP BY lower(trim({q(cn)})) HAVING count(DISTINCT {q(cn)}) > 1 LIMIT 3")]
                    out.append(_issue(t, cn, "GQ-005", "Minor", "Inconsistent spelling / case / spacing", f"{dv - dn} value(s) differ only by case or surrounding spaces, so grouping will split them.", dv - dn, dv, ex))
            pad = int(_run(db, f"SELECT count(*) FROM {q(t)} WHERE {q(cn)} <> trim({q(cn)})")[0][0])
            if pad:
                ex = [repr(r[0]) for r in _run(db, f"SELECT DISTINCT {q(cn)} FROM {q(t)} WHERE {q(cn)} <> trim({q(cn)}) LIMIT 3")]
                out.append(_issue(t, cn, "GQ-009", "Minor", "Leading / trailing spaces", "Values have extra spaces that break matching and grouping.", pad, n, ex))
        if k == "numeric" and c["non_null"] >= 20 and c["q1"] is not None and c["q3"] is not None and c["q3"] > c["q1"]:
            iqr = c["q3"] - c["q1"]
            lo, hi = c["q1"] - 3 * iqr, c["q3"] + 3 * iqr
            cnt = int(_run(db, f"SELECT count(*) FROM {q(t)} WHERE {q(cn)} < ? OR {q(cn)} > ?", [lo, hi])[0][0])
            if cnt:
                out.append(_issue(t, cn, "GQ-006", "Minor", "Extreme outliers", f"{cnt} value(s) are more than 3 IQRs outside the middle 50% (range {c['min']} to {c['max']}); check for typos or unit errors.", cnt, n, [f"min {c['min']}", f"max {c['max']}"]))
            if QUANTITY_LIKE.search(cn) and c["min"] is not None and c["min"] < 0:
                neg = int(_run(db, f"SELECT count(*) FROM {q(t)} WHERE {q(cn)} < 0")[0][0])
                out.append(_issue(t, cn, "GQ-008", "Minor", "Negative values in a quantity/amount column", f"{neg} negative value(s); expected only for returns or credits.", neg, n, [f"min {c['min']}"]))
        if k == "date" and c["max"] and c["max"] > date.today().isoformat():
            fut = int(_run(db, f"SELECT count(*) FROM {q(t)} WHERE {q(cn)} > current_date")[0][0])
            out.append(_issue(t, cn, "GQ-007", "Minor", "Future dates", f"{fut} value(s) are later than today (latest {c['max'][:10]}).", fut, n))
    if tp["duplicate_rows"]:
        ex = _run(db, f"SELECT * FROM {q(t)} GROUP BY ALL HAVING count(*) > 1 LIMIT 1")
        names = [c["name"] for c in tp["columns"]]
        out.append(_issue(t, None, "GQ-002", "Major", "Duplicate rows", "Rows that are identical in every column; the extra copies would be double counted.", tp["duplicate_rows"], n,
                          [", ".join(f"{a}={_py(b)}" for a, b in zip(names, ex[0]))[:200]] if ex else []))
    return out


def _charts(db: Database, tp: dict) -> list[dict]:
    t, n, cols = tp["name"], tp["rows"], tp["columns"]
    if n < 5:
        return []
    out = []
    nums = [c for c in cols if c["kind"] == "numeric" and c["distinct"] >= 10 and c["min"] != c["max"] and not re.search(r"(^|_)id$", c["name"])]
    cats = [c for c in cols if c["kind"] == "text" and 2 <= c["distinct"] <= 30 and c["non_null"] >= 0.5 * n]
    dates = [c for c in cols if c["kind"] == "date" and c["min"] and c["min"] != c["max"]]
    if dates:
        d = dates[0]["name"]
        rows = _run(db, f"SELECT strftime(date_trunc('month', {q(d)}), '%Y-%m') m, count(*) FROM {q(t)} WHERE {q(d)} IS NOT NULL GROUP BY 1 ORDER BY 1")
        if 3 <= len(rows) <= 120:
            out.append(dict(type="line", title=f"{t}: rows per month by {d}", xlabel=d, ylabel="rows", labels=[r[0] for r in rows], datasets=[dict(label="rows", data=[float(r[1]) for r in rows])]))
    if nums and cats:
        v, c = nums[0]["name"], max(cats, key=lambda x: x["distinct"] if x["distinct"] <= 12 else 0)["name"]
        rows = _run(db, f"SELECT {q(c)}, avg({q(v)}) a FROM {q(t)} WHERE {q(c)} IS NOT NULL AND {q(v)} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 12")
        if len(rows) >= 2:
            out.append(dict(type="bar", title=f"{t}: average {v} by {c}", xlabel=c, ylabel=f"avg {v}", labels=[str(r[0]) for r in rows], datasets=[dict(label=f"avg {v}", data=[round(float(r[1]), 4) for r in rows])]))
    if cats:
        c = max(cats, key=lambda x: x["distinct"] if x["distinct"] <= 12 else 0)
        rows = _run(db, f"SELECT {q(c['name'])}, count(*) FROM {q(t)} WHERE {q(c['name'])} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 10")
        out.append(dict(type="hbar", title=f"{t}: rows by {c['name']}", xlabel=c["name"], ylabel="rows", labels=[str(r[0]) for r in rows], datasets=[dict(label="rows", data=[float(r[1]) for r in rows])]))
    if nums:
        c = nums[0]
        w = (c["max"] - c["min"]) / 12
        rows = _run(db, f"SELECT least(floor(({q(c['name'])} - ?) / ?), 11)::INTEGER b, count(*) FROM {q(t)} WHERE {q(c['name'])} IS NOT NULL GROUP BY 1 ORDER BY 1", [c["min"], w])
        counts = {int(b): int(k) for b, k in rows}
        fmt = lambda x: f"{x:,.4g}"
        out.append(dict(type="bar", title=f"{t}: distribution of {c['name']}", xlabel=c["name"], ylabel="rows",
                        labels=[f"{fmt(c['min'] + i * w)} to {fmt(c['min'] + (i + 1) * w)}" for i in range(12)],
                        datasets=[dict(label="rows", data=[float(counts.get(i, 0)) for i in range(12)])]))
    return out


def profile(db: Database) -> dict:
    tables = [_table_profile(db, t) for t in _tables(db)]
    issues = [i for tp in tables for i in _issues(db, tp)]
    charts = [ch for tp in tables[:3] for ch in _charts(db, tp)][:9]
    cells = sum(tp["rows"] * len(tp["columns"]) for tp in tables)
    nulls = sum(tp["rows"] - c["non_null"] for tp in tables for c in tp["columns"])
    sev = {s: sum(1 for i in issues if i["severity"] == s) for s in ("Critical", "Major", "Minor")}
    order = {"Critical": 0, "Major": 1, "Minor": 2}
    issues.sort(key=lambda i: (order[i["severity"]], -i["count"]))
    return dict(summary=dict(tables=len(tables), rows=sum(tp["rows"] for tp in tables), columns=sum(len(tp["columns"]) for tp in tables),
                             completeness_pct=round(100 * (1 - nulls / cells), 2) if cells else 100.0,
                             duplicate_rows=sum(tp["duplicate_rows"] for tp in tables), issues=len(issues), issues_by_severity=sev),
                tables=tables, issues=issues, charts=charts)


def as_text(db: Database) -> str:
    """Compact report for the agent's data_quality tool on arbitrary datasets."""
    p = profile(db)
    s = p["summary"]
    lines = [f"{s['tables']} table(s), {s['rows']} rows, {s['columns']} columns; {s['completeness_pct']}% of cells filled; {s['duplicate_rows']} duplicate row(s); {s['issues']} data-review finding(s)."]
    for i in p["issues"][:25]:
        where = f"{i['table']}.{i['column']}" if i["column"] else i["table"]
        ex = f" e.g. {', '.join(i['examples'][:3])}" if i["examples"] else ""
        lines.append(f"[{i['severity']}] {i['id']} {i['title']} in {where}: {i['detail']}{ex}")
    if s["issues"] > 25:
        lines.append(f"... and {s['issues'] - 25} more.")
    return "\n".join(lines)
