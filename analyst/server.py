"""Web UI + streaming API.  python -m analyst.server --db data/labops.duckdb [--demo] [--sandbox local|docker] [--no-auth]

Security model for a local app that has accounts and accepts uploads
- Authentication: username + password (scrypt), server-side sessions in an HttpOnly, SameSite=Strict cookie.
  On first run the setup screen creates the first admin. `--no-auth` (or LABOPS_AUTH=0) disables accounts for single-user local use.
- Authorisation (RBAC): every /api route checks a permission (read / ask / upload / delete_own / delete_any / users / audit).
  Uploaded datasets are private to their owner (admins can see all); other users get a 404, not a 403.
- Rate limiting: per-IP for everything, per-user for the API, tighter buckets for login (plus lockout), asks, uploads and
  password changes, and one running analysis per user. Answers carry Retry-After.
- CSRF / cross-site: mutating requests need a custom header, cross-site fetches are refused via Sec-Fetch-Site, the cookie is SameSite=Strict.
- Hardening: Host allowlist (DNS rebinding), CSP, nosniff, frame denial, no-store on API responses, upload size enforced while streaming.
- Audit log: logins, failures, uploads, deletes, role changes and asks (length + hash only; never passwords or question text).
"""
import argparse
import hashlib
import json
import os
import re
import threading
from pathlib import Path
from urllib.parse import unquote

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import auth as authmod
from . import dashboard, dq, ingest
from .agent import MODEL, Analyst
from .auth import PERMS, AuthError, AuthStore
from .datasets import LAB_ID, DatasetError, Registry
from .db import Database
from .demo import DemoClient
from .example import build_example_workbook
from .llm import add_cli_args, make_client
from .ratelimit import RateLimiter
from .sandbox import DockerSandbox, LocalSandbox

STATIC = Path(__file__).parent / "static"
COOKIE = "labops_session"
MAX_QUESTION = 500
MAX_CONCURRENT = 2
MAX_PAGE = 200
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ARTIFACT_RE = re.compile(r"^[0-9a-f]{8}_[\w.\-]{1,120}\.(png|svg|csv)$")
DEMO_ONLY_LAB = ("**Demo mode only works with the built-in lab data.** To ask questions about your own workbook, start the server with a real model, "
                 "for example `python -m analyst.server --provider ollama` (free, local) or `--provider groq`. The Overview, Data Review and Data tabs "
                 "already work on your upload without any model.")
# (max requests, window in seconds)
DEFAULT_LIMITS = dict(api_ip=(240, 60), api_user=(300, 60), login_ip=(12, 60), login_fail_ip_user=(5, 900), login_fail_user=(25, 3600),
                      ask=(12, 60), upload=(8, 600), password=(5, 900), admin=(60, 60))
CSP = ("default-src 'self'; script-src 'self' https://cdnjs.cloudflare.com; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
LOCAL_ADMIN = dict(id=0, username="local", role="admin", disabled=False, must_change=False, created=0, last_login=None, permissions=sorted(PERMS["admin"]))


def _tables(db: Database) -> list[dict]:
    out = []
    for (t,) in db.con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main' ORDER BY 1").fetchall():
        n = db.con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        cols = [c for (c,) in db.con.execute("SELECT column_name FROM information_schema.columns WHERE table_name=? ORDER BY ordinal_position", [t]).fetchall()]
        out.append({"name": t, "rows": n, "columns": cols})
    return out


class Guard:
    """Pure-ASGI middleware (so streaming responses are never buffered): cross-site refusal, per-IP limit, security headers."""

    def __init__(self, app, limiter: RateLimiter, limits: dict):
        self.app, self.limiter, self.limits = app, limiter, limits

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        hdr = {k.decode().lower(): v.decode("latin-1") for k, v in scope["headers"]}
        ip = (scope.get("client") or ("unknown", 0))[0]
        is_api = path.startswith("/api/")
        https = scope.get("scheme") == "https"

        async def send_wrapped(message):
            if message["type"] == "http.response.start":
                h = list(message.get("headers", []))
                extra = {"content-security-policy": CSP, "x-content-type-options": "nosniff", "x-frame-options": "DENY", "referrer-policy": "same-origin",
                         "cross-origin-opener-policy": "same-origin", "permissions-policy": "camera=(), microphone=(), geolocation=()"}
                if is_api or path.startswith("/artifacts/"):
                    extra["cache-control"] = "no-store"
                if https:
                    extra["strict-transport-security"] = "max-age=31536000"
                h += [(k.encode(), v.encode()) for k, v in extra.items() if k.encode() not in {x[0].lower() for x in h}]
                message = {**message, "headers": h}
            await send(message)

        if is_api or path.startswith("/artifacts/"):
            if hdr.get("sec-fetch-site") == "cross-site":
                return await JSONResponse({"detail": "Cross-site requests are not allowed."}, 403)(scope, receive, send_wrapped)
            if is_api:
                ok, wait = self.limiter.check(f"ip:{ip}", *self.limits["api_ip"])
                if not ok:
                    return await JSONResponse({"detail": f"Too many requests from this address. Try again in {int(wait) + 1}s."}, 429, headers={"Retry-After": str(int(wait) + 1)})(scope, receive, send_wrapped)
        await self.app(scope, receive, send_wrapped)


def create_app(db_path: str, demo: bool = False, sandbox: str = "none", artifacts_dir: str = "artifacts",
               client=None, model: str | None = None, upload_dir: str | None = None,
               allowed_hosts: tuple = ("localhost", "127.0.0.1", "[::1]", "testserver"),
               auth: bool | None = None, auth_db: str | None = None, limits: dict | None = None, store: AuthStore | None = None) -> FastAPI:
    auth_enabled = auth if auth is not None else os.environ.get("LABOPS_AUTH", "1") != "0"
    L = {**DEFAULT_LIMITS, **(limits or {})}
    limiter = RateLimiter()
    app = FastAPI(title="LabOps Insight", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(Guard, limiter=limiter, limits=L)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))
    reg = Registry(db_path, upload_dir or Path(db_path).parent / "uploads")
    users = (store or AuthStore(auth_db or Path(db_path).parent / "auth.db")) if auth_enabled else None
    Path(artifacts_dir).mkdir(exist_ok=True)
    art_root = Path(artifacts_dir).resolve()
    make_sb = {"docker": DockerSandbox, "local": LocalSandbox}.get(sandbox)
    slots = threading.BoundedSemaphore(MAX_CONCURRENT)
    upload_slots = threading.BoundedSemaphore(2)
    busy_users: dict[int, threading.Lock] = {}
    setup_lock = threading.Lock()
    example = build_example_workbook()

    # ------------------------------------------------------------ helpers
    def ip_of(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def too_many(wait: float, what: str = "requests"):
        raise HTTPException(429, f"Too many {what}. Try again in {int(wait) + 1}s.", headers={"Retry-After": str(int(wait) + 1)})

    def limit(key: str, spec: tuple, what: str = "requests"):
        ok, wait = limiter.check(key, *spec)
        if not ok:
            too_many(wait, what)

    def need_header(request: Request) -> None:
        if request.headers.get("x-requested-with") != "labops":
            raise HTTPException(403, "missing X-Requested-With header")

    def session_user(request: Request) -> dict | None:
        if not auth_enabled:
            return LOCAL_ADMIN
        return users.session_user(request.cookies.get(COOKIE, ""))

    def require(perm: str):
        def dep(request: Request) -> dict:
            user = session_user(request)
            if not user:
                raise HTTPException(401, "Not signed in.")
            if auth_enabled:
                limit(f"user:{user['id']}", L["api_user"])
                if user["must_change"] and request.url.path not in ("/api/auth/password", "/api/auth/logout", "/api/auth/status"):
                    raise HTTPException(403, "You must change your password before continuing.")
            if perm not in user["permissions"]:
                if auth_enabled:
                    users.audit("denied", user["username"], f"{perm} {request.method} {request.url.path}", ip_of(request))
                raise HTTPException(403, "Your role does not allow this.")
            return user
        return dep

    def audit(user: dict, action: str, detail: str, request: Request) -> None:
        if auth_enabled:
            users.audit(action, user["username"], detail, ip_of(request))

    def get_db(dataset: str, user: dict) -> Database:
        try:
            reg.require_access(dataset, user)
            return reg.db(dataset)
        except DatasetError as e:
            raise HTTPException(e.status, str(e))

    async def json_body(request: Request, max_bytes: int = 4096) -> dict:
        size, chunks = 0, []
        async for chunk in request.stream():
            size += len(chunk)
            if size > max_bytes:
                raise HTTPException(413, "Request body too large.")
            chunks.append(chunk)
        try:
            d = json.loads(b"".join(chunks) or b"{}")
        except ValueError:
            raise HTTPException(400, "Invalid JSON.")
        if not isinstance(d, dict):
            raise HTTPException(400, "Expected a JSON object.")
        return d

    def start_session(request: Request, user: dict) -> JSONResponse:
        users.delete_session(request.cookies.get(COOKIE, ""))        # a new login always replaces the browser's previous session (no fixation)
        token = users.create_session(user["id"], ip_of(request), request.headers.get("user-agent", ""))
        resp = JSONResponse({"user": user})
        resp.set_cookie(COOKIE, token, max_age=int(users.absolute), httponly=True, samesite="strict", secure=request.url.scheme == "https", path="/")
        return resp

    def lab_only(dataset: str, what: str, user: dict) -> Database:
        db = get_db(dataset, user)
        if dataset != LAB_ID:
            raise HTTPException(400, f"{what} is only available for the built-in lab dataset; use the Overview and Data Review for uploads")
        return db

    # ------------------------------------------------------------ pages
    @app.get("/", response_class=HTMLResponse)
    def index():
        version = int(max(p.stat().st_mtime for p in STATIC.rglob("*") if p.is_file()))
        return HTMLResponse((STATIC / "index.html").read_text().replace("__V__", str(version)), headers={"Cache-Control": "no-store"})

    @app.get("/artifacts/{name}")
    def artifact(name: str, user: dict = Depends(require("read"))):
        path = (art_root / name).resolve()
        if not ARTIFACT_RE.match(name) or path.parent != art_root or not path.is_file():
            raise HTTPException(404, "not found")
        return FileResponse(path)

    # ------------------------------------------------------------ auth
    @app.get("/api/auth/status")
    def auth_status(request: Request):
        user = session_user(request)
        return {"enabled": auth_enabled, "setup_required": bool(auth_enabled and users.count_users() == 0), "user": user, "roles": list(authmod.ROLES)}

    @app.post("/api/auth/setup")
    async def auth_setup(request: Request):
        need_header(request)
        if not auth_enabled:
            raise HTTPException(400, "Authentication is disabled.")
        limit(f"login-ip:{ip_of(request)}", L["login_ip"], "attempts")
        body = await json_body(request)
        with setup_lock:                                     # two simultaneous setups must not both succeed
            if users.count_users() > 0:
                raise HTTPException(409, "Setup has already been completed. Sign in instead.")
            try:
                user = users.create_user(body.get("username"), body.get("password"), "admin")
            except AuthError as e:
                raise HTTPException(e.status, str(e))
        users.audit("setup", user["username"], "first admin created", ip_of(request))
        resp = start_session(request, user)
        users.audit("login_success", user["username"], "after setup", ip_of(request))
        return resp

    @app.post("/api/auth/login")
    async def auth_login(request: Request):
        need_header(request)
        if not auth_enabled:
            raise HTTPException(400, "Authentication is disabled.")
        ip = ip_of(request)
        limit(f"login-ip:{ip}", L["login_ip"], "sign-in attempts")
        body = await json_body(request)
        name, pw = body.get("username"), body.get("password")
        uname = name.strip().lower()[:64] if isinstance(name, str) else ""
        k_pair, k_user = f"lf:{ip}:{uname}", f"lfu:{uname}"
        for key, spec in ((k_pair, L["login_fail_ip_user"]), (k_user, L["login_fail_user"])):
            wait = limiter.retry_after(key, *spec)
            if wait > 0:                                      # locked: do not even spend a password hash on it
                users.audit("login_locked", uname if authmod.USERNAME_RE.match(uname) else "(invalid)", "", ip)
                too_many(wait, "failed sign-in attempts")
        user = users.authenticate(name, pw) if isinstance(pw, str) and len(pw) <= authmod.MAX_PASSWORD else None
        if not user:
            limiter.hit(k_pair, L["login_fail_ip_user"][1]); limiter.hit(k_user, L["login_fail_user"][1])
            users.audit("login_failed", uname if authmod.USERNAME_RE.match(uname) else "(invalid)", "", ip)
            raise HTTPException(401, "Invalid username or password.")
        limiter.reset(k_pair)
        users.audit("login_success", user["username"], "", ip)
        return start_session(request, user)

    @app.post("/api/auth/logout")
    def auth_logout(request: Request):
        need_header(request)
        resp = JSONResponse({"ok": True})
        if auth_enabled:
            user = session_user(request)
            users.delete_session(request.cookies.get(COOKIE, ""))
            if user:
                users.audit("logout", user["username"], "", ip_of(request))
            resp.delete_cookie(COOKIE, path="/")
        return resp

    @app.post("/api/auth/password")
    async def auth_password(request: Request, user: dict = Depends(require("read"))):
        need_header(request)
        if not auth_enabled:
            raise HTTPException(400, "Authentication is disabled.")
        limit(f"pw:{user['id']}", L["password"], "password change attempts")
        body = await json_body(request)
        cur, new = body.get("current"), body.get("new")
        if not isinstance(cur, str) or not isinstance(new, str) or not users.authenticate(user["username"], cur):
            users.audit("password_change_failed", user["username"], "", ip_of(request))
            raise HTTPException(400, "Your current password is not correct.")
        if new == cur:
            raise HTTPException(400, "Choose a password that is different from the current one.")
        try:
            users.update_user(user["id"], password=new, must_change=False, keep_session=request.cookies.get(COOKIE))
        except AuthError as e:
            raise HTTPException(e.status, str(e))
        users.audit("password_change", user["username"], "other sessions revoked", ip_of(request))
        return {"ok": True}

    # ------------------------------------------------------------ user admin + audit
    def admin_guard(request: Request, user: dict):
        if not auth_enabled:
            raise HTTPException(400, "Authentication is disabled.")
        limit(f"admin:{user['id']}", L["admin"], "admin requests")

    @app.get("/api/users")
    def list_users(request: Request, user: dict = Depends(require("users"))):
        admin_guard(request, user)
        return {"users": users.list_users(), "roles": list(authmod.ROLES)}

    @app.post("/api/users", status_code=201)
    async def create_user(request: Request, user: dict = Depends(require("users"))):
        need_header(request); admin_guard(request, user)
        b = await json_body(request)
        try:
            new = users.create_user(b.get("username"), b.get("password"), b.get("role", "viewer"), must_change=bool(b.get("must_change", True)))
        except AuthError as e:
            raise HTTPException(e.status, str(e))
        audit(user, "user_create", f"{new['username']} role={new['role']}", request)
        return new

    @app.patch("/api/users/{uid}")
    async def update_user(uid: int, request: Request, user: dict = Depends(require("users"))):
        need_header(request); admin_guard(request, user)
        b = await json_body(request)
        target = users.get_user(uid)
        if not target:
            raise HTTPException(404, "No such user.")
        if uid == user["id"] and (b.get("disabled") or (b.get("role") not in (None, user["role"]))):
            raise HTTPException(409, "You cannot disable or change the role of your own account.")
        try:
            updated = users.update_user(uid, role=b.get("role"), disabled=b.get("disabled"), password=b.get("password"), must_change=b.get("must_change", True if b.get("password") else None))
        except AuthError as e:
            raise HTTPException(e.status, str(e))
        changes = [k for k in ("role", "disabled", "password", "must_change") if k in b]
        audit(user, "user_update", f"{target['username']} {','.join(changes)}" + (f" role={b['role']}" if "role" in b else ""), request)
        return updated

    @app.delete("/api/users/{uid}")
    def remove_user(uid: int, request: Request, user: dict = Depends(require("users"))):
        need_header(request); admin_guard(request, user)
        target = users.get_user(uid)
        if not target:
            raise HTTPException(404, "No such user.")
        if uid == user["id"]:
            raise HTTPException(409, "You cannot delete your own account.")
        try:
            users.delete_user(uid)
        except AuthError as e:
            raise HTTPException(e.status, str(e))
        audit(user, "user_delete", target["username"], request)
        return {"deleted": uid}

    @app.get("/api/audit")
    def audit_log(request: Request, limit_: int = 200, offset: int = 0, action: str | None = None, user: dict = Depends(require("audit"))):
        admin_guard(request, user)
        return {"events": users.audit_list(limit_, offset, action)}

    # ------------------------------------------------------------ config + datasets
    @app.get("/api/config")
    def config(user: dict = Depends(require("read"))):
        return {"demo": demo, "model": "scripted-demo" if demo else (model or MODEL), "python": bool(make_sb), "auth": auth_enabled,
                "max_upload_mb": ingest.MAX_BYTES // 10**6, "formats": [".xlsx", ".csv"], "limits": {"rows": ingest.MAX_ROWS, "columns": ingest.MAX_COLS, "sheets": ingest.MAX_SHEETS}}

    @app.get("/api/datasets")
    def datasets(user: dict = Depends(require("read"))):
        return reg.list(user)

    @app.post("/api/datasets", status_code=201)
    async def upload(request: Request, user: dict = Depends(require("upload"))):
        need_header(request)
        name = unquote(request.headers.get("x-filename", "")).strip()
        if not name:
            raise HTTPException(400, "X-Filename header is required")
        limit(f"upload:{user['id']}", L["upload"], "uploads")
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
            raise HTTPException(429, "Another upload is being processed; try again in a moment.", headers={"Retry-After": "5"})
        try:
            meta = await run_in_threadpool(reg.add, name, b"".join(chunks), user)
        except ingest.IngestError as e:
            raise HTTPException(400, str(e))
        except DatasetError as e:
            raise HTTPException(e.status, str(e))
        finally:
            upload_slots.release()
        audit(user, "dataset_upload", f"{meta['id']} {meta['name'][:60]} {len(chunks) and size} bytes", request)
        return meta

    @app.delete("/api/datasets/{ds_id}")
    def delete_dataset(ds_id: str, request: Request, user: dict = Depends(require("delete_own"))):
        need_header(request)
        try:
            reg.delete(ds_id, user)
        except DatasetError as e:
            raise HTTPException(e.status, str(e))
        audit(user, "dataset_delete", ds_id, request)
        return {"deleted": ds_id}

    @app.get("/api/example.xlsx")
    def example_workbook(user: dict = Depends(require("read"))):
        return Response(example, media_type=XLSX, headers={"Content-Disposition": 'attachment; filename="example_orders.xlsx"'})

    # ------------------------------------------------------------ analysis views (read)
    @app.get("/api/schema")
    def schema(dataset: str = LAB_ID, user: dict = Depends(require("read"))):
        return {"schema": get_db(dataset, user).schema()}

    @app.get("/api/profile")
    def profile_view(dataset: str = LAB_ID, user: dict = Depends(require("read"))):
        get_db(dataset, user)
        return reg.profile(dataset)

    @app.get("/api/dashboard")
    def dash(month: str | None = None, lab: str | None = None, service_line: str | None = None, dataset: str = LAB_ID, user: dict = Depends(require("read"))):
        db = lab_only(dataset, "The operations dashboard", user)
        try:
            return dashboard.build(db, month or None, lab or None, service_line or None)
        except ValueError as e:  # slicer values are checked against the catalog, never interpolated
            raise HTTPException(400, str(e))

    @app.get("/api/dq")
    def dq_summary(dataset: str = LAB_ID, user: dict = Depends(require("read"))):
        return dq.summary(lab_only(dataset, "The lab data-review rules", user))

    @app.get("/api/dq/{rule_id}")
    def dq_issues(rule_id: str, limit: int = 200, offset: int = 0, dataset: str = LAB_ID, user: dict = Depends(require("read"))):
        try:
            return dq.issues(lab_only(dataset, "The lab data-review rules", user), rule_id, limit, offset)
        except KeyError:
            raise HTTPException(404, "unknown rule")

    @app.get("/api/tables")
    def tables(dataset: str = LAB_ID, user: dict = Depends(require("read"))):
        return _tables(get_db(dataset, user))

    @app.get("/api/table/{name}")
    def table(name: str, limit: int = 100, offset: int = 0, sort: str | None = None, desc: bool = False, dataset: str = LAB_ID, user: dict = Depends(require("read"))):
        db = get_db(dataset, user)
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

    # ------------------------------------------------------------ the agent (ask)
    @app.get("/api/ask")
    def ask(request: Request, q: str, dataset: str = LAB_ID, user: dict = Depends(require("ask"))):
        q = q.strip()
        if not q or len(q) > MAX_QUESTION:
            raise HTTPException(400, f"question must be 1-{MAX_QUESTION} characters")
        db = get_db(dataset, user)
        kind = reg.kind(dataset)
        limit(f"ask:{user['id']}", L["ask"], "questions")
        mine = busy_users.setdefault(user["id"], threading.Lock())
        if not mine.acquire(blocking=False):
            raise HTTPException(429, "You already have an analysis running. Wait for it to finish or press Stop.", headers={"Retry-After": "5"})
        if not slots.acquire(blocking=False):
            mine.release()
            raise HTTPException(429, "The server is busy with other analyses; try again shortly.", headers={"Retry-After": "5"})
        audit(user, "ask", f"dataset={dataset} len={len(q)} sha={hashlib.sha256(q.encode()).hexdigest()[:8]}", request)

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
                mine.release()

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/labops.duckdb")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--demo", action="store_true", help="scripted model for the built-in lab data; no API key needed")
    ap.add_argument("--sandbox", choices=["docker", "local", "none"], default="none")
    ap.add_argument("--no-auth", action="store_true", help="disable accounts (single-user local use only; everyone is an admin)")
    ap.add_argument("--auth-db", default=None, help="path of the users/sessions/audit SQLite file (default: next to --db)")
    add_cli_args(ap)
    a = ap.parse_args()
    client, model = (None, None) if a.demo else make_client(a.provider, a.model, a.base_url)
    uvicorn.run(create_app(a.db, a.demo, a.sandbox, client=client, model=model, auth=(False if a.no_auth else None), auth_db=a.auth_db), host="127.0.0.1", port=a.port)


if __name__ == "__main__":
    main()
