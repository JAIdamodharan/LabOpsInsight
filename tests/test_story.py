"""The story page's numbers come from the data; its buttons and text must stay consistent with the app."""
import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from analyst import predict, story
from analyst.server import create_app
from tests.test_analyst import db  # noqa: F401
from tests.test_server import path  # noqa: F401

STATIC = Path(__file__).resolve().parent.parent / "analyst" / "static"


def test_story_facts_match_the_planted_incidents(db):  # noqa: F811
    f = story.build(db, predict.drift(db))
    assert f["drift"]["instrument"] == "HPLC-03" and f["drift"]["oos_hplc03"] > 10 * f["drift"]["oos_others"]
    assert "2025-03-03" <= f["drift"]["alarm_date"] <= "2025-03-10"
    assert f["delivery"]["jan"] - f["delivery"]["feb"] > 3 and f["delivery"]["down"][0] == "LCMS-02"
    assert f["training"]["new_analysts"] == 6 and f["training"]["new_per_analyst"] > 10 * f["training"]["earlier_per_analyst"]
    truth = json.loads((Path(__file__).resolve().parent.parent / "data" / "ground_truth.json").read_text()) if (Path(__file__).resolve().parent.parent / "data" / "ground_truth.json").exists() else None
    assert f["scope"]["tests"] > 10000 and f["review"]["flagged"] > 0 and (truth is None or isinstance(truth, dict))


def test_story_endpoint(path, tmp_path):  # noqa: F811
    r = TestClient(create_app(path, artifacts_dir=str(tmp_path / "a"), upload_dir=str(tmp_path / "up"))).get("/api/story")
    assert r.status_code == 200 and set(r.json()) == {"scope", "review", "drift", "delivery", "training"}


def test_every_filled_value_and_button_target_exists():
    html, js = (STATIC / "index.html").read_text(), (STATIC / "app.js").read_text()
    tabs = set(re.findall(r"const TABS = \[([^\]]*)\]", js)[0].replace("'", "").replace(" ", "").split(","))
    section = (STATIC.parent / "story.html").read_text()
    assert set(re.findall(r'data-go="(\w+)"', section)) <= tabs
    facts = {"scope", "review", "drift", "delivery", "training"}
    assert {k.split(".")[0] for k in re.findall(r'data-k="([^"]+)"', section)} <= facts
    for q in re.findall(r'data-ask="([^"]+)"', section):
        assert 5 < len(q) <= 500


def test_the_page_copy_has_no_em_dashes():
    assert "—" not in (STATIC / "index.html").read_text()
