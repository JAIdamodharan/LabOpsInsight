# LabOps Insight: lab operations analytics, data review and an investigation agent

An end-to-end analytics project for an analytical / QC laboratory (the kind of operation inside a CRO / CDMO).
It answers three questions a lab-operations analyst gets asked every week:

1. **How is the lab performing?** OOS rate, on-time delivery, turnaround, instrument utilization and deviations,
   with slicers, month-over-month deltas and plain-language auto-insights.
2. **Can we trust the data?** A rule-based data-review engine (the analytics equivalent of clinical-data-management
   edit checks) that produces a query list, tagged with ALCOA+ data-integrity attributes.
3. **Why did it change?** A natural-language agent that investigates root causes with SQL, charts and a data-quality
   tool, and is built to say "the data shows X; an investigator should verify Y" instead of overclaiming.

> **All data is synthetic and anonymised.** Nothing here comes from, or represents, any real company's systems.
> The three operational root causes and eight data defects were planted so every result can be verified exactly.

## What the data contains (and what the project finds)
`scripts/make_data.py` builds `data/labops.duckdb` (17k tests, 7k samples, 13 instruments, 30 analysts) and `data/ground_truth.json`.

| Planted root cause | What the dashboard / agent surfaces |
|---|---|
| HPLC-03 drifts +4.5 points on Assay results 3-23 Mar 2025 until recalibrated | OOS rate 3.8% vs ~1.6% baseline; HPLC-03 at 24.7%; weekly mean Assay jumps 100 -> 104.7% then recovers |
| LCMS-02 breaks down 10-24 Feb 2025; LC-MS work queues on LCMS-01 | On-time delivery 90% vs ~95%; Related Substances at 75%; wait before testing 0.8 -> 6 days |
| Six analysts joined Jan 2025 and raise most Documentation deviations from Apr | Documentation deviations ~4 -> ~17 per month; 87% against the new cohort; other categories flat |

| Planted data defect | Rule | Count (found exactly) |
|---|---|---|
| Duplicate test records | DQ-001 | 40 |
| Completed before started | DQ-002 | 35 |
| Missing analyst (not attributable) | DQ-003 | 60 |
| Unit / scale error | DQ-004 | 25 |
| Status contradicts result vs spec (20 OOS shown as Pass) | DQ-005 | 30 |
| Result missing | DQ-006 | 30 |
| Test started on an instrument that was down | DQ-007 | 20 |
| Sample due before received | DQ-008 | 15 |

## Run it
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m scripts.make_data                      # builds the synthetic lab database
.venv/bin/python -m analyst.server --demo --sandbox local  # open http://127.0.0.1:8000, no API key needed
.venv/bin/python -m pytest                                 # 120+ tests, no API key needed
```
Tabs: **Dashboard** (slicers, KPI cards with deltas, gauges, insights, trends, instrument table, OOS heatmap, 3 themes),
**Data Review** (rule results + downloadable query list per rule), **Analyze** (the agent, with Copy answer / Download report), **Data** (raw table browser), **Pinned**.
There is a light/dark toggle, and Print gives a clean PDF of any dashboard.

## Look and feel
- **Glass UI.** iOS-style frosted surfaces (`backdrop-filter` blur + saturation, specular edge highlights) over a slow-moving aurora backdrop, in light and dark mode. The dashboard has a **Glass** theme (default) plus the original Mint / Corporate / Navy themes. Respects `prefers-reduced-motion`, `prefers-reduced-transparency` and `prefers-contrast` (falls back to solid panels).
- **Icons.** The tab, dataset and empty-state icons are the PNGs in `analyst/static/icons/` (`dashboard`, `market-research`, `analyze`, `data`, `pinned`, `lab_operations`); swap a file to change an icon. Only the small utility glyphs (upload, search, print, light/dark) are inline SVG.
- **Logo.** `analyst/static/brand/` holds the logo made from a Canva design: `logo-mark.png` (header), `logo.png` / `logo-dark.png` (lockups for light / dark backgrounds), favicons and the app icon, plus the raw Canva export. `scripts/make_brand.py` regenerates them from a new Canva export.

### Interactions
| Do this | Get this |
|---|---|
| Click a **KPI card** | the analyst investigates why that metric changed |
| Click a **point or bar** in the volume / on-time charts | the whole dashboard jumps to that month |
| Click a **deviation bar**, a **turnaround bar** or an **instrument row** | the analyst investigates it |
| `Cmd/Ctrl + K` | command palette: jump to tabs, switch dataset or month, change theme, upload, or type a question |
| `/` | focus the question box from anywhere |
| Arrow keys on the tab bar | move between tabs |

KPI numbers count up, cards carry sparklines with the selected month marked, and a soft highlight follows the cursor over glass cards.
In demo mode every scripted answer states its fixed scope (e.g. "HPLC-03 in March 2025") and questions outside it are declined rather than answered with the wrong story.

## Use your own Excel data
Click **Upload Excel** (or drop a file anywhere on the page). `.xlsx` and `.csv` are accepted; each sheet becomes a table and the first row is read
as the column headers. Headers are cleaned to `snake_case`, duplicates are suffixed, and the types are inferred. **Numbers stored as text are kept as text and flagged**,
not silently converted. Download the example workbook from the dialog to see the format; it contains realistic mistakes (duplicate rows, `N/A` in a numeric column, a price typo...).

| For an uploaded dataset | What you get | Needs a model? |
|---|---|---|
| **Overview** | row / column / completeness / duplicate KPIs, auto-generated charts (trend by month, average by category, distribution), per-column profile | no |
| **Data Review** | generic checks GQ-001..009 (missing values, duplicates, numbers stored as text, inconsistent spelling or spaces, outliers, negative quantities, future dates, constant columns) with severity, examples and a CSV export | no |
| **Data** | sortable, paged table browser | no |
| **Analyze** | the same agent, with a domain-neutral prompt and a `data_quality` tool that runs the generic review | **yes** (`--provider ollama` is free) |

Demo mode only scripts answers for the built-in lab data, and says so on uploads instead of faking it.

**Upload safety** (it is a local app, but uploads are untrusted input): `.xlsm`/`.xls` rejected; limits on size (15 MB, enforced while streaming), expanded size (zip bombs),
sheets (10), rows (200k), columns (100) and cells (3M); `defusedxml` guards against XML bombs; formulas are never evaluated; hidden sheets are skipped; every dataset
is its own read-only DuckDB file with external file access disabled; dataset ids are random hex and never used as paths; uploads and deletes need a custom header (blocks cross-site requests)
and a Host allowlist blocks DNS rebinding. Nothing leaves your computer.

`--demo` uses a scripted model that runs **real** SQL and data-quality checks for four investigations (OOS, on-time delivery,
deviations, data quality) and declines anything else. For free-form questions use a real model:

| Provider | Cost | Setup |
|---|---|---|
| `ollama` | free, local | `ollama pull llama3.2 && ollama serve`, then `--provider ollama` (a 3B model is weak; expect mistakes) |
| `groq` / `gemini` | free tiers | key from the provider, `export GROQ_API_KEY=...`, `--provider groq` |
| `anthropic` | paid per token | `export ANTHROPIC_API_KEY=...` |

```bash
.venv/bin/python -m analyst.cli --provider groq "Why was the OOS rate on HPLC-03 so high in March 2025?"
.venv/bin/python -m analyst.server --provider groq --sandbox local
.venv/bin/python -m eval.run_eval --provider groq          # 26-question benchmark
```
Default model names can be retired by providers; pass `--model` to override.

## Design decisions worth discussing
- **GxP-style behaviour.** The agent reports numbers as recorded, never silently drops or "fixes" records, runs the data-review
  check before concluding on result/date metrics, and separates what the data shows from what it infers. The data-review engine
  is read-only: it raises queries, it never edits.
- **A real OOS spike must not be "cleaned away".** A test asserts that the HPLC-03 drift is not flagged as a data-quality issue.
- **Guardrails.** Read-only DuckDB, external file access off, single-statement SELECT allowlist, row cap, timeout, token and step budgets.
  Dashboard slicer values are validated against the catalog and bound as parameters, never interpolated.
- **Charts cannot show invented numbers.** The model passes SQL and column names; `analyst/charts.py` validates them and the browser draws.
- **Deterministic evaluation.** `eval/` grades 26 questions (easy / medium / hard diagnosis / traps that must not be hallucinated)
  against golden SQL re-run on the live database, with no LLM judge. Reports accuracy by difficulty, cost per correct answer and a failure taxonomy.
- **Model-agnostic.** The agent speaks one format; `analyst/llm.py` translates to any OpenAI-compatible endpoint, with a rescue parser for
  tool calls that weak models write as text and a guard against answering over an unresolved error.

## Known limitations (be honest about these)
- The built-in data is synthetic: it demonstrates method, not results on real lab data. Uploaded workbooks get the generic Overview / Data Review, but the lab dashboard, the lab rules (DQ-001..010) and the scripted demo are specific to the built-in lab schema; mapping an arbitrary LIMS export onto them would need a column-mapping step that does not exist yet.
- Uploads are stored unencrypted under `data/uploads` and are visible to anyone with access to this app; there are no user accounts.
- Excel reading: formulas show their last saved value (a formula that was never calculated shows blank); merged cells keep only their top-left value; the first non-empty row is always the header; dates stored as text stay text.
- DQ-004 only catches scale errors that push a result beyond 3x the upper spec limit; near-zero values slip through.
- "Utilization" assumes 20 available hours per weekday per instrument; real utilization needs scheduled-time and downtime data.
- The agent has **not** yet been benchmarked with a real model on the lab question set (the earlier 3B-model baseline was for a retired retail dataset and is
  intentionally not reported). Run `eval.run_eval` and record the numbers here.
- The Docker sandbox path is implemented and unit-tested but its isolation test needs a running Docker daemon.
- Not validated for regulated (GxP / 21 CFR Part 11) use: no audit trail, e-signatures or access control.

## Layout
`analyst/` agent, DB guardrails, charts, dashboard, lab data review (`dq.py`), generic profiling and review (`profile.py`), safe Excel/CSV ingest (`ingest.py`), dataset registry (`datasets.py`), LLM adapter, server, UI (`static/index.html`, `style.css`, `app.js`) · `scripts/make_data.py` data generator ·
`eval/` benchmark · `sandbox/` Docker image for the optional Python tool · `tests/`

## Results to fill in after your own runs
| Model | Accuracy (26 q) | Cost / correct answer | Median latency |
|---|---|---|---|
| (run `python -m eval.run_eval --provider ...`) | | | |
