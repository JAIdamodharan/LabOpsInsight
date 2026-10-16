# api/

`index.py` is the Vercel entrypoint for the public, read-only demo (see `docs/DEPLOY.md`). It builds the synthetic database into `/tmp` on a cold start and starts the app with `public=True`. Locally you do not use it; run `python -m analyst.server` instead.
