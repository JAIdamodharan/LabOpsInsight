"""Web UI + streaming API.  python -m analyst.server --db data/shop.duckdb [--demo] [--sandbox local|docker]"""
import argparse
import json
import threading
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .agent import MODEL, Analyst
from .db import Database
from .demo import DemoClient
from .sandbox import DockerSandbox, LocalSandbox

STATIC = Path(__file__).parent / "static"
MAX_QUESTION = 500
MAX_CONCURRENT = 2


def create_app(db_path: str, demo: bool = False, sandbox: str = "none",
               artifacts_dir: str = "artifacts", client=None) -> FastAPI:
    app = FastAPI(title="Autonomous Data Analyst")
    db = Database(db_path)
    Path(artifacts_dir).mkdir(exist_ok=True)
    make_sb = {"docker": DockerSandbox, "local": LocalSandbox}.get(sandbox)
    slots = threading.BoundedSemaphore(MAX_CONCURRENT)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/config")
    def config():
        return {"demo": demo, "model": "scripted-demo" if demo else MODEL, "python": bool(make_sb)}

    @app.get("/api/schema")
    def schema():
        return {"schema": db.schema()}

    @app.get("/api/ask")
    def ask(q: str):
        q = q.strip()
        if not q or len(q) > MAX_QUESTION:
            raise HTTPException(400, f"question must be 1-{MAX_QUESTION} characters")
        if not slots.acquire(blocking=False):
            raise HTTPException(429, "server busy; try again shortly")

        def stream():
            try:
                sb = make_sb(db_path, artifacts_dir) if make_sb else None
                c = client or (DemoClient(db, bool(sb)) if demo else None)
                agent = Analyst(db, client=c, sandbox=sb)
                sent = 0
                for ev in agent.run(q):
                    d = dict(ev.data)
                    if ev.kind == "tool_result" and d["name"] == "run_python":
                        d["files"] = [Path(f).name for f in agent.last_stats.artifacts[sent:]]
                        sent = len(agent.last_stats.artifacts)
                    yield f"data: {json.dumps({'kind': ev.kind, **d}, default=str)}\n\n"
                s = agent.last_stats
                yield "data: " + json.dumps({"kind": "done", "steps": s.steps, "sql_calls": s.sql_calls,
                                             "sql_errors": s.sql_errors, "python_calls": s.python_calls,
                                             "in_tok": s.input_tokens, "out_tok": s.output_tokens,
                                             "stop": s.stop_reason}) + "\n\n"
            except Exception as e:  # surface to the UI instead of a dropped connection
                yield f"data: {json.dumps({'kind': 'error', 'message': f'{type(e).__name__}: {e}'})}\n\n"
            finally:
                slots.release()

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    app.mount("/artifacts", StaticFiles(directory=artifacts_dir), name="artifacts")
    return app


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/shop.duckdb")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--demo", action="store_true", help="scripted model; no API key needed")
    ap.add_argument("--sandbox", choices=["docker", "local", "none"], default="none")
    a = ap.parse_args()
    uvicorn.run(create_app(a.db, a.demo, a.sandbox), host="127.0.0.1", port=a.port)


if __name__ == "__main__":
    main()
