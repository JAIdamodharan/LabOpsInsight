import io
import sqlite3
import threading
import time
from types import SimpleNamespace as NS
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from analyst import auth as authmod
from analyst import datasets
from analyst.auth import AuthStore
from analyst.server import create_app
from tests.test_analyst import db, msg, text  # noqa: F401
from tests.test_ingest import xlsx
from tests.test_server import events, path  # noqa: F401

H = {"X-Requested-With": "labops"}
GOOD = "Tr1cky-passphrase!"
SALES = xlsx({"Orders": [["id", "units"]] + [[i, i % 5] for i in range(1, 21)]})
ROLE_PW = {"viewer": "Viewer-pass-phrase-1", "analyst": "Analyst-pass-phrase-1", "admin": GOOD}
LOOSE = dict(login_ip=(10_000, 60), api_ip=(10**6, 60), api_user=(10**6, 60), upload=(10**6, 60), ask=(10**6, 60), password=(10**6, 60), admin=(10**6, 60))


def mk(path, tmp_path, limits=None, **kw):
    return create_app(path, demo=True, artifacts_dir=str(tmp_path / "a"), upload_dir=str(tmp_path / "up"), auth=True,
                      auth_db=str(tmp_path / "auth.db"), limits={**LOOSE, **(limits or {})}, **kw)


def setup_admin(c, name="admin", pw=GOOD):
    r = c.post("/api/auth/setup", json={"username": name, "password": pw}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()["user"]


def add_user(admin, name, role, must_change=False, pw=None):
    r = admin.post("/api/users", json={"username": name, "password": pw or ROLE_PW[role], "role": role, "must_change": must_change}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


def sign_in(app, name, pw):
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": name, "password": pw}, headers=H)
    assert r.status_code == 200, r.text
    return c


@pytest.fixture
def world(path, tmp_path):
    """An app with one admin, one analyst, one viewer and a second analyst, each with their own signed-in client."""
    app = mk(path, tmp_path)
    admin = TestClient(app)
    setup_admin(admin)
    add_user(admin, "ann", "analyst"); add_user(admin, "ben", "analyst"); add_user(admin, "vic", "viewer")
    return NS(app=app, admin=admin, ann=sign_in(app, "ann", ROLE_PW["analyst"]), ben=sign_in(app, "ben", ROLE_PW["analyst"]),
              vic=sign_in(app, "vic", ROLE_PW["viewer"]), tmp=tmp_path)


def put(c, name="orders.xlsx", data=SALES):
    return c.post("/api/datasets", content=data, headers={"X-Filename": quote(name), **H})


# ======================================================== unauthenticated access
PROTECTED_GET = ["/api/config", "/api/datasets", "/api/tables", "/api/profile", "/api/dashboard", "/api/dq", "/api/dq/DQ-001", "/api/schema",
                 "/api/table/tests", "/api/example.xlsx", "/api/ask?q=hello", "/api/users", "/api/audit", "/artifacts/abcdef12_chart.png",
                 "/api/story", "/api/story/page", "/api/predict", "/api/predict/score?method=a&instrument=b&priority=c"]


@pytest.mark.parametrize("url", PROTECTED_GET)
def test_every_data_route_requires_a_session(path, tmp_path, url):
    c = TestClient(mk(path, tmp_path))
    setup_admin(TestClient(c.app))                                         # accounts exist, but this client is not signed in
    assert c.get(url).status_code == 401


@pytest.mark.parametrize("method,url", [("POST", "/api/datasets"), ("DELETE", "/api/datasets/abcdef123456"), ("POST", "/api/users"),
                                        ("PATCH", "/api/users/1"), ("DELETE", "/api/users/1"), ("POST", "/api/auth/password")])
def test_mutating_routes_require_a_session(path, tmp_path, method, url):
    c = TestClient(mk(path, tmp_path))
    r = c.request(method, url, headers={**H, "X-Filename": "a.xlsx"}, content=b"{}")
    assert r.status_code == 401


def test_public_surface_is_only_the_login_page_assets_and_status(path, tmp_path):
    c = TestClient(mk(path, tmp_path))
    assert c.get("/").status_code == 200 and c.get("/static/app.js").status_code == 200 and c.get("/api/auth/status").status_code == 200
    assert "tests" not in c.get("/").text and "HPLC" not in c.get("/").text          # no data leaks into the page shell


def test_openapi_docs_are_not_exposed(path, tmp_path):
    c = TestClient(mk(path, tmp_path))
    assert all(c.get(u).status_code == 404 for u in ("/docs", "/redoc", "/openapi.json"))


# ======================================================== first-run setup, login, logout
def test_setup_flow_and_cookie_flags(path, tmp_path):
    c = TestClient(mk(path, tmp_path))
    s = c.get("/api/auth/status").json()
    assert s["enabled"] and s["setup_required"] and s["user"] is None
    r = c.post("/api/auth/setup", json={"username": "Admin", "password": GOOD}, headers=H)
    assert r.status_code == 200 and r.json()["user"]["role"] == "admin" and r.json()["user"]["username"] == "admin"
    cookie = r.headers["set-cookie"].lower()
    assert "labops_session=" in cookie and "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie and "secure" not in cookie
    assert c.get("/api/auth/status").json() | {} and not c.get("/api/auth/status").json()["setup_required"]
    assert c.get("/api/config").status_code == 200
    again = TestClient(c.app).post("/api/auth/setup", json={"username": "evil", "password": GOOD}, headers=H)
    assert again.status_code == 409


def test_cookie_is_secure_and_hsts_is_sent_over_https(path, tmp_path):
    c = TestClient(mk(path, tmp_path), base_url="https://testserver")
    r = c.post("/api/auth/setup", json={"username": "admin", "password": GOOD}, headers=H)
    assert "secure" in r.headers["set-cookie"].lower() and "max-age=31536000" in r.headers["strict-transport-security"]


def test_setup_rejects_weak_passwords_and_bad_names_and_needs_the_header(path, tmp_path):
    c = TestClient(mk(path, tmp_path))
    assert c.post("/api/auth/setup", json={"username": "admin", "password": GOOD}).status_code == 403          # no header
    assert c.post("/api/auth/setup", json={"username": "admin", "password": "short"}, headers=H).status_code == 400
    assert c.post("/api/auth/setup", json={"username": "a b", "password": GOOD}, headers=H).status_code == 400
    assert c.get("/api/auth/status").json()["setup_required"]                                                  # nothing was created


def test_two_simultaneous_setups_create_exactly_one_admin(path, tmp_path):
    app = mk(path, tmp_path)
    out = []

    def go(n):
        out.append(TestClient(app).post("/api/auth/setup", json={"username": f"admin{n}", "password": GOOD}, headers=H).status_code)
    ts = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(out) == [200] + [409] * 5
    assert len(AuthStore(tmp_path / "auth.db").list_users()) == 1


def test_login_logout_and_session_invalidation(path, tmp_path):
    app = mk(path, tmp_path)
    setup_admin(TestClient(app))
    c = sign_in(app, "ADMIN", GOOD)
    token = c.cookies["labops_session"]
    assert c.get("/api/auth/status").json()["user"]["username"] == "admin"
    assert c.post("/api/auth/logout", headers=H).status_code == 200
    assert c.get("/api/datasets").status_code == 401
    stolen = TestClient(app)
    stolen.cookies.set("labops_session", token)
    assert stolen.get("/api/datasets").status_code == 401                         # the old token is dead server-side, not just deleted client-side


def test_logging_in_again_revokes_the_previous_session_token(path, tmp_path):
    app = mk(path, tmp_path)
    setup_admin(TestClient(app))
    c = sign_in(app, "admin", GOOD)
    old = c.cookies["labops_session"]
    assert c.post("/api/auth/login", json={"username": "admin", "password": GOOD}, headers=H).status_code == 200
    assert c.cookies["labops_session"] != old
    replay = TestClient(app)
    replay.cookies.set("labops_session", old)
    assert replay.get("/api/datasets").status_code == 401 and c.get("/api/datasets").status_code == 200


def test_login_errors_do_not_reveal_which_part_was_wrong(path, tmp_path):
    app = mk(path, tmp_path)
    setup_admin(TestClient(app))
    c = TestClient(app)
    bad_pw = c.post("/api/auth/login", json={"username": "admin", "password": "nope-nope-nope"}, headers=H)
    no_user = c.post("/api/auth/login", json={"username": "ghost", "password": "nope-nope-nope"}, headers=H)
    assert bad_pw.status_code == no_user.status_code == 401 and bad_pw.json() == no_user.json()
    assert "set-cookie" not in bad_pw.headers


@pytest.mark.parametrize("body", [{"username": 5, "password": GOOD}, {"username": "admin", "password": ["x"]}, {"username": None, "password": None},
                                  {"username": "admin", "password": "x" * 5000}, {}, {"username": "a'; DROP TABLE users;--", "password": GOOD}])
def test_malformed_logins_are_401_never_500(path, tmp_path, body):
    app = mk(path, tmp_path)
    setup_admin(TestClient(app))
    assert TestClient(app).post("/api/auth/login", json=body, headers=H).status_code in (401, 413)


def test_login_body_limits_and_invalid_json(path, tmp_path):
    app = mk(path, tmp_path)
    setup_admin(TestClient(app))
    c = TestClient(app)
    assert c.post("/api/auth/login", content=b"{" + b" " * 6000 + b"}", headers=H).status_code == 413
    assert c.post("/api/auth/login", content=b"not json", headers=H).status_code == 400
    assert c.post("/api/auth/login", content=b"[1,2]", headers=H).status_code == 400


def test_login_is_unreachable_without_the_header_and_from_other_sites(path, tmp_path):
    app = mk(path, tmp_path)
    setup_admin(TestClient(app))
    c = TestClient(app)
    ok = {"username": "admin", "password": GOOD}
    assert c.post("/api/auth/login", json=ok).status_code == 403                                                # login CSRF
    assert c.post("/api/auth/login", json=ok, headers={**H, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert c.post("/api/auth/login", json=ok, headers={**H, "Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_authenticate_always_spends_a_password_hash(monkeypatch, tmp_path):
    store = AuthStore(tmp_path / "t.db")
    store.create_user("alice", GOOD)
    calls = []
    real = authmod.verify_password
    monkeypatch.setattr(authmod, "verify_password", lambda *a: calls.append(1) or real(*a))
    for who in ("alice", "nobody", "bad name!", ""):
        store.authenticate(who, "whatever-password")
    assert len(calls) == 4                                                    # same work for existing, unknown and invalid usernames


# ======================================================== lockout + rate limits
def test_login_lockout_blocks_even_the_correct_password_then_recovers(path, tmp_path):
    app = mk(path, tmp_path, limits={"login_fail_ip_user": (3, 1)})
    setup_admin(TestClient(app))
    c = TestClient(app)
    for _ in range(3):
        assert c.post("/api/auth/login", json={"username": "admin", "password": "wrong-wrong-wrong"}, headers=H).status_code == 401
    locked = c.post("/api/auth/login", json={"username": "admin", "password": GOOD}, headers=H)
    assert locked.status_code == 429 and int(locked.headers["retry-after"]) >= 1 and "failed sign-in" in locked.json()["detail"]
    assert c.post("/api/auth/login", json={"username": "someone-else", "password": "x" * 12}, headers=H).status_code == 401      # other names unaffected
    time.sleep(1.1)
    assert c.post("/api/auth/login", json={"username": "admin", "password": GOOD}, headers=H).status_code == 200


def test_global_per_username_lockout_defends_against_many_ips_but_other_users_stay_usable(path, tmp_path):
    app = mk(path, tmp_path, limits={"login_fail_user": (4, 60), "login_fail_ip_user": (100, 60)})
    setup_admin(TestClient(app))
    c = TestClient(app)
    for _ in range(4):
        c.post("/api/auth/login", json={"username": "admin", "password": "wrong-wrong-wrong"}, headers=H)
    assert c.post("/api/auth/login", json={"username": "admin", "password": GOOD}, headers=H).status_code == 429


def test_failed_attempts_reset_after_a_successful_login(path, tmp_path):
    app = mk(path, tmp_path, limits={"login_fail_ip_user": (3, 60)})
    setup_admin(TestClient(app))
    c = TestClient(app)
    for _ in range(2):
        c.post("/api/auth/login", json={"username": "admin", "password": "wrong-wrong-wrong"}, headers=H)
    assert c.post("/api/auth/login", json={"username": "admin", "password": GOOD}, headers=H).status_code == 200
    for _ in range(2):
        assert c.post("/api/auth/login", json={"username": "admin", "password": "wrong-wrong-wrong"}, headers=H).status_code == 401


def test_login_per_ip_rate_limit(path, tmp_path):
    app = mk(path, tmp_path, limits={"login_ip": (3, 60)})
    c = TestClient(app)
    codes = [c.post("/api/auth/login", json={"username": f"u{i}name", "password": "x" * 12}, headers=H).status_code for i in range(5)]
    assert codes == [401, 401, 401, 429, 429]


def test_api_rate_limit_is_per_user_with_retry_after(path, tmp_path):
    app = mk(path, tmp_path, limits={"api_user": (5, 60)})
    admin = TestClient(app)
    setup_admin(admin)
    add_user(admin, "ann", "analyst")
    ann = sign_in(app, "ann", ROLE_PW["analyst"])
    codes = [ann.get("/api/datasets").status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]
    r = ann.get("/api/datasets")
    assert int(r.headers["retry-after"]) >= 1 and "Try again in" in r.json()["detail"]
    assert admin.get("/api/datasets").status_code == 200                       # someone else's budget is untouched


def test_api_rate_limit_per_ip_covers_unauthenticated_floods(path, tmp_path):
    c = TestClient(mk(path, tmp_path, limits={"api_ip": (4, 60)}))
    codes = [c.get("/api/auth/status").status_code for _ in range(6)]
    assert codes == [200] * 4 + [429] * 2
    assert c.get("/").status_code == 200                                         # the page shell itself is not rate limited


def test_ask_and_upload_have_their_own_tighter_buckets(path, tmp_path):
    app = mk(path, tmp_path, limits={"ask": (2, 60), "upload": (2, 60)})
    admin = TestClient(app)
    setup_admin(admin)
    assert [admin.get("/api/ask", params={"q": "Which records need data review?"}).status_code for _ in range(3)] == [200, 200, 429]
    assert [put(admin, f"a{i}.xlsx").status_code for i in range(3)] == [201, 201, 429]


def test_one_analysis_at_a_time_per_user(path, tmp_path):
    started, release = threading.Event(), threading.Event()

    class Blocking:
        def __init__(self):
            self.messages = NS(create=self._create)

        def _create(self, **kw):
            started.set(); release.wait(10)
            return NS(stop_reason="end_turn", content=[NS(type="text", text="done")], usage=NS(input_tokens=1, output_tokens=1))
    app = create_app(path, demo=False, client=Blocking(), artifacts_dir=str(tmp_path / "a"), upload_dir=str(tmp_path / "up"), auth=True, auth_db=str(tmp_path / "auth.db"), limits=LOOSE)
    one = TestClient(app)
    setup_admin(one)
    two = TestClient(app)
    two.cookies.update(one.cookies)                                                      # same user, second browser tab
    out = []
    t = threading.Thread(target=lambda: out.append(one.get("/api/ask", params={"q": "first question"}).status_code))
    t.start()
    assert started.wait(10)
    busy = two.get("/api/ask", params={"q": "second question"})
    release.set(); t.join(10)
    assert busy.status_code == 429 and "already have an analysis running" in busy.json()["detail"] and out == [200]
    assert two.get("/api/ask", params={"q": "third question"}).status_code == 200      # slot freed once the first finished


# ======================================================== RBAC matrix
def test_viewer_can_read_but_nothing_else(world):
    v = world.vic
    assert all(v.get(u).status_code == 200 for u in ("/api/config", "/api/datasets", "/api/tables", "/api/dashboard", "/api/dq", "/api/profile", "/api/example.xlsx"))
    assert v.get("/api/ask", params={"q": "Which records need data review?"}).status_code == 403
    assert put(v).status_code == 403
    assert v.delete("/api/datasets/abcdef123456", headers=H).status_code == 403
    assert v.get("/api/users").status_code == 403 and v.get("/api/audit").status_code == 403
    assert v.post("/api/users", json={"username": "x-new", "password": GOOD, "role": "admin"}, headers=H).status_code == 403


def test_analyst_can_ask_and_upload_but_not_administer(world):
    a = world.ann
    assert a.get("/api/ask", params={"q": "Which records need data review?"}).status_code == 200
    assert put(a).status_code == 201
    assert a.get("/api/users").status_code == 403 and a.get("/api/audit").status_code == 403
    assert a.patch("/api/users/1", json={"role": "viewer"}, headers=H).status_code == 403
    assert a.post("/api/users", json={"username": "x-new", "password": GOOD, "role": "admin"}, headers=H).status_code == 403


def test_admin_can_do_everything(world):
    ad = world.admin
    assert ad.get("/api/users").status_code == 200 and ad.get("/api/audit").status_code == 200
    assert ad.get("/api/ask", params={"q": "Which records need data review?"}).status_code == 200 and put(ad).status_code == 201


def test_denied_actions_are_audited(world):
    world.vic.get("/api/users")
    ev = world.admin.get("/api/audit", params={"action": "denied"}).json()["events"]
    assert ev and ev[0]["username"] == "vic" and "users" in ev[0]["detail"]


def test_role_changes_take_effect_immediately_by_revoking_sessions(world):
    uid = next(u["id"] for u in world.admin.get("/api/users").json()["users"] if u["username"] == "ann")
    assert world.ann.get("/api/ask", params={"q": "Which records need data review?"}).status_code == 200
    assert world.admin.patch(f"/api/users/{uid}", json={"role": "viewer"}, headers=H).status_code == 200
    assert world.ann.get("/api/datasets").status_code == 401                          # old session is gone; must sign in again
    ann2 = sign_in(world.app, "ann", ROLE_PW["analyst"])
    assert ann2.get("/api/ask", params={"q": "Which records need data review?"}).status_code == 403


def test_disabling_a_user_kills_their_session_and_blocks_login(world):
    uid = next(u["id"] for u in world.admin.get("/api/users").json()["users"] if u["username"] == "ben")
    assert world.admin.patch(f"/api/users/{uid}", json={"disabled": True}, headers=H).status_code == 200
    assert world.ben.get("/api/datasets").status_code == 401
    assert TestClient(world.app).post("/api/auth/login", json={"username": "ben", "password": ROLE_PW["analyst"]}, headers=H).status_code == 401


# ======================================================== dataset isolation
def test_uploads_are_private_to_their_owner(world):
    meta = put(world.ann).json()
    ds = meta["id"]
    assert meta["owner_name"] == "ann"
    assert [d["id"] for d in world.ann.get("/api/datasets").json()] == ["lab", ds]
    for other in (world.ben, world.vic):
        assert [d["id"] for d in other.get("/api/datasets").json()] == ["lab"]
        for url in ("/api/tables", "/api/profile", "/api/schema", "/api/table/orders"):
            assert other.get(url, params={"dataset": ds}).status_code == 404            # 404, not 403: existence is not revealed
    assert world.ben.get("/api/ask", params={"q": "x y z", "dataset": ds}).status_code == 404
    assert world.ben.delete(f"/api/datasets/{ds}", headers=H).status_code == 404
    assert world.ann.get("/api/tables", params={"dataset": ds}).status_code == 200


def test_admin_sees_every_upload_and_can_delete_any(world):
    ds = put(world.ann).json()["id"]
    assert ds in [d["id"] for d in world.admin.get("/api/datasets").json()]
    assert world.admin.get("/api/tables", params={"dataset": ds}).status_code == 200
    assert world.admin.delete(f"/api/datasets/{ds}", headers=H).status_code == 200
    assert world.ann.get("/api/tables", params={"dataset": ds}).status_code == 404


def test_owners_can_delete_their_own_datasets(world):
    ds = put(world.ann).json()["id"]
    assert world.ann.delete(f"/api/datasets/{ds}", headers=H).status_code == 200


def test_dataset_quota_is_per_user_not_global(world, monkeypatch):
    monkeypatch.setattr(datasets, "MAX_DATASETS", 2)
    assert [put(world.ann, f"a{i}.xlsx").status_code for i in range(3)] == [201, 201, 409]
    assert put(world.ben, "b.xlsx").status_code == 201                                  # ann hitting her quota does not block ben


def test_agent_runs_only_on_datasets_the_user_can_see(world):
    ds = put(world.ann).json()["id"]
    assert world.ann.get("/api/ask", params={"q": "total units?", "dataset": ds}).status_code == 200
    assert world.ben.get("/api/ask", params={"q": "total units?", "dataset": ds}).status_code == 404


# ======================================================== user administration
def test_user_admin_lifecycle_and_validation(world):
    ad = world.admin
    bad = ad.post("/api/users", json={"username": "newbie", "password": "weak", "role": "viewer"}, headers=H)
    assert bad.status_code == 400
    assert ad.post("/api/users", json={"username": "ANN", "password": GOOD, "role": "viewer"}, headers=H).status_code == 409
    assert ad.post("/api/users", json={"username": "newbie", "password": GOOD, "role": "root"}, headers=H).status_code == 400
    made = ad.post("/api/users", json={"username": "newbie", "password": GOOD, "role": "viewer"}, headers=H).json()
    assert made["must_change"] is True and "password_hash" not in made                    # admin-set passwords must be changed at first login
    assert ad.delete(f"/api/users/{made['id']}", headers=H).status_code == 200
    assert ad.delete(f"/api/users/{made['id']}", headers=H).status_code == 404
    assert ad.patch("/api/users/9999", json={"role": "viewer"}, headers=H).status_code == 404


def test_admins_cannot_lock_themselves_or_the_system_out(world):
    ad = world.admin
    me = next(u for u in ad.get("/api/users").json()["users"] if u["username"] == "admin")
    assert ad.patch(f"/api/users/{me['id']}", json={"role": "viewer"}, headers=H).status_code == 409
    assert ad.patch(f"/api/users/{me['id']}", json={"disabled": True}, headers=H).status_code == 409
    assert ad.delete(f"/api/users/{me['id']}", headers=H).status_code == 409
    assert ad.get("/api/users").status_code == 200


def test_admin_password_reset_forces_a_change(world):
    uid = next(u["id"] for u in world.admin.get("/api/users").json()["users"] if u["username"] == "vic")
    assert world.admin.patch(f"/api/users/{uid}", json={"password": "Brand-new-reset-pw-9"}, headers=H).json()["must_change"] is True
    assert TestClient(world.app).post("/api/auth/login", json={"username": "vic", "password": ROLE_PW["viewer"]}, headers=H).status_code == 401


def test_users_endpoint_never_exposes_hashes_or_tokens(world):
    raw = world.admin.get("/api/users").text
    assert "scrypt" not in raw and "password_hash" not in raw and "token" not in raw.lower()


# ======================================================== forced change + password change
def test_must_change_blocks_everything_until_the_password_is_changed(path, tmp_path):
    app = mk(path, tmp_path)
    admin = TestClient(app)
    setup_admin(admin)
    add_user(admin, "newbie", "analyst", must_change=True, pw="Temp-password-for-first-login1")
    n = sign_in(app, "newbie", "Temp-password-for-first-login1")
    assert n.get("/api/auth/status").json()["user"]["must_change"] is True
    for url in ("/api/datasets", "/api/tables", "/api/config"):
        r = n.get(url)
        assert r.status_code == 403 and "change your password" in r.json()["detail"]
    assert n.post("/api/auth/password", json={"current": "Temp-password-for-first-login1", "new": "My-own-fresh-phrase-3"}, headers=H).status_code == 200
    assert n.get("/api/datasets").status_code == 200


def test_password_change_rules_and_session_revocation(path, tmp_path):
    app = mk(path, tmp_path)
    c1 = TestClient(app)
    setup_admin(c1)
    c2 = sign_in(app, "admin", GOOD)                                                       # a second device
    P = "/api/auth/password"
    assert c1.post(P, json={"current": "wrong", "new": "Another-good-phrase-4"}, headers=H).status_code == 400
    assert c1.post(P, json={"current": GOOD, "new": GOOD}, headers=H).status_code == 400
    assert c1.post(P, json={"current": GOOD, "new": "weak"}, headers=H).status_code == 400
    assert c1.post(P, json={"current": GOOD}, headers=H).status_code == 400
    assert c1.post(P, json={"current": GOOD, "new": "Another-good-phrase-4"}, headers=H).status_code == 200
    assert c1.get("/api/datasets").status_code == 200 and c2.get("/api/datasets").status_code == 401
    t = TestClient(app)
    assert t.post("/api/auth/login", json={"username": "admin", "password": GOOD}, headers=H).status_code == 401
    assert t.post("/api/auth/login", json={"username": "admin", "password": "Another-good-phrase-4"}, headers=H).status_code == 200


def test_password_change_attempts_are_rate_limited(path, tmp_path):
    app = mk(path, tmp_path, limits={"password": (3, 60)})
    c = TestClient(app)
    setup_admin(c)
    codes = [c.post("/api/auth/password", json={"current": "wrong-wrong-1", "new": "Another-good-phrase-4"}, headers=H).status_code for _ in range(5)]
    assert codes == [400, 400, 400, 429, 429]


# ======================================================== sessions + headers + artifacts
def test_idle_sessions_expire_server_side(path, tmp_path):
    now = [1_000_000.0]
    store = AuthStore(tmp_path / "auth.db", clock=lambda: now[0], idle=600, absolute=86400)
    app = mk(path, tmp_path, store=store)
    admin = TestClient(app)
    setup_admin(admin)
    assert admin.get("/api/datasets").status_code == 200
    now[0] += 601
    assert admin.get("/api/datasets").status_code == 401


def test_only_a_hash_of_the_session_token_is_stored(path, tmp_path):
    app = mk(path, tmp_path)
    c = TestClient(app)
    setup_admin(c)
    token = c.cookies["labops_session"]
    rows = sqlite3.connect(str(tmp_path / "auth.db")).execute("SELECT token_hash FROM sessions").fetchall()
    assert rows and all(token not in r[0] for r in rows)


def test_security_headers_everywhere(path, tmp_path):
    c = TestClient(mk(path, tmp_path))
    for url in ("/", "/api/auth/status", "/static/app.js"):
        h = c.get(url).headers
        assert "default-src 'self'" in h["content-security-policy"] and "frame-ancestors 'none'" in h["content-security-policy"]
        assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY" and h["referrer-policy"] == "same-origin"
        assert "strict-transport-security" not in h                                       # only over https
    assert c.get("/api/auth/status").headers["cache-control"] == "no-store"
    assert "unsafe-eval" not in c.get("/").headers["content-security-policy"] and "script-src 'self' https://cdnjs.cloudflare.com;" in c.get("/").headers["content-security-policy"]


def test_cross_site_fetches_to_the_api_are_refused(path, tmp_path):
    app = mk(path, tmp_path)
    c = TestClient(app)
    setup_admin(c)
    assert c.get("/api/datasets", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert c.get("/api/ask", params={"q": "Which records need data review?"}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert c.get("/api/datasets", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_artifacts_need_a_session_and_cannot_escape_their_folder(path, tmp_path):
    app = mk(path, tmp_path)
    c = TestClient(app)
    setup_admin(c)
    art = tmp_path / "a"
    (art / "abcdef12_chart.png").write_bytes(b"\x89PNG fake")
    (tmp_path / "secret.png").write_bytes(b"nope")
    assert c.get("/artifacts/abcdef12_chart.png").status_code == 200
    assert TestClient(app).get("/artifacts/abcdef12_chart.png").status_code == 401
    for bad in ("../secret.png", "..%2Fsecret.png", "abcdef12_..%2F..%2Fsecret.png", "chart.png", "abcdef12_chart.exe", "abcdef12_missing.png", ".%2e/secret.png"):
        assert c.get(f"/artifacts/{bad}").status_code in (404, 400), bad


# ======================================================== audit
def test_audit_trail_records_actions_without_secrets(world):
    world.vic.get("/api/datasets")
    TestClient(world.app).post("/api/auth/login", json={"username": "ann", "password": "totally-wrong-pw"}, headers=H)
    TestClient(world.app).post("/api/auth/login", json={"username": "x'; DROP--", "password": "totally-wrong-pw"}, headers=H)
    ds = put(world.ann).json()["id"]
    world.ann.get("/api/ask", params={"q": "TOPSECRET question about HPLC-03?", "dataset": "lab"})
    world.ann.delete(f"/api/datasets/{ds}", headers=H)
    events_ = world.admin.get("/api/audit", params={"limit_": 500}).json()["events"]
    actions = [e["action"] for e in events_]
    for a in ("setup", "login_success", "login_failed", "dataset_upload", "dataset_delete", "ask", "user_create"):
        assert a in actions, a
    failed = [e for e in events_ if e["action"] == "login_failed"]
    assert {e["username"] for e in failed} == {"ann", "(invalid)"}                         # junk usernames are not stored verbatim
    blob = " ".join(f"{e['username']} {e['detail']}" for e in events_)
    assert "TOPSECRET" not in blob and GOOD not in blob and "totally-wrong-pw" not in blob and ROLE_PW["analyst"] not in blob
    ask = next(e for e in events_ if e["action"] == "ask")
    assert "len=" in ask["detail"] and "sha=" in ask["detail"]
    raw = " ".join(str(r) for r in sqlite3.connect(str(world.tmp / "auth.db")).execute("SELECT * FROM audit"))
    assert "TOPSECRET" not in raw and "totally-wrong-pw" not in raw


def test_audit_endpoint_filters_and_caps_limit(world):
    assert {e["action"] for e in world.admin.get("/api/audit", params={"action": "setup"}).json()["events"]} == {"setup"}
    assert len(world.admin.get("/api/audit", params={"limit_": 10**6}).json()["events"]) <= 500


# ======================================================== auth disabled mode
def test_auth_disabled_means_a_single_local_admin(path, tmp_path):
    c = TestClient(create_app(path, demo=True, artifacts_dir=str(tmp_path / "a"), upload_dir=str(tmp_path / "up"), auth=False))
    s = c.get("/api/auth/status").json()
    assert s["enabled"] is False and s["user"]["role"] == "admin" and s["setup_required"] is False
    assert c.get("/api/datasets").status_code == 200 and put(c).status_code == 201
    assert c.post("/api/auth/login", json={"username": "a", "password": "b"}, headers=H).status_code == 400
    assert c.post("/api/auth/setup", json={"username": "abc", "password": GOOD}, headers=H).status_code == 400
    assert c.get("/api/users").status_code == 400
    assert not (tmp_path / "auth.db").exists()                                              # no credential store is even created


def test_environment_variable_controls_the_default(path, tmp_path, monkeypatch):
    monkeypatch.setenv("LABOPS_AUTH", "1")
    c = TestClient(create_app(path, demo=True, artifacts_dir=str(tmp_path / "a"), upload_dir=str(tmp_path / "up"), auth_db=str(tmp_path / "auth.db")))
    assert c.get("/api/datasets").status_code == 401
    monkeypatch.setenv("LABOPS_AUTH", "0")
    c2 = TestClient(create_app(path, demo=True, artifacts_dir=str(tmp_path / "a2"), upload_dir=str(tmp_path / "up2")))
    assert c2.get("/api/datasets").status_code == 200


# ======================================================== CLI
def test_cli_creates_lists_and_manages_users_without_passwords_on_the_command_line(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "cli.db")
    monkeypatch.setattr(authmod.getpass, "getpass", lambda prompt="": "Cli-created-pass-77")
    authmod.main(["--auth-db", db_path, "create-user", "Carol", "--role", "analyst"])
    authmod.main(["--auth-db", db_path, "list-users"])
    out = capsys.readouterr().out
    assert "created carol (analyst)" in out and "carol" in out and "active" in out and "Cli-created-pass-77" not in out
    with pytest.raises(SystemExit):
        authmod.main(["--auth-db", db_path, "create-user", "dave", "--password", "x"])         # no such flag by design
    authmod.main(["--auth-db", db_path, "disable", "carol"])
    store = AuthStore(db_path)
    assert store.list_users()[0]["disabled"] is True
    authmod.main(["--auth-db", db_path, "enable", "carol"])
    assert AuthStore(db_path).authenticate("carol", "Cli-created-pass-77")
    assert any(e["username"] == "cli" for e in AuthStore(db_path).audit_list(20))


def test_cli_rejects_weak_passwords_and_mismatches(tmp_path, monkeypatch):
    monkeypatch.setattr(authmod.getpass, "getpass", lambda prompt="": "short")
    with pytest.raises(SystemExit):
        authmod.main(["--auth-db", str(tmp_path / "c.db"), "create-user", "erin"])
    answers = iter(["Good-password-123", "Different-password-123"])
    monkeypatch.setattr(authmod.getpass, "getpass", lambda prompt="": next(answers))
    with pytest.raises(SystemExit, match="do not match"):
        authmod.main(["--auth-db", str(tmp_path / "c.db"), "create-user", "erin"])
