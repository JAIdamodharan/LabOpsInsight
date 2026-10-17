"""The agent loop: plan -> query -> verify -> answer, with hard budgets."""
import os
from dataclasses import dataclass, field
from typing import Iterator

import anthropic

from . import dq, profile
from .charts import TYPES as CHART_TYPES, ChartError, build_chart
from .db import Database, QueryRejected

MODEL = os.environ.get("ANALYST_MODEL", "claude-sonnet-5-5")

LAB_GLOSSARY = """You are supporting an analytical / QC laboratory in a CRO-CDMO style environment. Domain glossary (use these definitions unless the user says otherwise):
- test = one analytical test on one sample (tests table). status is 'Pass' or 'OOS'. OOS = out of specification: result_value outside spec_low..spec_high. OOS rate = OOS tests / tests.
- turnaround time (TAT) = completed_date - samples.received_date, in days. A test is on time if completed_date <= samples.due_date. Use only rows with completed_date >= start_date and due_date >= received_date for TAT / on-time (the others are data errors).
- utilization = sum(run_hours) / available hours (20 h per weekday per instrument). instrument_events lists Preventive Maintenance, Calibration and Breakdown periods.
- deviation = a quality event (deviations table) with category, severity, lab, raised-by analyst; open = closed_date IS NULL.
- Month of a test = month of completed_date. Anonymised client codes and analyst IDs only.
- Break "why" questions down by instrument, lab, method, analyst/team and service line, and check instrument_events around the dates. Next-step examples: calibration records, instrument logs, training records."""

GENERIC_NOTES = """The user uploaded their own workbook(s). Each sheet became a table (names normalised to snake_case; reserved words get a trailing underscore); column names were normalised the same way. Do not assume a business domain: infer meaning from names and values, state your assumptions, and only ask what a column means if the answer depends on it. Numbers stored as text appear as VARCHAR: use TRY_CAST(col AS DOUBLE) when you need them numeric, and say so. Treat blanks as missing, not zero."""

METHOD = """Method:
1. Call get_schema first. Never guess column or table names.
2. Form hypotheses, then test them with SQL. For "why did X change" questions, quantify the change, then break it down by every available dimension (time and each categorical column) to find where it is concentrated, and compare with a baseline period.
3. DuckDB SQL: string literals use SINGLE quotes ('North'); double quotes denote identifiers. Dates are DATE columns (e.g. order_date BETWEEN '2025-03-01' AND '2025-03-31').
4. Sanity-check results (empty results, implausible values, duplicates, join fan-out). If a query errors, read the error and fix it.
5. Report numbers as recorded, never silently drop or "fix" records. Call data_quality when a number looks odd and before concluding on one; mention material data-quality issues as caveats. Never claim a cause the data cannot prove: say what the data shows, what you infer, and what should be verified next. The people who own the data decide; you advise.
6. When a visual helps, call the chart tool (the user sees an interactive chart; SQL results already show as sortable tables). Pick the type: bar/hbar to compare categories, line/area for trends over time, pie/doughnut only for parts of a whole with at most 6-8 slices, scatter for two numeric variables, kpi for headline numbers (one SQL row, one column per metric). The chart tool runs its own SQL: x is the category/time column, y the numeric column(s), series optionally splits one y into several lines/bars. Do not chart more than 2-3 times per question; the written answer matters most.
7. For statistics (significance, correlation, drift) or custom plots, use run_python if available. In it, `con` is a read-only DuckDB connection (con.sql("...").df() gives a DataFrame), pandas/numpy/scipy/matplotlib are imported as pd/np/plt, and charts must be saved with plt.savefig(os.path.join(OUT_DIR, "name.png")). Always print() results.
8. Dates: get_schema shows the real date range of every date column. Never assume today's date or the current year; use only dates inside the data's range. If a query returns 0 rows, check its filters against that range before anything else.
9. Scope: if the question asks for something these tables cannot answer (for example sales, revenue, prices, customers' payments, headcount cost), do not run queries for it. Answer briefly: say the data does not contain it, state what it does cover, and offer two or three questions you can answer.
10. Stop when you can answer with evidence. Final answer format: a one-line conclusion, then the key numbers supporting it, then caveats and recommended next verification steps."""


def build_system(domain: str = "lab", docs: bool = False) -> str:
    intro = "You are an autonomous senior data analyst. Answer the user's question by investigating the database with the run_sql tool."
    return "\n\n".join([intro, LAB_GLOSSARY if domain == "lab" else GENERIC_NOTES, METHOD] + ([DOCS_NOTE] if docs else []))


SYSTEM = build_system("lab")

MODEL_ROWS = 25          # rows of a SQL result the model gets back (the user's grid still shows everything)
MAX_NUDGES = 2
REPLY_TOKENS = 2048      # providers count the *requested* reply size against per-minute token limits, so don't ask for more than needed
KEEP_FULL = 2            # the newest tool-result rounds stay complete; older ones are shortened before being re-sent
OLD_RESULT_CHARS = 500
FINAL_NOTE = ("You have used your investigation budget. Do not call any more tools. Write the final answer now from what you already found: "
              "a one-line conclusion, the key numbers, and what you could not verify.")
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

DQ_TOOL = {
    "name": "data_quality",
    "description": "Run the rule-based data-review checks (duplicates, missing analyst/result, impossible dates, unit errors, status vs result mismatches, tests on a broken instrument...) and return counts per rule. Takes no arguments.",
    "input_schema": {"type": "object", "properties": {}},
}

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


DOCS_TOOL = {
    "name": "search_docs",
    "description": "Search the lab's written procedures (specifications, OOS investigation, calibration, deviation/CAPA, turnaround policy, training, data integrity). "
                   "Use it when the question needs what a limit, rule or procedure says, or to explain a finding. Returns up to 3 passages tagged [DOC section N].",
    "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "keywords or a short question"}}, "required": ["query"]},
}
DOCS_NOTE = ("Procedure documents: call search_docs when you need what a specification, rule or procedure says (limits, investigation steps, timelines, "
             "policies). Cite what you use as [DOC-ID section N]. Numbers about the data always come from SQL, never from the documents. "
             "If the documents do not cover something, say so; never invent a policy.")


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


def compact(messages: list) -> list:
    """Copy of the conversation where tool results older than the last KEEP_FULL rounds are shortened, so every call re-sends less."""
    idx = [i for i, m in enumerate(messages) if m["role"] == "user" and isinstance(m["content"], list)]
    old = set(idx[:-KEEP_FULL]) if len(idx) > KEEP_FULL else set()
    out = []
    for i, m in enumerate(messages):
        if i in old:
            m = {"role": "user", "content": [{**r, "content": r["content"][:OLD_RESULT_CHARS] + ("\n... (older result shortened)" if len(r["content"]) > OLD_RESULT_CHARS else "")} for r in m["content"]]}
        out.append(m)
    return out


class Analyst:
    def __init__(self, db: Database, client=None, model: str = MODEL,
                 max_steps: int = 15, max_tokens_budget: int = 150_000, sandbox=None, domain: str = "lab", knowledge=None):
        self.db = db
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.max_steps = max_steps
        self.max_tokens_budget = max_tokens_budget
        self.sandbox = sandbox
        self.knowledge = knowledge if domain == "lab" else None      # procedure documents only make sense for the lab data
        self.system = build_system(domain, docs=self.knowledge is not None)
        self.quality = dq.as_text if domain == "lab" else profile.as_text
        self.tools = TOOLS + [DQ_TOOL, CHART_TOOL] + ([DOCS_TOOL] if self.knowledge is not None else []) + ([PYTHON_TOOL] if sandbox else [])
        self._extra: dict = {}

    def _execute(self, name: str, args: dict, stats: RunStats) -> tuple[str, bool]:
        self._extra = {}
        if name == "get_schema":
            return self.db.schema(), False
        if name == "search_docs" and self.knowledge is not None:
            return self.knowledge.as_text(str(args.get("query", ""))[:300]), False
        if name == "data_quality":
            return self.quality(self.db), False
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
                text = res.to_text(MODEL_ROWS)
                if not res.rows:                                    # an empty result is usually a wrong filter; remind the model what the data covers
                    if not hasattr(self, "_span"):
                        self._span = self.db.date_span()
                    if self._span:
                        text += f"\nNote: 0 rows. The data only covers {self._span[0]} to {self._span[1]}; check date filters and the spelling of values before concluding anything."
                return text, False
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
            last_call = stats.steps >= self.max_steps                      # the final allowed step: no tools, just answer with what was found
            send = compact(messages) + ([{"role": "user", "content": FINAL_NOTE}] if last_call else [])
            resp = self.client.messages.create(
                model=self.model, max_tokens=REPLY_TOKENS, system=self.system, messages=send, **({} if last_call else {"tools": self.tools}),
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
