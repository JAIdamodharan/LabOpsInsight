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


ICONS = STATIC / "icons"
BRAND = STATIC / "brand"


def test_user_icons_live_in_the_icons_folder_and_not_in_the_project_root():
    expected = {"dashboard", "analyze", "data", "pinned", "lab_operations", "market-research"}
    assert {p.stem for p in ICONS.glob("*.png")} == expected
    root = Path(__file__).resolve().parent.parent
    assert not [p.name for p in root.glob("*.png")], "icons must not be left in the project root"


def test_every_asset_the_ui_references_exists():
    import re
    js, html, css = (STATIC / "app.js").read_text(), (STATIC / "index.html").read_text(), (STATIC / "style.css").read_text()
    paths = set(re.findall(r"/static/((?:icons|brand)/[\w.\-]+)", js + html + css))
    assert paths, "expected the UI to reference icons or brand assets"
    missing = sorted(p for p in paths if not (STATIC / p).exists())
    assert not missing, missing
    names = set(re.findall(r"pic\('([\w\-]+)'", js)) | set(re.findall(r"\b\w+: '([\w\-]+)'", js.split("const IMG =")[1].split("};")[0]))
    assert {n for n in names if not (ICONS / f"{n}.png").exists()} == set()


def test_brand_assets_are_valid_images():
    from PIL import Image
    for name, size in [("logo-mark.png", (512, 512)), ("favicon-32.png", (32, 32)), ("favicon-64.png", (64, 64)), ("apple-touch-icon.png", (180, 180))]:
        with Image.open(BRAND / name) as im:
            assert im.size == size, name
    with Image.open(BRAND / "logo-mark.png") as im:
        assert im.mode == "RGBA" and im.getchannel("A").getextrema()[0] == 0        # genuinely transparent background
    with Image.open(BRAND / "favicon.ico") as ico:
        assert ico.format == "ICO"
    assert (BRAND / "logo.png").stat().st_size > 5_000 and (BRAND / "logo-dark.png").exists()


def test_brand_and_icon_files_are_served(c):
    for url in ("/static/brand/logo-mark.png", "/static/brand/favicon.ico", "/static/icons/dashboard.png", "/static/icons/lab_operations.png"):
        r = c.get(url)
        assert r.status_code == 200 and len(r.content) > 500, url
