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

## 2. Deploy on Vercel with Groq answering live

You need three things: this GitHub repository, a Vercel account (the free Hobby plan works), and a Groq key from https://console.groq.com (free, no card). Use a key made just for this site so you can revoke it on its own.

### Step by step

1. On vercel.com choose **Add New, Project**, pick the GitHub repository and click **Import**. Leave the framework as **Other** and the build settings empty. `vercel.json` and `api/index.py` are picked up automatically.
2. Before the first deploy open **Environment Variables** and add these (apply to Production):

   | Name | Value | Why |
   |---|---|---|
   | `GROQ_API_KEY` | your key | the model provider |
   | `LABOPS_DEMO_LIVE` | `1` | turn on real answers (without it the site stays scripted) |
   | `ANALYST_PROVIDER` | `groq` | be explicit about the provider |
   | `ANALYST_MODEL` | `openai/gpt-oss-120b` | or a smaller model to stretch the free quota |

3. Click **Deploy**. When it finishes, open the URL and ask a question: the top-right badge shows the model name instead of "DEMO · scripted model" when live answers are on.
4. Add shared counters (strongly recommended, see "Protection" below): **Storage, Create Database, Upstash for Redis (free)**, connect it to the project, then **Redeploy**. The integration adds `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN` for you; nothing else to configure.
5. Turn on Vercel's own protection (see below), then share the link.

Command-line alternative to the steps above (after `npm i -g vercel`):

```bash
vercel login
vercel link
vercel env add GROQ_API_KEY production
vercel env add LABOPS_DEMO_LIVE production
vercel --prod
```

Every `git push` to `main` redeploys automatically once the project is linked. Optional custom domain: add it in Vercel and set `LABOPS_ALLOWED_HOSTS=your.domain` (`*.vercel.app` is already accepted).

### Protection against abuse (so strangers cannot use up your quota)

The key is the asset. A visitor cannot read it (it only exists in Vercel's server environment), but a visitor can spend it, so there are layers, and each one fails safe by falling back to the scripted walkthrough instead of taking the site down.

| Layer | What it does | Where |
|---|---|---|
| Daily budget per visitor | 6 live answers per address per day, 3 per minute (set with `LABOPS_LIVE_IP_DAILY`, `LABOPS_LIVE_IP_MINUTE`) | `analyst/quota.py` |
| Global daily budget | 200 live answers per day for everyone (`LABOPS_LIVE_GLOBAL_DAILY`). This caps the worst case no matter how many addresses an attacker uses | `analyst/quota.py` |
| Cost cap per answer | at most 6 agent steps, 40,000 tokens and 300 characters per question | `analyst/quota.py` |
| Shared counters | with Upstash the counters are the same for every serverless instance. Without it they are per instance, so the real limit can be several times higher | `analyst/quota.py` |
| Fail closed | if the counter store cannot be reached, live answers pause and the scripted demo answers | `analyst/quota.py` |
| Read only | no accounts, uploads, sandbox or anything that writes | `create_app(public=True)` |
| Per-address API limit and one analysis at a time | blunts scripts that hammer the site | `Guard`, `server.py` |
| Cross-site refusal, security headers, CSP | stops other websites from making your visitors' browsers spend your quota | `Guard` |

A visitor who runs out of live answers sees a short notice and still gets the scripted walkthrough for that question.

Worst case per day with the defaults: 200 answers at no more than 40,000 tokens each. On Groq's free tier you cannot be billed at all (the limit is simply reached); if you move to a paid tier, set a spending limit in the Groq console as well.

Add Vercel's protection in the dashboard (these happen before your code runs, so they also stop floods that would otherwise cost function time):

1. **Firewall, Bot Protection / Attack Challenge Mode:** turn on the challenge for suspicious traffic (available on the Hobby plan).
2. **Firewall, Custom Rules:** add a rate-limit rule for the path `/api/ask`, for example 10 requests per minute per IP, action "Deny" or "Challenge" (rule limits depend on your plan).
3. **Settings, Deployment Protection:** leave production public, and keep preview deployments protected so strangers cannot reach your test builds.

Rotate the Groq key right away if you ever see unexpected usage: create a new key in the Groq console, change `GROQ_API_KEY` in Vercel and redeploy. To switch live answers off without touching code, set `LABOPS_DEMO_LIVE` to `0` and redeploy.

### What the hosted demo is, and is not

| Feature | Hosted demo | Run locally |
|---|---|---|
| Dashboard, Data Review, Predict, Why LabOps, procedure search | yes | yes |
| Sign-in, roles, audit log | no (public, read only) | yes |
| Upload your own Excel | no | yes |
| `run_python` sandbox | no | yes (`--sandbox local` or Docker) |
| Free-form questions | live with `LABOPS_DEMO_LIVE=1` and a key, within the budgets above; otherwise scripted | yes with any provider |

Why the limits: a Vercel function has a read-only disk (only `/tmp`, which is wiped between cold starts), no long-lived memory, and anyone with the link can use it. Accounts, uploads and a code sandbox would either not survive or be unsafe on a public link, so the public mode switches them off by design. For private team use, run the full app locally or in a container (`sandbox/Dockerfile`).

### Things to know

- The function limit is 60 seconds (`maxDuration` in `vercel.json`). A long investigation on a slow model can hit it; the page then shows the error and you can ask again.
- The first Predict visit after an idle period trains the models (about 10 seconds), because the function starts cold.
- The bundle is about 330 MB uncompressed (numpy, pandas, scipy, scikit-learn, DuckDB). That fits Vercel's Python limit but not by a wide margin, so avoid adding heavy dependencies to `requirements.txt`.
- An address behind a shared network (a school, an office) shares one visitor budget. Raise `LABOPS_LIVE_IP_DAILY` if that matters for your audience.
- I could not run an actual Vercel build from this repository's development environment. The entrypoint was tested locally; check the first deployment's build log and function logs.

## 3. Run it locally instead

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # add GROQ_API_KEY
python scripts/make_data.py     # builds data/labops.duckdb
python -m analyst.server --provider groq --sandbox local
```
