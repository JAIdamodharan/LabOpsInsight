# Deploying: GitHub and Vercel

## 1. Push to GitHub

Check first that no secret is tracked (this should print nothing):

```bash
git ls-files | grep -E "(^|/)\.env$|auth\.db|\.pem$|\.key$"
git grep -n -E "(gsk_|sk-ant-|sk-or-|AIza|xai-)[A-Za-z0-9_-]{20,}" -- . ':!tests'
```

Then create an empty repository on github.com (no README, no .gitignore, no licence) and run:

```bash
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

`.env` is git-ignored, so your key stays on your machine. The CI workflow in `.github/workflows/ci.yml` runs the tests on every push and fails if a key-like string is ever committed.

If a real key was ever committed, rotate it in the provider console. Deleting the file is not enough because git keeps history.

## 2. Deploy the public demo on Vercel

1. In Vercel choose **Add New, Project**, import the GitHub repository, and keep the defaults. `vercel.json` and `api/index.py` are detected automatically (framework preset: Other).
2. Deploy. The first request builds the synthetic lab database in `/tmp` (about a second) and trains the Predict models (about 10 seconds), so the first Predict visit after a cold start is slow.
3. Optional, to let visitors ask free-form questions with a real model: under **Settings, Environment Variables** add `GROQ_API_KEY` and `LABOPS_DEMO_LIVE=1`. Without them the demo answers with the scripted walkthrough and spends no quota.
4. Optional custom domain: add it in Vercel and set `LABOPS_ALLOWED_HOSTS=your.domain` so the app accepts that Host name (`*.vercel.app` is already accepted).

### What the hosted demo is, and is not

| Feature | Hosted demo | Run locally |
|---|---|---|
| Dashboard, Data Review, Predict, Why LabOps, procedure search | yes | yes |
| Sign-in, roles, audit log | no (public, read only) | yes |
| Upload your own Excel | no | yes |
| `run_python` sandbox | no | yes (`--sandbox local` or Docker) |
| Free-form questions | scripted unless `LABOPS_DEMO_LIVE=1` and a key are set | yes with any provider |
| Per-visitor limits | yes, by forwarded address (6 questions per minute) | per signed-in user |

Why these limits: a Vercel function has a read-only disk (only `/tmp`, which is wiped between cold starts), no long-lived memory, and anyone with the link can use it. Accounts, uploads and a code sandbox would either not survive or be unsafe on a public link, so the public mode switches them off by design (`create_app(public=True)`). For private team use, run the full app locally or in a container (`sandbox/Dockerfile`).

### Things to know

- The rate limiter lives in memory, so it is per function instance, not global. It stops casual abuse, not a determined attacker. If you enable the live mode, set a spending or rate cap on the provider key.
- The function limit is 60 seconds (`maxDuration` in `vercel.json`). A long investigation on a slow free model can hit it; the page then shows the error and you can ask again.
- The bundle is about 330 MB uncompressed (numpy, pandas, scipy, scikit-learn, DuckDB). That fits Vercel's Python limit but not by a wide margin, so avoid adding heavy dependencies to `requirements.txt`.
- I could not run an actual Vercel build from this repository's development environment. The entrypoint was tested locally with the same environment (`LABOPS_TMP`, public mode, a real server); check the first deployment's build log and function logs.

## 3. Run it locally instead

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # add GROQ_API_KEY
python scripts/make_data.py     # builds data/labops.duckdb
python -m analyst.server --provider groq --sandbox local
```
