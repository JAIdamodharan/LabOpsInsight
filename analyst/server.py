"""Web UI + streaming API.  python -m analyst.server --db data/labops.duckdb [--demo] [--sandbox local|docker]

Security notes for a local app that accepts uploads: it binds to 127.0.0.1 only; a Host allowlist blocks DNS-rebinding;
mutating requests need a custom header (cross-site pages cannot send it without a CORS preflight, which is never granted);
upload size is enforced while streaming; dataset ids are random hex and never used as paths.
"""
import argparse
import json
import threading
from pathlib import Path
from urllib.parse import unquote

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import dashboard, dq, ingest
from .agent import MODEL, Analyst
from .datasets import LAB_ID, DatasetError, Registry
from .db import Database
from .demo import DemoClient
from .example import build_example_workbook
from .llm import add_cli_args, make_client
from .sandbox import DockerSandbox, LocalSandbox

STATIC = Path(__file__).parent / "static"
MAX_QUESTION = 500
MAX_CONCURRENT = 2
MAX_PAGE = 200
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DEMO_ONLY_LAB = ("**Demo mode only works with the built-in lab data.** To ask questions about your own workbook, start the server with a real model, "
                 "for example `python -m analyst.server --provider ollama` (free, local) or `--provider groq`. The Overview, Data Review and Data tabs "
                 "already work on your upload without any model.")


def _tables(db: Database) -> list[dict]:
    out = []
    for (t,) in db.con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main' ORDER BY 1").fetchall():
        n = db.con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        cols = [c for (c,) in db.con.execute("SELECT column_name FROM information_schema.columns WHERE table_name=? ORDER BY ordinal_position", [t]).fetchall()]
        out.append({"name": t, "rows": n, "columns": cols})
    return out


def create_app(db_path: str, demo: bool = False, sandbox: str = "none", artifacts_dir: str = "artifacts",
               client=None, model: str | None = None, upload_dir: str | None = None,
               allowed_hosts: tuple = ("localhost", "127.0.0.1", "[::1]", "testserver")) -> FastAPI:
    app = FastAPI(title="LabOps Insight")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))
    reg = Registry(db_path, upload_dir or Path(db_path).parent / "uploads")
    Path(artifacts_dir).mkdir(exist_ok=True)
    make_sb = {"docker": DockerSandbox, "local": LocalSandbox}.get(sandbox)
    slots = threading.BoundedSemaphore(MAX_CONCURRENT)
    upload_slots = threading.BoundedSemaphore(2)
    example = build_example_workbook()

    def get_db(dataset: str) -> Database:
        try:
            return reg.db(dataset)
        except DatasetError as e:
            raise HTTPException(e.status, str(e))

    def need_header(request: Request) -> None:
        if request.headers.get("x-requested-with") != "labops":
            raise HTTPException(403, "missing X-Requested-With header")

    def lab_only(dataset: str, what: str) -> Database:
        db = get_db(dataset)
        if dataset != LAB_ID:
            raise HTTPException(400, f"{what} is only available for the built-in lab dataset; use the Overview and Data Review for uploads")
        return db

    # ---- pages / config ---------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def index():
        version = int(max(p.stat().st_mtime for p in STATIC.iterdir() if p.is_file()))
        return HTMLResponse((STATIC / "index.html").read_text().replace("__V__", str(version)), headers={"Cache-Control": "no-store"})

    @app.get("/api/config")
    def config():
        return {"demo": demo, "model": "scripted-demo" if demo else (model or MODEL), "python": bool(make_sb),
                "max_upload_mb": ingest.MAX_BYTES // 10**6, "formats": [".xlsx", ".csv"], "limits": {"rows": ingest.MAX_ROWS, "columns": ingest.MAX_COLS, "sheets": ingest.MAX_SHEETS}}

    # ---- datasets ---------------------------------------------------------
    @app.get("/api/datasets")
    def datasets():
        return reg.list()

    @app.post("/api/datasets", status_code=201)
    async def upload(request: Request):
        name = unquote(request.headers.get("x-filename", "")).strip()
        if not name:
            raise HTTPException(400, "X-Filename header is required")
        try:
            declared = int(request.headers.get("content-length", "0"))
        except ValueError:
            declared = 0
        if declared > ingest.MAX_BYTES + 1024:
            raise HTTPException(413, f"The file is larger than the {ingest.MAX_BYTES // 10**6} MB limit.")
        chunks, size = [], 0
        async for chunk in request.stream():          # enforce the cap while streaming, not after buffering everything
            size += len(chunk)
            if size > ingest.MAX_BYTES:
                raise HTTPException(413, f"The file is larger than the {ingest.MAX_BYTES // 10**6} MB limit.")
            chunks.append(chunk)
        if not upload_slots.acquire(blocking=False):
            raise HTTPException(429, "Another upload is being processed; try again in a moment.")
        try:
            return await run_in_threadpool(reg.add, name, b"".join(chunks))
        except ingest.IngestError as e:
            raise HTTPException(400, str(e))
        except DatasetError as e:
            raise HTTPException(e.status, str(e))
        finally:
            upload_slots.release()

    @app.delete("/api/datasets/{ds_id}")
    def delete_dataset(ds_id: str, request: Request):
        need_header(request)
        try:
            reg.delete(ds_id)
        except DatasetError as e:
            raise HTTPException(e.status, str(e))
        return {"deleted": ds_id}

    @app.get("/api/example.xlsx")
    def example_workbook():
        return Response(example, media_type=XLSX, headers={"Content-Disposition": 'attachment; filename="example_orders.xlsx"'})

    # ---- analysis views ---------------------------------------------------
    @app.get("/api/schema")
    def schema(dataset: str = LAB_ID):
        return {"schema": get_db(dataset).schema()}

    @app.get("/api/profile")
    def profile_view(dataset: str = LAB_ID):
        get_db(dataset)
        return reg.profile(dataset)

    @app.get("/api/dashboard")
    def dash(month: str | None = None, lab: str | None = None, service_line: str | None = None, dataset: str = LAB_ID):
        db = lab_only(dataset, "The operations dashboard")
        try:
            return dashboard.build(db, month or None, lab or None, service_line or None)
        except ValueError as e:  # slicer values are checked against the catalog, never interpolated
            raise HTTPException(400, str(e))

    @app.get("/api/dq")
    def dq_summary(dataset: str = LAB_ID):
        return dq.summary(lab_only(dataset, "The lab data-review rules"))

    @app.get("/api/dq/{rule_id}")
    def dq_issues(rule_id: str, limit: int = 200, offset: int = 0, dataset: str = LAB_ID):
        try:
            return dq.issues(lab_only(dataset, "The lab data-review rules"), rule_id, limit, offset)
        except KeyError:
            raise HTTPException(404, "unknown rule")

    @app.get("/api/tables")
    def tables(dataset: str = LAB_ID):
        return _tables(get_db(dataset))

    @app.get("/api/table/{name}")
    def table(name: str, limit: int = 100, offset: int = 0, sort: str | None = None, desc: bool = False, dataset: str = LAB_ID):
        db = get_db(dataset)
        meta = {t["name"]: t for t in _tables(db)}
        if name not in meta:  # identifiers come from the catalog, never from user text
            raise HTTPException(404, "unknown table")
        cols = meta[name]["columns"]
        if sort is not None and sort not in cols:
            raise HTTPException(400, "unknown sort column")
        limit, offset = max(1, min(limit, MAX_PAGE)), max(0, offset)
        order = f' ORDER BY "{sort}" {"DESC" if desc else "ASC"}' if sort else ""
        res = db.run(f'SELECT * FROM "{name}"{order} LIMIT {limit} OFFSET {offset}')
        return {"name": name, "total": meta[name]["rows"], "columns": res.columns,
                "rows": [list(r) for r in res.rows], "offset": offset, "limit": limit}

    # ---- the agent --------------------------------------------------------
    @app.get("/api/ask")
    def ask(q: str, dataset: str = LAB_ID):
        q = q.strip()
        if not q or len(q) > MAX_QUESTION:
            raise HTTPException(400, f"question must be 1-{MAX_QUESTION} characters")
        db = get_db(dataset)
        kind = reg.kind(dataset)
        if not slots.acquire(blocking=False):
            raise HTTPException(429, "server busy; try again shortly")

        def stream():
            try:
                if demo and kind != "lab":
                    yield f"data: {json.dumps({'kind': 'answer', 'text': DEMO_ONLY_LAB})}\n\n"
                    yield "data: " + json.dumps({"kind": "done", "steps": 0, "sql_calls": 0, "sql_errors": 0, "python_calls": 0, "chart_calls": 0, "in_tok": 0, "out_tok": 0, "stop": "demo_unsupported"}) + "\n\n"
                    return
                sb = make_sb(reg.path(dataset), artifacts_dir) if make_sb else None
                c = client or (DemoClient(db, bool(sb)) if demo else None)
                agent = Analyst(db, client=c, model=model or MODEL, sandbox=sb, domain=kind)
                sent = 0
                for ev in agent.run(q):
                    d = dict(ev.data)
                    if ev.kind == "tool_result" and d["name"] == "run_python":
                        d["files"] = [Path(f).name for f in agent.last_stats.artifacts[sent:]]
                        sent = len(agent.last_stats.artifacts)
                    yield f"data: {json.dumps({'kind': ev.kind, **d}, default=str)}\n\n"
                s = agent.last_stats
                yield "data: " + json.dumps({"kind": "done", "steps": s.steps, "sql_calls": s.sql_calls,
                                             "sql_errors": s.sql_errors, "python_calls": s.python_calls, "chart_calls": s.chart_calls,
                                             "in_tok": s.input_tokens, "out_tok": s.output_tokens,
                                             "stop": s.stop_reason}) + "\n\n"
            except Exception as e:  # surface to the UI instead of a dropped connection
                yield f"data: {json.dumps({'kind': 'error', 'message': f'{type(e).__name__}: {e}'})}\n\n"
            finally:
                slots.release()

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    app.mount("/artifacts", StaticFiles(directory=artifacts_dir), name="artifacts")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/labops.duckdb")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--demo", action="store_true", help="scripted model for the built-in lab data; no API key needed")
    ap.add_argument("--sandbox", choices=["docker", "local", "none"], default="none")
    add_cli_args(ap)
    a = ap.parse_args()
    client, model = (None, None) if a.demo else make_client(a.provider, a.model, a.base_url)
    uvicorn.run(create_app(a.db, a.demo, a.sandbox, client=client, model=model), host="127.0.0.1", port=a.port)


if __name__ == "__main__":
    main()
