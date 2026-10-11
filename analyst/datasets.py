"""Registry of datasets the app can analyse: the built-in lab database plus user uploads (one DuckDB file per upload).

Uploaded data lives under `upload_dir/<12-hex-id>.duckdb` with a `<id>.json` sidecar. File names on disk never come from the user:
ids are random and validated against a strict pattern before use, so a dataset id can never be a path.
"""
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import ingest, profile
from .db import Database

LAB_ID = "lab"
ID_RE = re.compile(r"^[0-9a-f]{12}$")
MAX_DATASETS = 10
MAX_TOTAL_BYTES = 300_000_000


class DatasetError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _display_name(filename: str) -> str:
    stem = Path(filename.replace("\\", "/")).stem
    stem = re.sub(r"[\x00-\x1f\x7f]", "", stem).strip()
    return (stem or "Uploaded data")[:80]


def _examples(tables: list[dict]) -> list[str]:
    ex = ["Give me an overview of this data: key totals, and anything unusual I should know about."]
    first = tables[0]
    ex.append(f"How many rows are in {first['name']}, and which columns have missing values?")
    num = next((t for t in tables for c in t["columns"] if c["type"] in ("BIGINT", "DOUBLE") and not c["name"].endswith("id")), None)
    for t in tables:
        nums = [c["name"] for c in t["columns"] if c["type"] in ("BIGINT", "DOUBLE") and not c["name"].endswith("id")]
        cats = [c["name"] for c in t["columns"] if c["type"] == "VARCHAR"]
        dates = [c["name"] for c in t["columns"] if c["type"] in ("DATE", "TIMESTAMP")]
        if nums and cats:
            ex.append(f"What is the average {nums[0]} by {cats[0]} in {t['name']}? Show a bar chart.")
            break
    for t in tables:
        nums = [c["name"] for c in t["columns"] if c["type"] in ("BIGINT", "DOUBLE") and not c["name"].endswith("id")]
        dates = [c["name"] for c in t["columns"] if c["type"] in ("DATE", "TIMESTAMP")]
        if nums and dates:
            ex.append(f"How does the total {nums[0]} trend by month in {t['name']}? Show a line chart.")
            break
    return ex[:4]


class Registry:
    def __init__(self, lab_path: str, upload_dir: str | Path):
        self.lab_path = str(lab_path)
        self.dir = Path(upload_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._dbs: dict[str, Database] = {}
        self._profiles: dict[str, dict] = {}
        self._lock = threading.RLock()

    # ---- paths / lookup -------------------------------------------------
    def _check(self, ds_id: str) -> None:
        if ds_id != LAB_ID and not ID_RE.match(ds_id or ""):
            raise DatasetError("unknown dataset", 404)

    def path(self, ds_id: str) -> str:
        self._check(ds_id)
        if ds_id == LAB_ID:
            return self.lab_path
        p = self.dir / f"{ds_id}.duckdb"
        if not p.exists():
            raise DatasetError("unknown dataset", 404)
        return str(p)

    def kind(self, ds_id: str) -> str:
        self.path(ds_id)
        return "lab" if ds_id == LAB_ID else "custom"

    def db(self, ds_id: str) -> Database:
        path = self.path(ds_id)
        with self._lock:
            if ds_id not in self._dbs:
                self._dbs[ds_id] = Database(path)
            return self._dbs[ds_id]

    def profile(self, ds_id: str) -> dict:
        db = self.db(ds_id)
        if ds_id not in self._profiles:          # uploads are immutable, so the profile can be cached
            self._profiles[ds_id] = profile.profile(db)
        return self._profiles[ds_id]

    # ---- listing --------------------------------------------------------
    def _tables_of(self, db: Database) -> list[dict]:
        out = []
        for (t,) in db.con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main' ORDER BY 1").fetchall():
            cols = db.con.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name=? ORDER BY ordinal_position", [t]).fetchall()
            out.append(dict(name=t, rows=db.con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0], columns=[dict(name=c, type=d) for c, d in cols]))
        return out

    def _meta(self, ds_id: str) -> dict:
        if ds_id == LAB_ID:
            t = self._tables_of(self.db(LAB_ID))
            return dict(id=LAB_ID, name="Lab operations (demo data)", kind="lab", filename=None, created=None,
                        tables=[dict(name=x["name"], rows=x["rows"], columns=len(x["columns"])) for x in t], bytes=Path(self.lab_path).stat().st_size, warnings=[], examples=[])
        return json.loads((self.dir / f"{ds_id}.json").read_text())

    # ---- ownership / access (uploads are private to their owner; admins can see everything) ----
    def meta(self, ds_id: str) -> dict:
        self.path(ds_id)
        return self._meta(ds_id)

    def can_access(self, ds_id: str, user: dict | None) -> bool:
        self.path(ds_id)
        if ds_id == LAB_ID or user is None:
            return True
        if user.get("role") == "admin":
            return True
        return self._meta(ds_id).get("owner_id") == user.get("id")

    def require_access(self, ds_id: str, user: dict | None) -> None:
        if not self.can_access(ds_id, user):
            raise DatasetError("unknown dataset", 404)          # 404 not 403: do not reveal that someone else's dataset exists

    def _upload_metas(self) -> list[dict]:
        uploads = []
        for p in self.dir.glob("*.json"):
            if ID_RE.match(p.stem) and (self.dir / f"{p.stem}.duckdb").exists():
                try:
                    uploads.append(json.loads(p.read_text()))
                except (OSError, ValueError):
                    continue
        return uploads

    def list(self, user: dict | None = None) -> list[dict]:
        uploads = [m for m in self._upload_metas() if user is None or user.get("role") == "admin" or m.get("owner_id") == user.get("id")]
        return [self._meta(LAB_ID)] + sorted(uploads, key=lambda m: m["created"])

    # ---- mutation -------------------------------------------------------
    def add(self, filename: str, data: bytes, owner: dict | None = None) -> dict:
        with self._lock:
            existing = [p for p in self.dir.glob("*.duckdb")]
            mine = [m for m in self._upload_metas() if owner is None or m.get("owner_id") == owner.get("id")]
            if len(mine) >= MAX_DATASETS:
                raise DatasetError(f"You already have {MAX_DATASETS} uploaded datasets. Delete one first.", 409)
            if sum(p.stat().st_size for p in existing) + len(data) > MAX_TOTAL_BYTES:
                raise DatasetError("The upload storage limit has been reached. Delete an uploaded dataset first.", 409)
            ds_id = uuid.uuid4().hex[:12]
        tmp = self.dir / f"{ds_id}.building"
        try:
            res = ingest.ingest(filename, data, tmp)
        except Exception:
            tmp.unlink(missing_ok=True)
            Path(str(tmp) + ".wal").unlink(missing_ok=True)
            raise
        final = self.dir / f"{ds_id}.duckdb"
        tmp.replace(final)
        tables = [dict(name=t.name, sheet=t.sheet, rows=t.rows, columns=len(t.columns)) for t in res.tables]
        meta = dict(id=ds_id, name=_display_name(filename), kind="custom", filename=Path(filename.replace("\\", "/")).name[:120],
                    owner_id=None if owner is None else owner.get("id"), owner_name=None if owner is None else owner.get("username"),
                    created=datetime.now(timezone.utc).isoformat(timespec="seconds"), tables=tables, bytes=final.stat().st_size, warnings=res.warnings[:20],
                    examples=_examples([dict(name=t.name, columns=t.columns) for t in res.tables]))
        (self.dir / f"{ds_id}.json").write_text(json.dumps(meta))
        return meta

    def delete(self, ds_id: str, user: dict | None = None) -> None:
        if ds_id == LAB_ID:
            raise DatasetError("The built-in dataset cannot be deleted.", 400)
        self.path(ds_id)
        if user is not None:
            perms = set(user.get("permissions", ()))
            owner = self._meta(ds_id).get("owner_id")
            if "delete_any" not in perms:
                if owner != user.get("id"):
                    raise DatasetError("unknown dataset", 404)
                if "delete_own" not in perms:
                    raise DatasetError("Your role cannot delete datasets.", 403)
        with self._lock:
            db = self._dbs.pop(ds_id, None)
            self._profiles.pop(ds_id, None)
            if db:
                db.con.close()
            for suffix in (".duckdb", ".duckdb.wal", ".json"):
                (self.dir / f"{ds_id}{suffix}").unlink(missing_ok=True)
