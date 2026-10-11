import sqlite3

import pytest

from analyst import auth
from analyst.auth import AuthError, AuthStore, check_password, hash_password, normalize_username, verify_password
from analyst.ratelimit import RateLimiter


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    return AuthStore(tmp_path / "auth.db", clock=clock, idle=3600, absolute=86400)


GOOD = "Tr1cky-passphrase!"


# ---------------- passwords
def test_hash_is_salted_verifiable_and_not_the_password():
    a, b = hash_password(GOOD), hash_password(GOOD)
    assert a != b and GOOD not in a and a.startswith("scrypt$")
    assert verify_password(GOOD, a) and verify_password(GOOD, b)
    assert not verify_password(GOOD + "x", a) and not verify_password("", a)


@pytest.mark.parametrize("stored", ["", "garbage", "scrypt$x$y$z$a$b", "md5$1$2$3$4$5", "scrypt$16$8$1$%%%$%%%"])
def test_verify_never_raises_on_malformed_hashes(stored):
    assert verify_password(GOOD, stored) is False


@pytest.mark.parametrize("pw,msg", [
    ("short1A!", "at least 10"), ("x" * 129, "at most 128"), ("password123", "too easy"), ("aaaaaaaaaaaa", "too easy"),
    ("alicealice99Z", "must not contain the username"), ("alllowercaseletters", "two kinds"), (123456, "at least 10"), (None, "at least 10"),
])
def test_password_policy(pw, msg):
    with pytest.raises(AuthError, match=msg):
        check_password(pw, "alice")


def test_good_passwords_pass_policy():
    check_password(GOOD, "alice"); check_password("correct horse battery 7", "bob")


@pytest.mark.parametrize("name", ["", "ab", "A", "x" * 33, "has space", "semi;colon", "-lead", "😀emoji", None, 5, "a/b"])
def test_username_validation(name):
    with pytest.raises(AuthError):
        normalize_username(name)


def test_username_is_normalised():
    assert normalize_username("  Alice.Smith ") == "alice.smith"


# ---------------- users
def test_create_authenticate_and_duplicate(store):
    u = store.create_user("Alice", GOOD, "analyst")
    assert u["username"] == "alice" and u["role"] == "analyst" and "password_hash" not in u and "read" in u["permissions"]
    assert store.authenticate("ALICE", GOOD)["id"] == u["id"]
    assert store.authenticate("alice", "wrong") is None and store.authenticate("nobody", GOOD) is None and store.authenticate("a'; DROP TABLE users;--", GOOD) is None
    with pytest.raises(AuthError, match="already taken") as e:
        store.create_user("alice", GOOD)
    assert e.value.status == 409


def test_disabled_users_cannot_log_in(store):
    u = store.create_user("alice", GOOD)
    store.update_user(u["id"], disabled=True)
    assert store.authenticate("alice", GOOD) is None
    store.update_user(u["id"], disabled=False)
    assert store.authenticate("alice", GOOD)


def test_last_admin_cannot_be_demoted_disabled_or_deleted(store):
    a = store.create_user("root-admin", GOOD, "admin")
    for kw in ({"role": "viewer"}, {"disabled": True}):
        with pytest.raises(AuthError, match="last active admin"):
            store.update_user(a["id"], **kw)
    with pytest.raises(AuthError, match="last active admin"):
        store.delete_user(a["id"])
    b = store.create_user("second-admin", GOOD, "admin")
    store.update_user(a["id"], role="viewer")                      # fine with another admin around
    with pytest.raises(AuthError):
        store.update_user(b["id"], disabled=True)


def test_password_is_stored_only_as_a_hash(store, tmp_path):
    store.create_user("alice", GOOD)
    raw = sqlite3.connect(str(tmp_path / "auth.db")).execute("SELECT password_hash FROM users").fetchone()[0]
    assert GOOD not in raw and raw.startswith("scrypt$")


def test_database_file_is_owner_only(tmp_path):
    import os, stat
    AuthStore(tmp_path / "x.db")
    assert stat.S_IMODE(os.stat(tmp_path / "x.db").st_mode) == 0o600


# ---------------- sessions
def test_session_roundtrip_and_logout(store):
    u = store.create_user("alice", GOOD)
    tok = store.create_session(u["id"], "1.2.3.4", "UA")
    assert len(tok) >= 40 and store.session_user(tok)["username"] == "alice"
    store.delete_session(tok)
    assert store.session_user(tok) is None


def test_only_a_hash_of_the_token_is_stored(store, tmp_path):
    tok = store.create_session(store.create_user("alice", GOOD)["id"])
    rows = sqlite3.connect(str(tmp_path / "auth.db")).execute("SELECT token_hash FROM sessions").fetchall()
    assert tok not in rows[0][0] and len(rows[0][0]) == 64


def test_idle_and_absolute_expiry(store, clock):
    uid = store.create_user("alice", GOOD)["id"]
    t1 = store.create_session(uid)
    clock.t += 3601
    assert store.session_user(t1) is None                          # idle timeout (3600)
    t2 = store.create_session(uid)
    for _ in range(30):                                            # stay active for 30 h, always within the idle window
        clock.t += 3000
        if store.session_user(t2) is None:
            break
    assert store.session_user(t2) is None                          # but the absolute lifetime (24 h) still ends it


@pytest.mark.parametrize("tok", ["", None, "x" * 500, "../../etc/passwd", "'; DROP TABLE sessions;--"])
def test_garbage_tokens_are_rejected(store, tok):
    assert store.session_user(tok) is None


def test_password_change_and_disable_revoke_sessions(store):
    uid = store.create_user("alice", GOOD, "analyst")["id"]
    keep, other = store.create_session(uid), store.create_session(uid)
    store.update_user(uid, password="An0ther-good-pass!", keep_session=keep)
    assert store.session_user(keep) and store.session_user(other) is None
    store.update_user(uid, disabled=True)
    assert store.session_user(keep) is None
    store.update_user(uid, disabled=False)
    tok = store.create_session(uid)
    store.update_user(uid, role="viewer")                           # role change forces re-login
    assert store.session_user(tok) is None


def test_deleting_a_user_deletes_their_sessions(store):
    store.create_user("keeper-admin", GOOD, "admin")
    uid = store.create_user("alice", GOOD)["id"]
    tok = store.create_session(uid)
    store.delete_user(uid)
    assert store.session_user(tok) is None and store.session_count() == 0


# ---------------- RBAC table
def test_permission_matrix():
    P = auth.PERMS
    assert P["viewer"] == {"read"}
    assert {"ask", "upload", "delete_own"} <= P["analyst"] and not ({"users", "audit", "delete_any"} & P["analyst"])
    assert {"users", "audit", "delete_any"} <= P["admin"] and P["viewer"] < P["analyst"] < P["admin"]


# ---------------- audit
def test_audit_log_orders_filters_and_prunes(store, monkeypatch):
    for i in range(5):
        store.audit("login_success" if i % 2 else "ask", "alice", f"n={i}", "1.1.1.1")
    rows = store.audit_list(10)
    assert [r["detail"] for r in rows] == [f"n={i}" for i in (4, 3, 2, 1, 0)]
    assert {r["action"] for r in store.audit_list(10, action="ask")} == {"ask"}
    monkeypatch.setattr(auth, "AUDIT_KEEP", 3)
    store.audit("x", "alice", "last")
    assert len(store.audit_list(100)) <= 4 and store.audit_list(1)[0]["detail"] == "last"
    assert len(store.audit_list(10**6)) <= 500


# ---------------- rate limiter
def test_rate_limiter_window_and_retry_after():
    t = [0.0]
    rl = RateLimiter(clock=lambda: t[0])
    assert [rl.check("k", 3, 10)[0] for _ in range(3)] == [True, True, True]
    ok, wait = rl.check("k", 3, 10)
    assert not ok and 9.9 < wait <= 10
    t[0] = 5.0
    assert not rl.check("k", 3, 10)[0]
    t[0] = 10.1
    assert rl.check("k", 3, 10)[0]                                  # the first hit aged out
    assert rl.check("other", 3, 10)[0]                              # keys are independent


def test_rate_limiter_hit_count_reset_and_retry():
    t = [0.0]
    rl = RateLimiter(clock=lambda: t[0])
    for _ in range(5):
        rl.hit("f", 100)
        t[0] += 1
    assert rl.count("f", 100) == 5 and rl.count("f", 2.5) == 2
    assert rl.retry_after("f", 5, 100) > 90 and rl.retry_after("f", 6, 100) == 0
    rl.reset("f")
    assert rl.count("f", 100) == 0
