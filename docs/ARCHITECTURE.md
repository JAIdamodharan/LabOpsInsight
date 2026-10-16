# Architecture

```
browser (vanilla JS, Chart.js)  <--SSE/JSON-->  FastAPI app (analyst/server.py)
                                                 |-- Guard middleware: cross-site refusal, per-IP limit, security headers, CSP
                                                 |-- auth.py: scrypt passwords, hashed server-side sessions, RBAC, audit log (SQLite)
                                                 |-- datasets.py + ingest.py: safe Excel/CSV upload -> per-user DuckDB files
                                                 |-- dashboard.py / dq.py / profile.py: KPIs, data-review rules, upload profiling
                                                 |-- predict.py: forecasts, late-delivery model, EWMA drift monitor (scikit-learn)
                                                 |-- story.py: facts for the "Why LabOps" page, computed from the data
                                                 `-- agent.py: tool-using LLM loop
                                                       tools: get_schema, run_sql, chart, data_quality, search_docs, run_python
                                                       |-- db.py: read-only, single-statement SELECT guard, row cap, timeout
                                                       |-- rag.py + knowledge/*.md: local BM25 over synthetic SOPs
                                                       |-- sandbox.py: optional isolated Python (Docker, or local for development)
                                                       `-- llm.py: one adapter for Anthropic and any OpenAI-compatible API (Groq, Gemini, ...)
```

## Key decisions

- **The model never sees more than it needs.** SQL results are trimmed to 25 rows for the model (the user still sees every row), older tool results are shortened before being re-sent, and the reply size is capped, because free tiers limit tokens per minute.
- **Guardrails are in code, not in the prompt.** Only a single read-only statement runs, with external file access disabled, a row cap and a timeout.
- **Honest answers.** The agent must cite procedure sections, take numbers only from SQL, and say when something is not covered. On the last allowed step it is forced to write a conclusion from what it found.
- **Validation you can trust.** Risk models are tested on a later period than they were trained on. A test-level OOS classifier that scored 0.46 ROC AUC was removed and replaced by a drift monitor that the planted incident verifies.
- **Verifiable data.** The synthetic dataset has planted incidents and defects with exact counts in `data/ground_truth.json`; tests and the benchmark check the app finds them.
- **Two run modes.** Private (accounts, roles, uploads) and public (`create_app(public=True)`: read and ask only), the second for hosting a shared demo.

## Request lifecycle for a question

1. `GET /api/ask?q=...` passes the Guard, auth and rate limits, and takes the per-user (or per-visitor) lock.
2. The agent runs in a worker thread and streams events over server-sent events; keepalives and visible rate-limit waits are relayed.
3. Closing the page or pressing Stop cancels the worker and releases the lock immediately.

## Tests

`python -m pytest -q` (about 450 tests). They cover guardrails, the agent loop with fake models, auth and RBAC, upload safety, the dashboard and data-review numbers against ground truth, the prediction module, retrieval, the public mode, and static-page invariants (every id the script uses exists, no unsafe DOM sinks, palette contrast).
