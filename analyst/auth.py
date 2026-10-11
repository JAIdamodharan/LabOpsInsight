"""Users, password hashing, server-side sessions, roles and the audit log (SQLite, standard library only).

Design notes
- Passwords: scrypt with a per-user random salt; verified in constant time. Unknown usernames are checked against a dummy
  hash so response time does not reveal whether an account exists.
- Sessions: a random 256-bit token is given to the browser; only its SHA-256 is stored, so a leaked database cannot be
  replayed as cookies. Sessions expire after an idle timeout and an absolute lifetime, and are revoked on password change,
  disable and delete.
- Roles (RBAC): viewer < analyst < admin; see PERMS. The server enforces them; the UI only mirrors them.
- Audit log: who did what and when. It never stores passwords or question text (only a length and a short hash).
CLI:  python -m analyst.auth create-user NAME --role admin   |  list-users  |  set-password NAME  |  disable NAME  |  enable NAME
"""
import argparse
import base64
import getpass
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from pathlib import Path

ROLES = ("viewer", "analyst", "admin")
PERMS = {
    "viewer": frozenset({"read"}),
    "analyst": frozenset({"read", "ask", "upload", "delete_own"}),
    "admin": frozenset({"read", "ask", "upload", "delete_own", "delete_any", "users", "audit"}),
}
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1
MIN_PASSWORD, MAX_PASSWORD = 10, 128
IDLE_TIMEOUT = 12 * 3600
ABSOLUTE_TIMEOUT = 7 * 86400
AUDIT_KEEP = 5000
COMMON = {"password", "password1", "password123", "1234567890", "qwertyuiop", "letmein123", "welcome123", "admin12345", "iloveyou123", "labops1234"}


class AuthError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- passwords
def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, maxmem=128 * 1024 * 1024, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(dk)}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, want = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), maxmem=128 * 1024 * 1024, dklen=32)
        return hmac.compare_digest(dk, base64.b64decode(want))
    except (ValueError, TypeError):
        return False


def check_password(pw: str, username: str = "") -> None:
    """Raise AuthError (400) with a user-fixable message if the password is not acceptable."""
    if not isinstance(pw, str) or len(pw) < MIN_PASSWORD:
        raise AuthError(f"Use at least {MIN_PASSWORD} characters.")
    if len(pw) > MAX_PASSWORD:
        raise AuthError(f"Use at most {MAX_PASSWORD} characters.")
    low = pw.lower()
    if low in COMMON or len(set(pw)) < 5:
        raise AuthError("That password is too easy to guess. Mix words, numbers and symbols.")
    if username and username.lower() in low:
        raise AuthError("The password must not contain the username.")
    if sum(bool(re.search(p, pw)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]")) < 2:
        raise AuthError("Use at least two kinds of characters (lower case, upper case, digits, symbols).")


def normalize_username(u) -> str:
    u = (u or "").strip().lower() if isinstance(u, str) else ""
    if not USERNAME_RE.match(u):
        raise AuthError("Usernames are 3-32 characters: letters, digits, dot, dash or underscore.")
    return u


# -------------------------------------------------------------------- store
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('viewer','analyst','admin')), disabled INTEGER NOT NULL DEFAULT 0,
  must_change INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, last_login REAL);
CREATE TABLE IF NOT EXISTS sessions(
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created REAL NOT NULL, last_seen REAL NOT NULL, expires REAL NOT NULL, ip TEXT, ua TEXT);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, username TEXT, action TEXT NOT NULL, detail TEXT, ip TEXT);
"""


def public_user(row) -> dict:
    return dict(id=row["id"], username=row["username"], role=row["role"], disabled=bool(row["disabled"]),
                must_change=bool(row["must_change"]), created=row["created"], last_login=row["last_login"],
                permissions=sorted(PERMS[row["role"]]))


class AuthStore:
    def __init__(self, path: str | Path, clock=time.time, idle=IDLE_TIMEOUT, absolute=ABSOLUTE_TIMEOUT):
        self.path, self.clock, self.idle, self.absolute = Path(path), clock, idle, absolute
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.executescript(SCHEMA)
        if new:
            try:
                os.chmod(self.path, 0o600)   # credentials and sessions: owner-only
            except OSError:
                pass
        self._dummy = hash_password(secrets.token_hex(8))      # for constant-ish timing on unknown users

    def _q(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args)

    # ---- users
    def count_users(self) -> int:
        return self._q("SELECT count(*) FROM users").fetchone()[0]

    def create_user(self, username, password, role="viewer", must_change=False) -> dict:
        username = normalize_username(username)
        if role not in ROLES:
            raise AuthError("Unknown role.")
        check_password(password, username)
        h = hash_password(password)
        with self.lock:
            if self._q("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                raise AuthError("That username is already taken.", 409)
            cur = self._q("INSERT INTO users(username,password_hash,role,must_change,created) VALUES(?,?,?,?,?)", (username, h, role, int(must_change), self.clock()))
            return self.get_user(cur.lastrowid)

    def get_user(self, uid: int) -> dict | None:
        row = self._q("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return public_user(row) if row else None

    def list_users(self) -> list[dict]:
        return [public_user(r) for r in self._q("SELECT * FROM users ORDER BY username").fetchall()]

    def authenticate(self, username, password) -> dict | None:
        """Return the user for a correct username+password, else None. Always spends one scrypt computation."""
        try:
            username = normalize_username(username)
        except AuthError:
            verify_password(str(password), self._dummy)
            return None
        row = self._q("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        ok = verify_password(str(password), row["password_hash"] if row else self._dummy)
        if not row or not ok or row["disabled"]:
            return None
        self._q("UPDATE users SET last_login=? WHERE id=?", (self.clock(), row["id"]))
        return public_user(self._q("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone())

    def _admins(self, excluding: int | None = None) -> int:
        return self._q("SELECT count(*) FROM users WHERE role='admin' AND disabled=0 AND id IS NOT ?", (excluding,)).fetchone()[0]

    def update_user(self, uid, role=None, disabled=None, password=None, must_change=None, keep_session=None) -> dict:
        u = self.get_user(uid)
        if not u:
            raise AuthError("No such user.", 404)
        if role is not None and role not in ROLES:
            raise AuthError("Unknown role.")
        losing_admin = u["role"] == "admin" and not u["disabled"] and ((role is not None and role != "admin") or disabled)
        if losing_admin and self._admins(excluding=uid) == 0:
            raise AuthError("You cannot remove or disable the last active admin.", 409)
        with self.lock:
            if role is not None:
                self._q("UPDATE users SET role=? WHERE id=?", (role, uid))
            if disabled is not None:
                self._q("UPDATE users SET disabled=? WHERE id=?", (int(bool(disabled)), uid))
            if password is not None:
                check_password(password, u["username"])
                self._q("UPDATE users SET password_hash=? WHERE id=?", (hash_password(password), uid))
            if must_change is not None:
                self._q("UPDATE users SET must_change=? WHERE id=?", (int(bool(must_change)), uid))
            if disabled or password is not None or (role is not None and role != u["role"]):
                self.delete_user_sessions(uid, keep=keep_session)       # force re-authentication with the new rights
        return self.get_user(uid)

    def delete_user(self, uid) -> None:
        u = self.get_user(uid)
        if not u:
            raise AuthError("No such user.", 404)
        if u["role"] == "admin" and not u["disabled"] and self._admins(excluding=uid) == 0:
            raise AuthError("You cannot delete the last active admin.", 409)
        self._q("DELETE FROM users WHERE id=?", (uid,))

    # ---- sessions
    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def create_session(self, uid: int, ip: str = "", ua: str = "") -> str:
        token = secrets.token_urlsafe(32)
        now = self.clock()
        self._q("INSERT INTO sessions VALUES(?,?,?,?,?,?,?)", (self._hash(token), uid, now, now, now + self.absolute, ip[:64], ua[:200]))
        self._q("DELETE FROM sessions WHERE expires < ? OR last_seen < ?", (now, now - self.idle))
        return token

    def session_user(self, token: str) -> dict | None:
        if not token or len(token) > 200:
            return None
        now, h = self.clock(), self._hash(token)
        row = self._q("SELECT * FROM sessions WHERE token_hash=?", (h,)).fetchone()
        if not row:
            return None
        if row["expires"] < now or row["last_seen"] < now - self.idle:
            self._q("DELETE FROM sessions WHERE token_hash=?", (h,))
            return None
        user = self._q("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
        if not user or user["disabled"]:
            self._q("DELETE FROM sessions WHERE token_hash=?", (h,))
            return None
        if now - row["last_seen"] > 60:
            self._q("UPDATE sessions SET last_seen=? WHERE token_hash=?", (now, h))
        return public_user(user)

    def delete_session(self, token: str) -> None:
        if token:
            self._q("DELETE FROM sessions WHERE token_hash=?", (self._hash(token),))

    def delete_user_sessions(self, uid: int, keep: str | None = None) -> None:
        if keep:
            self._q("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (uid, self._hash(keep)))
        else:
            self._q("DELETE FROM sessions WHERE user_id=?", (uid,))

    def session_count(self, uid: int | None = None) -> int:
        if uid is None:
            return self._q("SELECT count(*) FROM sessions").fetchone()[0]
        return self._q("SELECT count(*) FROM sessions WHERE user_id=?", (uid,)).fetchone()[0]

    # ---- audit
    def audit(self, action: str, username: str | None = None, detail: str = "", ip: str = "") -> None:
        with self.lock:
            self._q("INSERT INTO audit(ts,username,action,detail,ip) VALUES(?,?,?,?,?)", (self.clock(), username, action[:40], detail[:300], ip[:64]))
            self._q("DELETE FROM audit WHERE id <= (SELECT max(id) FROM audit) - ?", (AUDIT_KEEP,))

    def audit_list(self, limit: int = 200, offset: int = 0, action: str | None = None) -> list[dict]:
        limit, offset = max(1, min(int(limit), 500)), max(0, int(offset))
        where, args = ("WHERE action=?", [action]) if action else ("", [])
        rows = self._q(f"SELECT * FROM audit {where} ORDER BY id DESC LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
        return [dict(id=r["id"], ts=r["ts"], username=r["username"], action=r["action"], detail=r["detail"], ip=r["ip"]) for r in rows]


# ---------------------------------------------------------------------- CLI
def _prompt_password() -> str:
    a, b = getpass.getpass("Password: "), getpass.getpass("Repeat password: ")
    if a != b:
        sys.exit("Passwords do not match.")
    return a


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m analyst.auth", description="Manage LabOps Insight users (the password is always prompted, never a flag).")
    ap.add_argument("--auth-db", default="data/auth.db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-user"); c.add_argument("username"); c.add_argument("--role", choices=ROLES, default="viewer")
    sub.add_parser("list-users")
    for name in ("set-password", "disable", "enable"):
        sub.add_parser(name).add_argument("username")
    a = ap.parse_args(argv)
    store = AuthStore(a.auth_db)
    try:
        if a.cmd == "create-user":
            u = store.create_user(a.username, _prompt_password(), a.role)
            store.audit("user_create", "cli", f"{u['username']} role={u['role']}")
            print(f"created {u['username']} ({u['role']})")
        elif a.cmd == "list-users":
            for u in store.list_users():
                print(f"{u['username']:<20} {u['role']:<8} {'disabled' if u['disabled'] else 'active'}")
        else:
            row = next((u for u in store.list_users() if u["username"] == a.username.strip().lower()), None)
            if not row:
                sys.exit("No such user.")
            if a.cmd == "set-password":
                store.update_user(row["id"], password=_prompt_password(), must_change=False)
                store.audit("password_reset", "cli", row["username"])
            else:
                store.update_user(row["id"], disabled=(a.cmd == "disable"))
                store.audit("user_update", "cli", f"{row['username']} {a.cmd}")
            print("done")
    except AuthError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
