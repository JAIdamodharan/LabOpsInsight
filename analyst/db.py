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

    def to_text(self) -> str:
        if not self.columns:
            return "(no columns)"
        lines = [" | ".join(self.columns)]
        lines += [" | ".join("NULL" if v is None else str(v) for v in r) for r in self.rows]
        if self.truncated:
            lines.append(f"... truncated to {MAX_ROWS} rows; aggregate or filter instead")
        if not self.rows:
            lines.append("(0 rows)")
        return "\n".join(lines)


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
            out.append(f"{t} ({n} rows): " + ", ".join(f"{c} {d}" for c, d in cols))
        return "\n".join(out)
