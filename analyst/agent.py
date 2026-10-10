"""The agent loop: plan -> query -> verify -> answer, with hard budgets."""
import os
from dataclasses import dataclass, field
from typing import Iterator

import anthropic

from .charts import TYPES as CHART_TYPES, ChartError, build_chart
from .db import Database, QueryRejected

MODEL = os.environ.get("ANALYST_MODEL", "claude-sonnet-5-5")

SYSTEM = """You are an autonomous senior data analyst. Answer the user's business question by investigating the database with the run_sql tool.

Method:
1. Call get_schema first. Never guess column or table names.
2. Form hypotheses, then test them with SQL. For "why did X change" questions, quantify the change, then break it down by every available dimension (time, region, channel, category, ...) to find where it is concentrated, and compare against a baseline period.
3. DuckDB SQL: string literals use SINGLE quotes ('West'); double quotes denote identifiers. Dates are DATE columns (e.g. order_date BETWEEN '2025-03-01' AND '2025-03-31').
4. Sanity-check results (empty results, implausible totals, row-count/duplicate issues from joins). If a query errors, read the error and fix it.
5. Revenue = sum(quantity * unit_price) over completed orders unless the question implies otherwise; state any assumption you make.
6. When a visual helps, call the chart tool (the user sees an interactive chart; SQL results already show as sortable tables). Pick the type: bar/hbar to compare categories, line/area for trends over time, pie/doughnut only for parts of a whole with at most 6-8 slices, scatter for two numeric variables, kpi for headline numbers (one SQL row, one column per metric). The chart tool runs its own SQL: the x column is the category/time axis, y the numeric column(s), series optionally splits one y into several lines/bars. Do not chart more than 2-3 times per question; the written answer still matters most.
7. For statistics (significance, correlation, anomaly detection) or custom plots, use run_python if available. In it, `con` is a read-only DuckDB connection (con.sql("...").df() gives a DataFrame), pandas/numpy/scipy/matplotlib are imported as pd/np/plt, and charts must be saved with plt.savefig(os.path.join(OUT_DIR, "name.png")). Always print() results. Prefer run_sql for plain aggregation.
8. Stop when you can answer with evidence. Final answer format: a one-line conclusion, then the key numbers supporting it, then caveats. Do not claim causes the data cannot support; distinguish what the data shows from what you infer."""

MAX_NUDGES = 2
NUDGE = ("Your last tool call failed and you have not resolved it. Do not answer yet: call the tool again with a corrected "
         "query (use the tool-calling interface, not a code block) and get real results first.")

TOOLS = [
    {
        "name": "get_schema",
        "description": "Return all tables with row counts, column names and types.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "run_sql",
        "description": "Run one read-only DuckDB SQL query. Returns at most 200 rows. Aggregate rather than dumping rows.",
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        },
    },
]

CHART_TOOL = {
    "name": "chart",
    "description": "Render an interactive chart or KPI cards for the user from a SQL query (at most 200 rows). The user sees it; you only get a confirmation.",
    "input_schema": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": CHART_TYPES},
            "title": {"type": "string"},
            "sql": {"type": "string", "description": "read-only DuckDB query producing the chart data"},
            "x": {"type": "string", "description": "result column for categories / time / scatter x (not needed for kpi)"},
            "y": {"type": "string", "description": "numeric result column; comma-separate several columns for multiple series"},
            "series": {"type": "string", "description": "optional result column that splits y into separate series"},
        },
        "required": ["type", "title", "sql"],
    },
}

PYTHON_TOOL = {
    "name": "run_python",
    "description": "Run Python in an isolated sandbox (no network). `con` = read-only DuckDB connection; pd, np, plt preloaded; save charts to OUT_DIR. print() what you need to see.",
    "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
}


@dataclass
class Event:
    kind: str  # "tool_call" | "tool_result" | "answer" | "stopped"
    data: dict


@dataclass
class RunStats:
    steps: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    sql_errors: int = 0
    sql_calls: int = 0
    stop_reason: str = ""
    answer: str = ""
    python_calls: int = 0
    nudges: int = 0
    chart_calls: int = 0
    charts: list[dict] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)


class Analyst:
    def __init__(self, db: Database, client=None, model: str = MODEL,
                 max_steps: int = 15, max_tokens_budget: int = 150_000, sandbox=None):
        self.db = db
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.max_steps = max_steps
        self.max_tokens_budget = max_tokens_budget
        self.sandbox = sandbox
        self.tools = TOOLS + [CHART_TOOL] + ([PYTHON_TOOL] if sandbox else [])
        self._extra: dict = {}

    def _execute(self, name: str, args: dict, stats: RunStats) -> tuple[str, bool]:
        self._extra = {}
        if name == "get_schema":
            return self.db.schema(), False
        if name == "chart":
            stats.chart_calls += 1
            try:
                res = self.db.run(args.get("sql", ""))
                spec = build_chart(res, args)
            except (QueryRejected, ChartError) as e:
                return f"CHART ERROR: {e}", True
            except Exception as e:
                return f"CHART SQL ERROR: {e}", True
            stats.charts.append(spec)
            self._extra = {"chart": spec}
            n = len(spec.get("cards") or spec.get("labels") or spec["datasets"][0]["data"])
            return f"Rendered {spec['type']} chart '{spec['title']}' ({n} points) for the user.", False
        if name == "run_sql":
            stats.sql_calls += 1
            try:
                res = self.db.run(args.get("sql", ""))
                self._extra = {"table": {"columns": res.columns, "rows": res.rows, "truncated": res.truncated}}
                return res.to_text(), False
            except QueryRejected as e:
                stats.sql_errors += 1
                return f"REJECTED: {e}", True
            except Exception as e:  # duckdb binder/parser errors etc.
                stats.sql_errors += 1
                return f"SQL ERROR: {e}", True
        if name == "run_python" and self.sandbox:
            stats.python_calls += 1
            res = self.sandbox.run(args.get("code", ""))
            stats.artifacts += [str(f) for f in res.files]
            return res.to_text(), res.is_error
        return f"Unknown tool {name}", True

    def run(self, question: str) -> Iterator[Event]:
        """Yield events as they happen; the final RunStats is available via .last_stats."""
        stats = RunStats()
        self.last_stats = stats
        messages = [{"role": "user", "content": question}]

        def emit(kind, **data):
            ev = Event(kind, data)
            stats.events.append(ev)
            return ev

        last_failed = False
        while True:
            if stats.steps >= self.max_steps:
                stats.stop_reason = "max_steps"
                yield emit("stopped", reason="max_steps")
                return
            if stats.input_tokens + stats.output_tokens >= self.max_tokens_budget:
                stats.stop_reason = "token_budget"
                yield emit("stopped", reason="token_budget")
                return

            stats.steps += 1
            resp = self.client.messages.create(
                model=self.model, max_tokens=4096, system=SYSTEM, tools=self.tools, messages=messages,
            )
            stats.input_tokens += resp.usage.input_tokens
            stats.output_tokens += resp.usage.output_tokens
            messages.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason != "tool_use" and last_failed and stats.nudges < MAX_NUDGES:
                # Never accept a final answer on top of an unresolved tool error (weak models do this a lot).
                stats.nudges += 1
                messages.append({"role": "user", "content": NUDGE})
                continue

            if resp.stop_reason != "tool_use":
                stats.answer = "".join(b.text for b in resp.content if b.type == "text")
                stats.stop_reason = "answered"
                yield emit("answer", text=stats.answer)
                return

            results = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                yield emit("tool_call", name=block.name, input=block.input)
                text, is_err = self._execute(block.name, block.input, stats)
                yield emit("tool_result", name=block.name, text=text, is_error=is_err, **self._extra)
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": text, "is_error": is_err})
            last_failed = any(r["is_error"] for r in results)
            messages.append({"role": "user", "content": results})
