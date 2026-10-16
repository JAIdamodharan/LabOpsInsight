"""Prediction module: honest validation (time split), drift detection against the planted incident, and the API."""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from analyst import predict
from analyst.server import create_app
from tests.test_analyst import db  # noqa: F401
from tests.test_server import path  # noqa: F401


@pytest.fixture(scope="module")
def bundle(db):  # noqa: F811
    return predict.train(db)


def test_drift_monitor_finds_the_planted_hplc03_assay_drift_and_sees_it_clear(db):  # noqa: F811
    d = predict.drift(db)
    top = d["alarms"][0]
    assert (top["instrument"], top["method"], top["direction"]) == ("HPLC-03", "Assay", "up")
    assert "2025-03-03" <= top["first_alarm"] <= "2025-03-10"                       # drift starts 3 Mar; caught within days
    assert "2025-03-24" <= top["last_alarm"] <= "2025-04-05" and not top["active"]   # recalibrated 24 Mar
    assert top["peak_shift_sd"] > 2 and len(d["alarms"]) <= 3                         # few false alarms
    assert d["chart"]["instrument"] == "HPLC-03" and d["chart"]["points"]


def test_late_model_is_validated_on_a_later_period_and_beats_chance(bundle):
    i = bundle.info["late"]
    assert i["train_until"] < str(bundle.frame["completed_date"].max().date())
    assert i["models"][i["best"]]["roc_auc"] > 0.9 and i["models"][i["best"]]["lift"] > 3
    assert i["importance"] and all(r["importance"] > 0 for r in i["importance"])
    assert "oos" not in bundle.models                                                # the chance-level OOS classifier was dropped on purpose


def test_no_feature_uses_the_outcome_or_future_information(bundle):
    cols = set(predict.CAT) | set(predict.TARGETS["late"]["num"])
    assert not cols & {"late", "oos", "status", "completed_date", "result_value"}


def test_scoring_responds_sensibly_and_rejects_unknown_values(bundle):
    o = bundle.info["options"]
    low = predict.score(bundle, o["methods"][0], o["instruments"][0], o["priorities"][0], 0)["late"]
    high = predict.score(bundle, o["methods"][0], o["instruments"][0], o["priorities"][0], 8)["late"]
    assert 0 <= low < high <= 1
    with pytest.raises(ValueError):
        predict.score(bundle, "Nope", o["instruments"][0], o["priorities"][0])


def test_forecast_has_three_ordered_future_months_with_valid_intervals(bundle):
    f = predict.forecast(bundle.frame)
    assert len(f["future"]) == 3 and f["future"][0] > f["months"][-1]
    for s in f["series"].values():
        assert len(s["history"]) == len(f["months"]) and len(s["forecast"]) == 3
        assert all(p["lo"] <= p["value"] <= p["hi"] for p in s["forecast"])
    assert all(0 <= p["lo"] and p["hi"] <= 100 for p in f["series"]["on_time"]["forecast"])


def test_forecast_series_picks_the_trend_for_a_clean_trend_and_the_mean_for_noise():
    assert predict.forecast_series(list(np.arange(10, 20, dtype=float)))["model"].startswith("Linear")
    rng = np.random.default_rng(1)
    noisy = list(50 + rng.normal(0, 5, 12))
    assert predict.forecast_series(noisy)["model"] in {"Average", "Last value", "Linear trend (last 6 months)"}


def test_api_returns_everything_the_tab_needs_and_validates_scoring(path, tmp_path):  # noqa: F811
    c = TestClient(create_app(path, artifacts_dir=str(tmp_path / "a"), upload_dir=str(tmp_path / "up")))
    r = c.get("/api/predict")
    assert r.status_code == 200
    j = r.json()
    assert set(j) >= {"forecast", "drift", "late", "options", "as_of"} and j["drift"]["alarms"]
    o = j["options"]
    ok = c.get("/api/predict/score", params={"method": o["methods"][0], "instrument": o["instruments"][0], "priority": o["priorities"][0], "queue_days": 2})
    assert ok.status_code == 200 and 0 <= ok.json()["late"] <= 1
    assert c.get("/api/predict/score", params={"method": "x", "instrument": "y", "priority": "z"}).status_code == 400
