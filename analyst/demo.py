"""Scripted stand-in for the model, for UI development and offline demos.

It issues real tool calls (real SQL, real data-quality checks) in the order a good analyst would, and writes its
final answer from the actual query results. Only the *decisions* are scripted; there are four investigations
(OOS, on-time delivery, deviations, data quality). Anything else is declined honestly.
"""
import re
from types import SimpleNamespace as NS

from . import dq
from .db import Database

OTHER_INSTRUMENT = re.compile(r"\b(?:hplc|lcms|gc|diss|kf)-0\d\b")
OTHER_CATEGORIES = ("equipment", "sample handling", "process", "data integrity")
UNSUPPORTED = ("**Demo mode only covers four scripted investigations:** OOS rate (e.g. HPLC-03), on-time delivery / turnaround, "
               "deviations (e.g. Documentation), and data quality. Choose one of the example questions, or run with a real model "
               "(`--provider ollama|groq|anthropic`) for free-form questions.")

# ---- SQL used by the scripted investigations ---------------------------------------------------------------
OOS_MONTHLY = """SELECT strftime(completed_date,'%Y-%m') AS month, round(100.0*avg((status='OOS')::int),2) AS oos_rate_pct, count(*) AS tests
FROM tests GROUP BY 1 ORDER BY 1"""
OOS_BY_INST = """SELECT instrument_id, lab, count(*) AS tests, count(*) FILTER (status='OOS') AS oos, round(100.0*avg((status='OOS')::int),1) AS oos_rate_pct
FROM tests JOIN instruments USING(instrument_id) WHERE strftime(completed_date,'%Y-%m')='2025-03'
GROUP BY 1,2 ORDER BY 5 DESC LIMIT 8"""
OOS_WEEKLY = """SELECT strftime(date_trunc('week', completed_date),'%Y-%m-%d') AS week,
       CASE WHEN instrument_id='HPLC-03' THEN 'HPLC-03' ELSE 'Other HPLC' END AS instrument_group, round(avg(result_value),2) AS mean_assay_pct
FROM tests WHERE method='Assay' AND instrument_id LIKE 'HPLC-%' AND result_value < 200
  AND completed_date BETWEEN '2025-01-15' AND '2025-05-15' GROUP BY 1,2 ORDER BY 1,2"""
OOS_EVENTS = "SELECT event_type, start_date, end_date FROM instrument_events WHERE instrument_id='HPLC-03' ORDER BY start_date"

OT_MONTHLY = """SELECT strftime(completed_date,'%Y-%m') AS month, round(100.0*avg((completed_date<=due_date)::int),1) AS on_time_pct,
       round(avg(completed_date-received_date),2) AS avg_tat_days
FROM tests JOIN samples USING(sample_id) WHERE completed_date>=start_date AND due_date>=received_date GROUP BY 1 ORDER BY 1"""
OT_METHOD = """SELECT method, count(*) AS tests, round(100.0*avg((completed_date<=due_date)::int),1) AS on_time_pct, round(avg(completed_date-received_date),2) AS avg_tat_days
FROM tests JOIN samples USING(sample_id) WHERE strftime(completed_date,'%Y-%m')='2025-02' AND completed_date>=start_date AND due_date>=received_date
GROUP BY 1 ORDER BY 3"""
OT_DOWN = """SELECT i.instrument_id, i.instrument_type, i.lab, e.start_date, e.end_date FROM instrument_events e JOIN instruments i USING(instrument_id)
WHERE e.event_type='Breakdown' AND e.start_date<='2025-02-28' AND e.end_date>='2025-02-01' ORDER BY e.start_date"""
OT_WAIT = """SELECT strftime(date_trunc('week', s.received_date),'%Y-%m-%d') AS week_received, round(avg(t.start_date - s.received_date),2) AS avg_wait_days
FROM tests t JOIN samples s USING(sample_id) JOIN instruments i USING(instrument_id)
WHERE i.instrument_type='LC-MS' AND s.received_date BETWEEN '2025-01-06' AND '2025-03-31' AND t.completed_date>=t.start_date GROUP BY 1 ORDER BY 1"""

DV_MONTHLY = """SELECT strftime(opened_date,'%Y-%m') AS month, category, count(*) AS deviations FROM deviations
WHERE category IN ('Documentation','Equipment','Process','Sample Handling') GROUP BY 1,2 ORDER BY 1,2"""
DV_ANALYST = """SELECT d.analyst_id, a.team, a.join_date, count(*) AS documentation_deviations FROM deviations d JOIN analysts a USING(analyst_id)
WHERE d.category='Documentation' AND d.opened_date>='2025-04-01' GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 10"""
DV_COHORT = """SELECT cohort, count(*) AS analysts, sum(n) AS documentation_deviations, round(avg(n),1) AS per_analyst FROM (
  SELECT a.analyst_id, CASE WHEN a.join_date>='2025-01-01' THEN 'Joined Jan 2025' ELSE 'Earlier hires' END AS cohort, count(d.deviation_id) AS n
  FROM analysts a LEFT JOIN deviations d ON d.analyst_id=a.analyst_id AND d.category='Documentation' AND d.opened_date>='2025-04-01'
  GROUP BY 1,2) GROUP BY 1 ORDER BY 1"""

DQ_MISMATCH = """SELECT test_id, method, instrument_id, result_value, spec_low, spec_high, status FROM tests
WHERE result_value IS NOT NULL AND status='Pass' AND (result_value<spec_low OR result_value>spec_high) ORDER BY test_id LIMIT 10"""
DQ_MONTHLY = """SELECT strftime(completed_date,'%Y-%m') AS month, count(*) FILTER (analyst_id IS NULL) AS missing_analyst,
       count(*) FILTER (result_value IS NULL) AS missing_result FROM tests GROUP BY 1 ORDER BY 1"""


from .rag import Retriever

KB = Retriever()
DOC_WORDS = ("sop", "procedure", "policy", "spec limit", "specification", "what does", "limit for", "how long", "timeline", "capa", "alcoa", "due date", "requirement", "what should", "what must")


def _tu(i, name, **inp):
    return NS(type="tool_use", id=f"demo{i}", name=name, input=inp)


class DemoClient:
    def __init__(self, db: Database, with_python: bool = False):
        self.db, self.i, self.plan, self.final, self.scope = db, 0, None, None, ""
        self.messages = NS(create=self._create)

    def q(self, sql):
        return self.db.run(sql).rows

    # ---- scenarios: (list of (tool, kwargs)), final-answer builder ----
    def _scenario(self, question: str):
        t = question.lower()
        has = lambda *w: any(x in t for x in w)
        named = set(OTHER_INSTRUMENT.findall(t))
        if has(*DOC_WORDS) and not has("why", "increase", "change", "compared", "records need"):
            return self._procedures(question)
        if has("data quality", "data-quality", "duplicate", "data review", "integrity", "clean", "records need") and not has("deviation"):
            return self._dq()
        if has("deviation", "documentation", "training"):
            if has(*OTHER_CATEGORIES) and "documentation" not in t:
                return [], lambda: UNSUPPORTED          # the scripted walkthrough is about Documentation deviations only
            return self._deviations()
        if has("utilization", "tests completed", "units"):
            return [], lambda: UNSUPPORTED
        if has("on-time", "on time", "turnaround", "tat", "delay", "late", "delivery", "breakdown", "lcms-02"):
            return self._on_time()
        if has("oos", "out of spec", "out-of-spec", "hplc", "calibration", "instrument"):
            if named - {"hplc-03"}:
                return [], lambda: UNSUPPORTED          # another instrument was named: do not answer with the HPLC-03 story
            return self._oos()
        return [], lambda: UNSUPPORTED

    def _procedures(self, question):
        self.scope = "the synthetic procedure documents (no data queries)"
        plan = [("search_docs", dict(query=question))]

        def final():
            hits = KB.search(question, 2)
            if not hits:
                return "**The procedure documents do not cover this.** I will not invent a policy. Try asking about specification limits, OOS investigation, calibration, deviations and CAPA, turnaround, training or ALCOA+."
            body = "\n\n".join(f"- **[{h['doc']} section {h['section']}]** {h['title']}: " + " ".join(h["text"].split())[:420] for h in hits)
            return f"**What the procedures say:**\n\n{body}\n\n*(Demo mode: scripted model, real queries.)*"
        return plan, final

    def _oos(self):
        self.scope = "HPLC-03 in March 2025"
        self.cite = "OOS investigation instrument drift calibration"
        plan = [("get_schema", {}), ("run_sql", dict(sql=OOS_MONTHLY)),
                ("chart", dict(type="line", title="Monthly OOS rate (%)", sql=OOS_MONTHLY, x="month", y="oos_rate_pct")),
                ("run_sql", dict(sql=OOS_BY_INST)),
                ("chart", dict(type="hbar", title="March 2025 OOS rate by instrument (%)", sql=OOS_BY_INST, x="instrument_id", y="oos_rate_pct")),
                ("run_sql", dict(sql=OOS_WEEKLY)),
                ("chart", dict(type="line", title="Weekly mean Assay result: HPLC-03 vs other HPLCs (spec 95-105 %)", sql=OOS_WEEKLY, x="week", y="mean_assay_pct", series="instrument_group")),
                ("run_sql", dict(sql=OOS_EVENTS)), ("search_docs", dict(query="OOS investigation instrument drift calibration")), ("data_quality", {})]

        def final():
            m = dict(self.q("SELECT strftime(completed_date,'%Y-%m'), round(100.0*avg((status='OOS')::int),2) FROM tests GROUP BY 1"))
            base = sum(v for k, v in m.items() if k != "2025-03") / (len(m) - 1)
            tot, oos, rate = self.q("""SELECT count(*), count(*) FILTER (status='OOS'), round(100.0*avg((status='OOS')::int),1) FROM tests WHERE instrument_id='HPLC-03' AND strftime(completed_date,'%Y-%m')='2025-03'""")[0]
            all_oos = self.q("SELECT count(*) FILTER (status='OOS') FROM tests WHERE strftime(completed_date,'%Y-%m')='2025-03'")[0][0]
            drift, before = self.q("""SELECT round(avg(result_value) FILTER (completed_date BETWEEN '2025-03-03' AND '2025-03-23'),2),
                                             round(avg(result_value) FILTER (completed_date BETWEEN '2025-01-01' AND '2025-02-28'),2)
                                      FROM tests WHERE instrument_id='HPLC-03' AND method='Assay' AND result_value<200""")[0]
            cal = self.q("SELECT start_date FROM instrument_events WHERE instrument_id='HPLC-03' AND event_type='Calibration' AND start_date>='2025-03-01' ORDER BY 1 LIMIT 1")[0][0]
            return (f"**The March 2025 OOS rate rose to {m['2025-03']}% (about {base:.1f}% in other months), and the increase traces to one instrument: HPLC-03 in Lab B.**\n\n"
                    f"- HPLC-03 had {oos} OOS results out of {tot} tests in March ({rate}%), which is {oos / all_oos:.0%} of all March OOS results across the lab.\n"
                    f"- Its mean Assay result moved from {before}% (Jan-Feb) to {drift}% during 3-23 March, towards the 105% upper limit, while the other HPLCs stayed flat. The shift is a step, not noise.\n"
                    f"- The drift stops after the calibration logged on {cal}, and the instrument returns to its normal OOS rate in April.\n\n"
                    "**Caveats and next steps:** the data shows *where and when*; it does not prove *why*. A sustained one-directional shift that ends at calibration is consistent with calibration drift, "
                    "but an investigator should verify the calibration record, system-suitability results and standard preparation for HPLC-03 before concluding. "
                    "Assay results reported during 3-23 March on HPLC-03 may need review under the OOS procedure. "
                    "The data_quality check also flagged unrelated record issues (see the Data Review tab); none of them explain this pattern.\n\n*(Demo mode: scripted model, real queries.)*")
        return plan, final

    def _on_time(self):
        self.scope = "February 2025, Related Substances and LCMS-02"
        plan = [("get_schema", {}), ("run_sql", dict(sql=OT_MONTHLY)),
                ("chart", dict(type="line", title="Monthly on-time delivery (%)", sql=OT_MONTHLY, x="month", y="on_time_pct")),
                ("run_sql", dict(sql=OT_METHOD)),
                ("chart", dict(type="bar", title="Feb 2025 on-time delivery by method (%)", sql=OT_METHOD, x="method", y="on_time_pct")),
                ("run_sql", dict(sql=OT_DOWN)),
                ("chart", dict(type="line", title="Average wait before LC-MS testing starts (days), by week sample received", sql=OT_WAIT, x="week_received", y="avg_wait_days")),
                ("data_quality", {})]

        def final():
            ot = dict(self.q("SELECT strftime(completed_date,'%Y-%m'), round(100.0*avg((completed_date<=due_date)::int),1) FROM tests JOIN samples USING(sample_id) WHERE completed_date>=start_date AND due_date>=received_date GROUP BY 1"))
            base = sum(v for k, v in ot.items() if k != "2025-02") / (len(ot) - 1)
            rs = self.q("""SELECT count(*), round(100.0*avg((completed_date<=due_date)::int),1), round(avg(completed_date-received_date),1) FROM tests JOIN samples USING(sample_id)
                           WHERE method='Related Substances' AND strftime(completed_date,'%Y-%m')='2025-02' AND completed_date>=start_date AND due_date>=received_date""")[0]
            ev = self.q("SELECT instrument_id, start_date, end_date FROM instrument_events WHERE event_type='Breakdown' AND instrument_id='LCMS-02' AND start_date>='2025-02-01' LIMIT 1")[0]
            w_in, w_out = self.q("""SELECT round(avg(t.start_date - s.received_date) FILTER (s.received_date BETWEEN '2025-02-10' AND '2025-02-24'),1),
                                           round(avg(t.start_date - s.received_date) FILTER (s.received_date BETWEEN '2025-01-06' AND '2025-02-07'),1)
                                    FROM tests t JOIN samples s USING(sample_id) JOIN instruments i USING(instrument_id) WHERE i.instrument_type='LC-MS' AND t.completed_date>=t.start_date""")[0]
            return (f"**On-time delivery fell to {ot['2025-02']}% in February 2025 (about {base:.1f}% in other months). The drop is concentrated in Related Substances, which depends on LC-MS, and coincides with the LCMS-02 breakdown.**\n\n"
                    f"- Related Substances: {rs[1]}% on time across {rs[0]} tests, with average turnaround {rs[2]} days. Every other method stayed near 95%.\n"
                    f"- {ev[0]} was logged as broken down from {ev[1]} to {ev[2]}. During that window LC-MS work waited {w_in} days on average before starting, versus {w_out} days in the weeks before.\n"
                    "- On-time delivery recovers to normal after the instrument returns to service.\n\n"
                    "**Caveats and next steps:** the timing and the single-method concentration point to lost LC-MS capacity (one instrument serving the queue), which is an inference from the data. "
                    "Verify with the lab's work-allocation records, and consider whether cross-training or an HPLC fallback for some Related Substances work would reduce single-point-of-failure risk. "
                    "On-time and turnaround exclude records with impossible dates (flagged by the data-quality checks).\n\n*(Demo mode: scripted model, real queries.)*")
        return plan, final

    def _deviations(self):
        self.scope = "Documentation deviations from April 2025"
        plan = [("get_schema", {}), ("run_sql", dict(sql=DV_MONTHLY)),
                ("chart", dict(type="line", title="Deviations opened per month, by category", sql=DV_MONTHLY, x="month", y="deviations", series="category")),
                ("run_sql", dict(sql=DV_ANALYST)),
                ("chart", dict(type="bar", title="Documentation deviations since 1 Apr 2025, by analyst", sql=DV_ANALYST, x="analyst_id", y="documentation_deviations")),
                ("run_sql", dict(sql=DV_COHORT)),
                ("chart", dict(type="bar", title="Documentation deviations per analyst since 1 Apr 2025", sql=DV_COHORT, x="cohort", y="per_analyst"))]

        def final():
            pre, post = self.q("""SELECT round(avg(n) FILTER (m < '2025-04'),1), round(avg(n) FILTER (m >= '2025-04'),1) FROM (
                                    SELECT strftime(opened_date,'%Y-%m') m, count(*) n FROM deviations WHERE category='Documentation' GROUP BY 1)""")[0]
            new, old = self.q(DV_COHORT)[1], self.q(DV_COHORT)[0]
            share = self.q("""SELECT round(100.0*count(*) FILTER (a.join_date>='2025-01-01')/count(*)) FROM deviations d JOIN analysts a USING(analyst_id)
                              WHERE d.category='Documentation' AND d.opened_date>='2025-04-01'""")[0][0]
            minor = self.q("SELECT round(100.0*count(*) FILTER (severity='Minor')/count(*)) FROM deviations WHERE category='Documentation' AND opened_date>='2025-04-01'")[0][0]
            o_pre, o_post = self.q("""SELECT round(avg(n) FILTER (m < '2025-04'),1), round(avg(n) FILTER (m >= '2025-04'),1) FROM (
                                       SELECT strftime(opened_date,'%Y-%m') m, count(*) n FROM deviations WHERE category <> 'Documentation' GROUP BY 1)""")[0]
            return (f"**Documentation deviations jumped from about {pre} to {post} per month starting April 2025, and {share:.0f}% of them were raised against analysts who joined in January 2025.**\n\n"
                    f"- {new[0]}: {new[3]} Documentation deviations per analyst since April ({new[1]} analysts), versus {old[3]} per analyst for earlier hires ({old[1]} analysts).\n"
                    f"- All other categories combined averaged {o_pre} per month before April and {o_post} after, so this is not a general quality decline.\n"
                    f"- {minor:.0f}% of the Documentation deviations are Minor severity, which fits record-keeping errors more than process failures.\n\n"
                    "**Caveats and next steps:** this shows the deviations cluster in a cohort; it does not prove a training gap. Check onboarding and GDP (good documentation practice) training records for the January cohort, "
                    "and whether their review workflow differs. A short refresher plus second-person review for the first months is a typical response, but that is for QA to decide.\n\n*(Demo mode: scripted model, real queries.)*")
        return plan, final

    def _dq(self):
        self.scope = "the whole built-in lab dataset"
        plan = [("get_schema", {}), ("data_quality", {}), ("run_sql", dict(sql=DQ_MISMATCH)),
                ("chart", dict(type="bar", title="Records missing an analyst or a result, by month", sql=DQ_MONTHLY, x="month", y="missing_analyst,missing_result"))]

        def final():
            s = dq.summary(self.db)
            fails = [r for r in s["rules"] if r["count"]]
            crit = [r for r in fails if r["severity"] == "Critical"]
            lines = "\n".join(f"- **{r['id']} {r['title']}** ({r['severity']}): {r['count']} records" for r in sorted(fails, key=lambda r: (r['severity'] != 'Critical', -r['count'])))
            return (f"**{s['flagged_tests']} of {s['total_tests']} test records ({100 - s['clean_pct']:.1f}%) fail at least one data-review rule; {len(crit)} of the {len(fails)} failing rules are Critical.**\n\n{lines}\n\n"
                    "The most serious pattern is results whose recorded status contradicts the specification (the table above shows OOS results recorded as Pass), because that can hide a genuine OOS from investigation. "
                    "Missing analyst IDs break attributability (ALCOA+), and tests logged on an instrument during a recorded breakdown suggest wrong instrument IDs.\n\n"
                    "**Next steps:** raise queries to the owning lab for the Critical groups first (the Data Review tab has a downloadable query list per rule). I have not changed any record.\n\n*(Demo mode: scripted model, real queries.)*")
        return plan, final

    def _create(self, **kw):
        if self.plan is None:
            first = kw["messages"][0]["content"]
            self.plan, self.final = self._scenario(first if isinstance(first, str) else "")
        i, self.i = self.i, self.i + 1
        usage = NS(input_tokens=1500 + 1100 * i, output_tokens=170)
        if i < len(self.plan):
            name, args = self.plan[i]
            return NS(stop_reason="tool_use", content=[_tu(i, name, **args)], usage=usage)
        text = self.final()
        cite = getattr(self, "cite", None)
        if cite:
            refs = "; ".join(f"[{h['doc']} section {h['section']}] {h['title']}" for h in KB.search(cite, 2))
            text = text.replace("*(Demo mode:", f"**Procedure references:** {refs}.\n\n*(Demo mode:", 1)
        text = text.replace("*(Demo mode: scripted model, real queries.)*", f"*(Demo mode: scripted model, real queries. Scope of this walkthrough: {self.scope}.)*")
        return NS(stop_reason="end_turn", content=[NS(type="text", text=text)], usage=usage)
