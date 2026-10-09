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

## Design
- `analyst/db.py` – guardrails: read-only connection, external file access disabled,
  single-statement allowlist, 200-row cap, 10s timeout.
- `analyst/agent.py` – hand-written tool loop with step and token budgets; yields events for live tracing.
- `scripts/make_data.py` – synthetic data with a known ground truth (mobile_app orders in the West
  region fell ~65% in March 2025), so answers can be graded automatically.

## Roadmap
- [x] M1 core loop + guardrails + tests
- [ ] M2 verified self-correction metrics on real runs
- [ ] M3 sandboxed Python tool (Docker) for charts/stats
- [ ] M5 eval harness (BIRD subset + hand-written questions): accuracy, cost, latency, failure taxonomy
- [ ] M6 streaming UI + demo video
