"""Guarded, read-only database access for the agent."""
import re
import threading
from dataclasses import dataclass

import duckdb

MAX_ROWS = 200
TIMEOUT_S = 10

_ALLOWED_START = re.compile(r"^\s*(select|with|describe|show|summarize|explain)\b", re.I)


class QueryRejected(Exception):
    pass


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool

    def to_text(self, max_rows: int | None = None, max_chars: int = 6000) -> str:
        """Plain-text table. With max_rows, only that many rows are shown (used for what the *model* sees, to save tokens)."""
        if not self.columns:
            return "(no columns)"
        rows = self.rows if max_rows is None else self.rows[:max_rows]
        lines = [" | ".join(self.columns)]
        lines += [" | ".join("NULL" if v is None else str(v) for v in r) for r in rows]
        if max_rows is not None and len(self.rows) > max_rows:
            lines.append(f"... showing {max_rows} of {len(self.rows)}{'+' if self.truncated else ''} rows (the user sees them all). Aggregate, filter or LIMIT instead of listing rows.")
        elif self.truncated:
            lines.append(f"... truncated to {MAX_ROWS} rows; aggregate or filter instead")
        if not self.rows:
            lines.append("(0 rows)")
        text = "\n".join(lines)
        return text if len(text) <= max_chars else text[:max_chars] + "\n... (output shortened)"


class Database:
    def __init__(self, path: str):
        # read_only blocks writes; external access off blocks read_csv('/etc/passwd') etc.
        self.con = duckdb.connect(path, read_only=True, config={"enable_external_access": False})

    def validate(self, sql: str) -> str:
        stripped = sql.strip().rstrip(";").strip()
        if not stripped:
            raise QueryRejected("Empty query.")
        if ";" in stripped:
            raise QueryRejected("Only a single statement is allowed.")
        if not _ALLOWED_START.match(stripped):
            raise QueryRejected("Only read-only SELECT/WITH/DESCRIBE/SHOW/SUMMARIZE/EXPLAIN queries are allowed.")
        return stripped

    def run(self, sql: str, params: list | None = None, timeout: float | None = None) -> QueryResult:
        sql = self.validate(sql)
        cur = self.con.cursor()
        timer = threading.Timer(timeout or TIMEOUT_S, cur.interrupt)
        timer.start()
        try:
            cur.execute(sql, params or [])
            cols = [d[0] for d in (cur.description or [])]
            rows = cur.fetchmany(MAX_ROWS + 1)
        except duckdb.InterruptException:
            raise QueryRejected(f"Query exceeded {timeout or TIMEOUT_S}s timeout; simplify it.")
        finally:
            timer.cancel()
            cur.close()
        return QueryResult(cols, rows[:MAX_ROWS], len(rows) > MAX_ROWS)

    def date_columns(self) -> list[tuple[str, str]]:
        return [(t, c) for t, c in self.con.execute(
            "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema='main' AND data_type IN ('DATE','TIMESTAMP') ORDER BY 1, ordinal_position").fetchall()]

    def date_span(self) -> tuple[str, str] | None:
        """Earliest and latest date anywhere in the data, so questions can be checked against what the data actually covers."""
        lo, hi = None, None
        for t, c in self.date_columns():
            a, b = self.con.execute(f'SELECT min("{c}"), max("{c}") FROM "{t}"').fetchone()
            if a is not None:
                lo, hi = (a if lo is None or a < lo else lo), (b if hi is None or b > hi else hi)
        return (str(lo)[:10], str(hi)[:10]) if lo is not None else None

    def schema(self) -> str:
        out = []
        tables = self.con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main' ORDER BY 1"
        ).fetchall()
        for (t,) in tables:
            n = self.con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            cols = self.con.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name=? ORDER BY ordinal_position", [t]
            ).fetchall()
            parts = []
            for c, d in cols:
                extra = ""
                if d in ("DATE", "TIMESTAMP"):                      # show the real date range so nobody has to guess it
                    a, b = self.con.execute(f'SELECT min("{c}"), max("{c}") FROM "{t}"').fetchone()
                    if a is not None:
                        extra = f" [{str(a)[:10]} to {str(b)[:10]}]"
                parts.append(f"{c} {d}{extra}")
            out.append(f"{t} ({n} rows): " + ", ".join(parts))
        return "\n".join(out)
