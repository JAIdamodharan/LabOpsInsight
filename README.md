# Autonomous Data Analyst Agent

Ask a business question in plain English; the agent inspects the schema, writes and runs
read-only SQL, self-corrects on errors, and answers with evidence.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m scripts.make_data            # synthetic shop DB with a planted incident
export ANTHROPIC_API_KEY=...
.venv/bin/python -m analyst.cli "Why did revenue drop in March 2025?"
.venv/bin/python -m pytest                       # guardrail + agent-loop tests (no API key needed)
```

## Evaluation
```bash
.venv/bin/python -m eval.run_eval --price-in <USD/Mtok> --price-out <USD/Mtok>
```
22 questions (easy/medium/hard/trap) graded **deterministically**: numeric answers are compared to golden
SQL re-run against the DB (2% tolerance, handles `$1.42M`-style formats), categorical answers must contain
the golden value, diagnosis questions must name the planted cause, and *trap* questions (unanswerable from
the data) must be recognised rather than hallucinated. The report gives accuracy by difficulty, median latency,
tokens, cost per correct answer, self-correction rate, and a failure taxonomy. Results land in `eval/results/`.

## Web UI
```bash
.venv/bin/python -m analyst.server --sandbox local            # real model (needs ANTHROPIC_API_KEY)
.venv/bin/python -m analyst.server --demo --sandbox local     # scripted model, real queries: no key needed
```
Open http://127.0.0.1:8000. Steps stream live over SSE: SQL with result tables, Python with charts, then the
answer and run stats. Demo mode is labelled in the UI; only the agent's decisions are scripted, the SQL and
Python really execute.

## Python sandbox
`--sandbox docker` (recommended) runs model-written Python in a locked-down container; `--sandbox local`
is **dev-only** (env scrubbed, timeout, but no filesystem/network isolation). Charts are saved to `artifacts/`.
```bash
docker build -t analyst-sandbox sandbox/
.venv/bin/python -m analyst.cli --sandbox docker "Is the March drop statistically significant? Show a chart."
```

## Design
- `analyst/db.py` – guardrails: read-only connection, external file access disabled,
  single-statement allowlist, 200-row cap, 10s timeout.
- `analyst/agent.py` – hand-written tool loop with step and token budgets; yields events for live tracing.
- `scripts/make_data.py` – synthetic data with a known ground truth (mobile_app orders in the West/East
  regions fell ~85% in March 2025, cutting revenue ~18%), so answers can be graded automatically.

## Roadmap
- [x] M1 core loop + guardrails + tests
- [x] M2 eval harness (22 questions, deterministic grading, cost/latency/failure report)
- [ ] M2b first real baseline numbers (needs API key) + prompt iteration
- [x] M3 sandboxed Python tool for charts/stats (`DockerSandbox`: no network, read-only FS, non-root, mem/CPU/pid limits; build with `docker build -t analyst-sandbox sandbox/`)
- [ ] M5 add BIRD subset for an external, comparable number
- [x] M6 streaming web UI (FastAPI + SSE)
- [ ] M6b record demo video with a real model run
