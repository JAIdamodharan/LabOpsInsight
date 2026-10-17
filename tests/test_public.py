"""Public demo mode (used for the hosted Vercel deployment): read and ask only, no uploads, per-visitor limits, proxy-aware addresses."""
import pytest
from fastapi.testclient import TestClient

from analyst.demo import DemoClient
from analyst.server import create_app
from tests.test_analyst import db  # noqa: F401
from tests.test_server import events, path  # noqa: F401


def pub(path, tmp_path, **kw):  # noqa: F811
    return TestClient(create_app(path, demo=True, public=True, artifacts_dir=str(tmp_path / "a"), upload_dir=str(tmp_path / "up"), **kw),
                      base_url="http://demo.vercel.app")


def test_visitors_can_read_without_signing_in(path, tmp_path):  # noqa: F811
    c = pub(path, tmp_path)
    cfg = c.get("/api/config").json()
    assert cfg["public"] is True and cfg["auth"] is False and cfg["demo"] is True and cfg["python"] is False
    assert c.get("/api/dashboard").status_code == 200 and c.get("/api/dq").status_code == 200 and c.get("/api/story").status_code == 200
    me = c.get("/api/auth/status").json()
    assert me["enabled"] is False


def test_visitors_cannot_upload_delete_or_manage_users(path, tmp_path):  # noqa: F811
    c = pub(path, tmp_path)
    h = {"X-Requested-With": "labops", "X-Filename": "a.xlsx"}
    assert c.post("/api/datasets", headers=h, content=b"x").status_code == 403
    assert c.delete("/api/datasets/abcdef123456", headers=h).status_code in (403, 404)
    assert c.get("/api/users").status_code == 403 and c.get("/api/audit").status_code == 403


def test_visitors_can_ask_the_scripted_demo(path, tmp_path):  # noqa: F811
    c = pub(path, tmp_path)
    ev = events(c.get("/api/ask", params={"q": "Which records need data review?"}))
    assert ev[-1]["kind"] == "done" and any(e["kind"] == "answer" for e in ev)


def test_questions_are_limited_per_visitor_address_not_globally(path, tmp_path):  # noqa: F811
    c = pub(path, tmp_path, limits={"ask": (2, 60)})
    a = {"X-Forwarded-For": "203.0.113.5"}
    b = {"X-Forwarded-For": "198.51.100.9"}
    assert [c.get("/api/ask", params={"q": "Which records need data review?"}, headers=a).status_code for _ in range(3)] == [200, 200, 429]
    assert c.get("/api/ask", params={"q": "Which records need data review?"}, headers=b).status_code == 200      # another visitor is unaffected


def test_forwarded_address_is_ignored_outside_public_mode(path, tmp_path):  # noqa: F811
    c = TestClient(create_app(path, demo=True, public=False, auth=False, limits={"ask": (1, 60)}, artifacts_dir=str(tmp_path / "a"), upload_dir=str(tmp_path / "up")))
    q = {"q": "Which records need data review?"}
    assert c.get("/api/ask", params=q, headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
    assert c.get("/api/ask", params=q, headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 429               # spoofing the header buys nothing


def test_vercel_hostnames_are_allowed_only_in_public_mode(path, tmp_path):  # noqa: F811
    off = TestClient(create_app(path, demo=True, auth=False, public=False, artifacts_dir=str(tmp_path / "a"), upload_dir=str(tmp_path / "up")), base_url="http://evil.vercel.app")
    assert off.get("/api/config").status_code == 400
    assert pub(path, tmp_path).get("/api/config").status_code == 200


def test_the_vercel_entrypoint_builds_its_own_database_and_serves(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    monkeypatch.setenv("LABOPS_TMP", str(tmp_path))
    monkeypatch.delenv("LABOPS_DEMO_LIVE", raising=False)
    spec = importlib.util.spec_from_file_location("vercel_entry", Path(__file__).resolve().parent.parent / "api" / "index.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert (tmp_path / "labops" / "labops.duckdb").exists()
    c = TestClient(m.app, base_url="http://x.vercel.app")
    assert c.get("/").status_code == 200 and c.get("/api/config").json()["public"] is True


def test_the_public_page_keeps_an_upload_button_that_explains_itself():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "analyst" / "static" / "app.js").read_text()
    assert "PUBLIC_NOTE" in js and "!can('upload') && !S.config.public" in js and "if (!can('upload')) return toast(PUBLIC_NOTE" in js
