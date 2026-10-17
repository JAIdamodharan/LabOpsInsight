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

## Screenshots

All screens use the built-in synthetic lab data (demo mode, scripted answers).

**Why LabOps**: a full-width story page with numbers computed from the data.
![Why LabOps story page](docs/screenshots/story.jpg)

**Dashboard** (dark and light):
![Dashboard, dark mode](docs/screenshots/dashboard.jpg)
![Dashboard, light mode](docs/screenshots/dashboard-light.jpg)

**Data Review**: rule-based checks tagged with ALCOA+ principles.
![Data Review](docs/screenshots/data-review.jpg)

**Predict**: forecasts with ranges, and the drift early-warning chart that catches the March HPLC-03 incident.
![Predict](docs/screenshots/predict.jpg)

**Analyze**: the agent's SQL, chart, cited procedures and final answer.
![Analyze](docs/screenshots/analyze.jpg)

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

## Quick start
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python scripts/make_data.py                                # builds the synthetic lab database
cp .env.example .env                                       # optional: add a free GROQ_API_KEY for real answers
python -m analyst.server --demo --sandbox local            # http://127.0.0.1:8000
python -m pytest -q                                        # about 450 tests, no key or network needed
```
`--demo` uses the built-in lab data and answers with a real model when a key is found in `.env`, otherwise with a scripted walkthrough (add `--scripted` to force that). Without `--demo` the app starts in private mode with sign-in; the first visit creates the admin account. More in [docs/DEPLOY.md](docs/DEPLOY.md) (GitHub, Vercel, running locally) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

**Hosted demo:** the repository deploys to Vercel as a public, read-only demo (no accounts, no uploads). See [docs/DEPLOY.md](docs/DEPLOY.md).

Tabs: **Why LabOps** (the story, with numbers computed from the data), **Dashboard** (slicers, KPI cards with deltas, gauges, insights, trends, instrument table, OOS heatmap, 3 themes),
**Data Review** (rule results + downloadable query list per rule), **Predict** (forecasts, late-delivery risk, drift warning), **Analyze** (the agent, with Copy answer / Download report), **Data** (raw table browser), **Pinned**.
There is a light/dark toggle, and Print gives a clean PDF of any dashboard.

## Accounts, roles and security
The app asks you to sign in. **On first run it shows a setup screen that creates the first account, which becomes the admin**; after that, admins add everyone else
(Menu -> Manage users). Nothing is created for you, so there are no default passwords.
```bash
.venv/bin/python -m analyst.server --demo           # accounts on (default); data/auth.db is created next to the lab database
.venv/bin/python -m analyst.server --demo --no-auth # single-user local mode: no sign-in, everyone is an admin (also LABOPS_AUTH=0)
.venv/bin/python -m analyst.auth create-user alice --role analyst   # manage users from the terminal (password is prompted, never a flag)
.venv/bin/python -m analyst.auth list-users | set-password NAME | disable NAME | enable NAME
```

| Role | Can do |
|---|---|
| **viewer** | read the dashboard, data review, tables and profiles |
| **analyst** | + ask the analyst, upload datasets, delete **own** datasets |
| **admin** | + manage users and roles, read the audit log, see and delete **any** dataset |

Uploaded datasets are **private to their owner** (admins can see all); another user gets a 404, so even the existence of a dataset is not revealed. The UI hides what a role cannot do, but every route re-checks on the server.

**What is enforced** (all covered by `tests/test_auth_store.py`, `tests/test_auth_api.py`, `tests/test_static.py`):
- *Passwords:* scrypt with a per-user salt, constant-time compare, a policy (10+ chars, two kinds, not the username, not a common password), and a dummy hash for unknown users so timing does not reveal which usernames exist. Login errors are identical for "no such user" and "wrong password".
- *Sessions:* random 256-bit token in an `HttpOnly`, `SameSite=Strict` cookie (`Secure` over HTTPS); only the token's SHA-256 is stored server-side. 12 h idle / 7 day absolute expiry. Sessions are revoked on logout, password change (other devices), role change, disable and delete, and a new login replaces the browser's previous token.
- *Rate limiting:* per IP for every API call, per user for the API, plus tighter buckets for sign-in (and a lockout after 5 failures per IP+username, 25/hour per username across IPs), questions, uploads and password changes; one running analysis per user. Answers carry `Retry-After` and the UI shows a live countdown.
- *Cross-site and injection defences:* mutating requests need an `X-Requested-With` header, cross-site fetches are refused via `Sec-Fetch-Site`, Host allowlist against DNS rebinding, a strict Content-Security-Policy (no inline scripts, `script-src` limited to this origin and cdnjs), `nosniff`, frame denial, `no-store` on API responses, upload size enforced while streaming.
- *Admin safety:* the last active admin cannot be demoted, disabled or deleted; admins cannot lock themselves out; admin-set passwords must be changed at next sign-in.
- *Audit log* (admins only): sign-ins, failures, lockouts, logouts, password changes, uploads, deletes, role changes, denied actions and questions asked (length + short hash only: never passwords or question text).
- *Logout* clears the cookie, the session row, and the browser-side caches (recent questions, pins, loaded data).

**Honest limits.** The rate limiter and lockout are in memory and per process (a restart resets them; run one process, or move them to Redis for several). There is no MFA, no email-based password reset (an admin resets it), and no account self-registration. The server speaks plain HTTP on `127.0.0.1`: if you ever expose it beyond localhost, put it behind an HTTPS reverse proxy (the cookie then becomes `Secure` automatically; `X-Forwarded-For` is deliberately not trusted, so limits would key on the proxy's address until you configure that). Chart.js is loaded from cdnjs (allowed by the CSP); vendoring it with a Subresource Integrity hash would remove that dependency. This is hardening for a local analytics tool, not a certified system (no 21 CFR Part 11 e-signatures).

## Look and feel
- **Glass UI.** iOS-style frosted surfaces (`backdrop-filter` blur + saturation, specular edge highlights) over a slow-moving aurora backdrop, in light and dark mode. The dashboard has a **Glass** theme (default) plus the original Mint / Corporate themes. Respects `prefers-reduced-motion`, `prefers-reduced-transparency` and `prefers-contrast` (falls back to solid panels).
- **Colour.** No purple and no gradient accents: a solid bright accent (cyan with dark text in dark mode, blue with white text in light mode) and a 12-colour chart palette defined once as `--c1..--c12` in `style.css`. Dark-mode series colours are saturated and every one is at least 4.5:1 against its glass card; light-mode ones are deeper and at least 3:1. Lines glow in dark mode and tooltips are always dark with white text. `tests/test_palette.py` enforces all of this (no purple-range colour in CSS/JS/HTML or the logo images, contrast floors, distinguishable series, vivid dark colours), so a later edit that breaks it fails the build.
- **Loading states.** A full-screen splash (your logo, an animated bar and what it is doing) while the session and workspace load, a thin progress bar at the top while any request is in flight, a blurred overlay for slow views, skeleton cards while charts load, and a spinner with a lockout countdown on the sign-in button.
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

**Free-tier rate limits.** Free plans cap requests and tokens per minute. When a provider answers "429", the app waits (honouring `Retry-After`, up to 60 s, 4 retries) and *says so*: the terminal prints `note: the provider is rate limiting you ... waiting 12s before retry 1/4`, and the web page shows a live countdown instead of a frozen "Working…". Closing the page or pressing Stop cancels a wait immediately, and the server sends keepalive pings so long waits never drop the connection. To stay under token limits the model is sent at most 40 rows of any SQL result (you still see the whole table). If you hit limits often, pick a smaller or higher-limit model with `ANALYST_MODEL`, or wait a minute between questions. Do not press keys in the terminal while a command is running: Ctrl+C there cancels it.

**Automatic model fallback.** If you did not choose a model and the preset default is not available to your key, the app asks the provider which models your key can use, picks the best chat model (preferring large instruct models with tool calling), prints a one-line note saying which one, and carries on. A model you choose yourself (`--model` or `ANALYST_MODEL`) is never replaced silently.

**If a provider says "model not found"** (names get retired, or restricted per project), ask your own key what it can use, then set one:
```bash
.venv/bin/python -m analyst.llm models --provider groq
```
and put `ANALYST_MODEL=<name>` (and optionally `ANALYST_PROVIDER=groq`) in `.env`, or pass `--model <name>`.

**Keeping your API key (do this once).** `export KEY=...` only lasts for the one terminal window it was typed in, which is why a new tab says "not set". Instead:
```bash
cp .env.example .env && chmod 600 .env
```
then open `.env` in your editor and paste the key after `GROQ_API_KEY=` (no quotes needed). The app reads `.env` from the folder you start it in. Real shell variables still win over the file; only `*_API_KEY` names and a few app settings are loaded (never `PATH`, `LD_PRELOAD` and the like); empty values are ignored; and it warns if the file is readable by other users. `.env` is git-ignored; never paste a key into chat, an issue or a commit. Placeholder text like `paste-your-key-here` is rejected with an explanation.

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
- Uploads are stored unencrypted under `data/uploads`. Each upload is private to the user who made it, but the files are readable by anyone with access to the server's disk.
- Excel reading: formulas show their last saved value (a formula that was never calculated shows blank); merged cells keep only their top-left value; the first non-empty row is always the header; dates stored as text stay text.
- DQ-004 only catches scale errors that push a result beyond 3x the upper spec limit; near-zero values slip through.
- "Utilization" assumes 20 available hours per weekday per instrument; real utilization needs scheduled-time and downtime data.
- The benchmark has not been run in full with a real model. A first run on Groq (`openai/gpt-oss-120b`) passed 16 of 26 questions; 9 of the 10 failures were requests that never completed because of the free tier's 8,000 tokens-per-minute cap (since reduced by sending fewer tokens per call), so they say little about answer quality. Run `eval.run_eval` and record your numbers below.
- The Docker sandbox path is implemented and unit-tested but its isolation test needs a running Docker daemon.
- Not validated for regulated (GxP / 21 CFR Part 11) use. There is sign-in, roles and an audit log, but no electronic signatures, validation documentation or change control.
- The hosted Vercel demo is public and read only by design, and its rate limits are per function instance, not global (see [docs/DEPLOY.md](docs/DEPLOY.md)).

## Layout
```
analyst/        the app: agent, DB guardrails, charts, dashboard, data review (dq.py, profile.py), safe ingest, auth and roles,
                predictions (predict.py), retrieval (rag.py + knowledge/), story page, LLM adapter, server and UI (static/)
api/            Vercel entrypoint for the public demo
docs/           DEPLOY.md and ARCHITECTURE.md
eval/           benchmark (26 questions, graded against golden SQL)
sandbox/        Docker image for the optional Python tool
sample_data/    15 synthetic workbooks for trying uploads
scripts/        data and asset generators
tests/          about 450 tests
```

## Results to fill in after your own runs
| Model | Accuracy (26 q) | Cost / correct answer | Median latency |
|---|---|---|---|
| `openai/gpt-oss-120b` on Groq, first partial run | 16 / 26 (9 failures caused by the token cap) | free | about 30 s |
| (run `python -m eval.run_eval --provider ...`) | | | |


## Image credits

Banner photos are from Wikimedia Commons and are in the public domain or CC0 (no attribution required; credited here anyway):

- `instrument-scientist.jpg` and `hero-instrument.jpg`: [U.S. Department of Energy - Science - 408 041 001](https://commons.wikimedia.org/wiki/File:U.S._Department_of_Energy_-_Science_-_408_041_001_(9788226824).jpg), public domain.
- `clean-lab.jpg`: [U.S. Department of Energy - Science - 395 006 004](https://commons.wikimedia.org/wiki/File:U.S._Department_of_Energy_-_Science_-_395_006_004_(9735099945).jpg), public domain.
- `glass-flasks.jpg`: [Birnenkolben.jpg](https://commons.wikimedia.org/wiki/File:Birnenkolben.jpg), CC0.
- `analyst-instrument.jpg`: [U.S. Department of Energy - Science - 395 014 001](https://commons.wikimedia.org/wiki/File:U.S._Department_of_Energy_-_Science_-_395_014_001_(10293874364).jpg), public domain (cropped).


## Predict tab (machine learning)

Lab dataset only. Everything is trained on the lab data when the tab is first opened (a few seconds) and cached.

- **Forecasts** (OOS rate, on-time delivery, tests per month, turnaround): the best of three simple models (last value, average, 6-month linear trend), chosen by one-step rolling-origin backtesting, with 80% intervals taken from backtest errors. With about 8 months of history the intervals are wide, and the page says so.
- **Late-delivery risk model:** logistic regression vs gradient boosting (scikit-learn), validated on a **time split** (train on the earlier 75% of completions, test on the later 25%), reporting ROC AUC, precision-recall AUC and top-decile lift, plus permutation importance and a "what-if" scorer. Features only use information known when a test starts; instrument history looks strictly backwards.
- **Drift early warning:** an EWMA control chart per instrument and method on robustly standardised results. It flags the planted HPLC-03 Assay drift within days of its start (3 Mar) and shows it clearing after recalibration. A smaller, weaker alarm elsewhere can be a false alarm.
- **What did not work, and was removed:** a test-level OOS classifier scored ROC AUC about 0.46 on held-out months (no better than chance): OOS results are under 2% and almost all come from one instrument drift that cannot be learned from the earlier months. The drift monitor is the honest replacement.

## Sample datasets for testing uploads

```bash
.venv/bin/python scripts/make_datasets.py
```

writes 15 synthetic, anonymised files to `sample_data/` (stability study, HPLC run log, sales, HR attrition, clinical vitals, inventory with expiry dates, balance calibration, support tickets, bioassay plates, environmental monitoring, batch release, deviations/CAPA, a deliberately messy file, a 60,000-row sensor CSV, and an 80-column wide file). Upload them with **Upload Excel**. A test checks that every file ingests cleanly.


## Procedure documents (RAG) and the demo

- The agent has a `search_docs` tool over eight **synthetic** lab procedures in `analyst/knowledge/` (specification limits, OOS investigation, HPLC calibration and drift, instrument downtime, turnaround policy, deviations and CAPA, analyst training, ALCOA+). Retrieval is local BM25 over document sections (`analyst/rag.py`): no embeddings and no extra API. Passages are tagged `[SOP-QC-012 section 2]` and the model is told to cite them, to take numbers only from SQL, and to say so when the documents do not cover something. A test checks that the specification table in the documents matches the data. These are teaching documents, not any real company's SOPs.
- `--demo` now means "built-in lab data". If a key is found (`ANALYST_PROVIDER`, or the first of `GROQ_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, ...) the demo answers with that real model, procedure search included. With no usable key it falls back to the scripted walkthrough (which also cites the procedures). Use `--demo --scripted` to force the scripted version and spend no quota.
- On the last allowed step the agent is told to stop investigating and write its answer, so a long investigation ends with a written conclusion instead of "max_steps".
- The "Why LabOps" hero slideshow uses wider crops of the same four photos (`hero-*.jpg`); sources and licences are as listed above.


## License

MIT, see [LICENSE](LICENSE). The photographs are separate works (public domain or CC0), credited above. All data in the project is synthetic.
