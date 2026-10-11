import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from analyst.server import STATIC, create_app
from tests.test_analyst import db  # noqa: F401
from tests.test_server import path  # noqa: F401


@pytest.fixture
def c(path, tmp_path):
    return TestClient(create_app(path, demo=True, artifacts_dir=str(tmp_path / "a"), upload_dir=str(tmp_path / "up")))


def test_index_is_versioned_and_not_cached(c):
    r = c.get("/")
    assert r.status_code == 200 and "__V__" not in r.text and "/static/app.js?v=" in r.text and "/static/style.css?v=" in r.text
    assert r.headers["cache-control"] == "no-store"


def test_static_assets_served(c):
    js, css = c.get("/static/app.js"), c.get("/static/style.css")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"] and "function applyDataset" in js.text
    assert css.status_code == 200 and "text/css" in css.headers["content-type"]


@pytest.mark.parametrize("url", ["/static/../server.py", "/static/%2e%2e/server.py", "/static/..%2fdb.py"])
def test_static_cannot_escape_its_directory(c, url):
    assert c.get(url).status_code in (404, 400)


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_app_js_is_syntactically_valid():
    r = subprocess.run(["node", "--check", str(STATIC / "app.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_every_element_id_the_script_uses_exists_in_the_page():
    import re
    html, js = (STATIC / "index.html").read_text(), (STATIC / "app.js").read_text()
    ids_in_html = set(re.findall(r'id="([^"]+)"', html))
    used = set(re.findall(r"\$\('#([A-Za-z0-9_-]+)'\)", js))
    dynamic = {"pincount", "dq-list", "prog"}                # created at runtime or handled specially
    assert used - ids_in_html - dynamic == set(), used - ids_in_html - dynamic
