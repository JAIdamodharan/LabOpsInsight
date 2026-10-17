"""Spending protection for a public live demo: budgets, graceful fallback to the scripted walkthrough, cost caps, shared store."""
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from analyst.quota import LivePolicy, MemoryStore, UpstashStore, store_from_env
from analyst.server import create_app
from tests.test_analyst import FakeClient, db, msg, text  # noqa: F401
from tests.test_server import events, path  # noqa: F401


class Counting:
    """A live model that always answers at once, and counts how often it is called."""
    def __init__(self):
        self.calls = 0
        from types import SimpleNamespace as NS
        self.messages = NS(create=self._c)
        self.NS = NS

    def _c(self, **kw):
        self.calls += 1
        return msg("end_turn", text("LIVE ANSWER"))


def app(path, tmp_path, client, monkeypatch, **env):  # noqa: F811
    for k in ("UPSTASH_REDIS_REST_URL", "KV_REST_API_URL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    return TestClient(create_app(path, demo=False, public=True, client=client, model="live-model", artifacts_dir=str(tmp_path / "a"), upload_dir=str(tmp_path / "up"),
                                 limits={"ask": (100, 60), "api_ip": (1000, 60)}), base_url="http://x.vercel.app")


ASK = {"q": "Which records need data review?"}


def kinds(c, ip="203.0.113.1", q=None):
    return [e["kind"] for e in events(c.get("/api/ask", params=q or ASK, headers={"X-Forwarded-For": ip}))]


def test_the_memory_store_counts_and_expires():
    s = MemoryStore()
    assert [s.incr("k", 60) for _ in range(3)] == [1, 2, 3]
    s.incr("short", 0)
    time.sleep(0.01)
    assert s.incr("short", 60) == 1


def test_a_visitor_gets_live_answers_up_to_the_daily_limit_then_the_scripted_demo_with_a_notice(path, tmp_path, monkeypatch):  # noqa: F811
    live = Counting()
    c = app(path, tmp_path, live, monkeypatch, LABOPS_LIVE_IP_DAILY=2, LABOPS_LIVE_IP_MINUTE=50)
    assert kinds(c) == ["answer", "done"] and kinds(c) == ["answer", "done"] and live.calls == 2
    third = kinds(c)
    assert third[0] == "notice" and "done" in third and live.calls == 2                 # the paid model was not called again
    ev = events(c.get("/api/ask", params=ASK, headers={"X-Forwarded-For": "203.0.113.1"}))
    assert "today's 2 live answers" in ev[0]["text"] and any(e["kind"] == "tool_call" for e in ev)   # the scripted walkthrough really ran


def test_the_per_minute_limit_slows_bursts(path, tmp_path, monkeypatch):  # noqa: F811
    live = Counting()
    c = app(path, tmp_path, live, monkeypatch, LABOPS_LIVE_IP_MINUTE=1, LABOPS_LIVE_IP_DAILY=50)
    kinds(c)
    assert kinds(c)[0] == "notice" and live.calls == 1


def test_the_global_daily_budget_caps_total_spend_across_visitors(path, tmp_path, monkeypatch):  # noqa: F811
    live = Counting()
    c = app(path, tmp_path, live, monkeypatch, LABOPS_LIVE_GLOBAL_DAILY=2, LABOPS_LIVE_IP_DAILY=50, LABOPS_LIVE_IP_MINUTE=50)
    for ip in ("1.1.1.1", "2.2.2.2"):
        assert kinds(c, ip)[0] == "answer"
    third = events(c.get("/api/ask", params=ASK, headers={"X-Forwarded-For": "3.3.3.3"}))
    assert third[0]["kind"] == "notice" and "budget" in third[0]["text"] and live.calls == 2


def test_each_live_answer_has_cost_caps_and_questions_are_shorter(path, tmp_path, monkeypatch):  # noqa: F811
    seen = {}
    import analyst.server as srv
    real = srv.Analyst

    class Spy(real):
        def __init__(self, *a, **k):
            seen.update(steps=k.get("max_steps"), budget=k.get("max_tokens_budget"))
            super().__init__(*a, **k)
    monkeypatch.setattr(srv, "Analyst", Spy)
    c = app(path, tmp_path, Counting(), monkeypatch, LABOPS_LIVE_MAX_STEPS=4, LABOPS_LIVE_TOKEN_BUDGET=12000, LABOPS_LIVE_MAX_QUESTION=60)
    kinds(c)
    assert seen == {"steps": 4, "budget": 12000}
    assert c.get("/api/ask", params={"q": "x" * 61}).status_code == 400
    assert c.get("/api/config").json()["live_limits"]["ip_daily"] == 6


def test_no_policy_without_a_live_model_or_outside_public_mode(path, tmp_path, monkeypatch):  # noqa: F811
    scripted = TestClient(create_app(path, demo=True, public=True, artifacts_dir=str(tmp_path / "a"), upload_dir=str(tmp_path / "u")), base_url="http://x.vercel.app")
    assert scripted.get("/api/config").json()["live_limits"] is None
    private = TestClient(create_app(path, demo=False, public=False, auth=False, client=Counting(), model="m", artifacts_dir=str(tmp_path / "a2"), upload_dir=str(tmp_path / "u2")))
    assert private.get("/api/config").json()["live_limits"] is None


def test_upstash_store_sends_atomic_incr_with_expiry_and_reads_the_count():
    sent = []

    def handler(req):
        sent.append((str(req.url), req.headers["authorization"], json.loads(req.content)))
        return httpx.Response(200, json=[{"result": 7}, {"result": 1}])
    s = UpstashStore("https://example.upstash.io/", "tok", transport=httpx.MockTransport(handler))
    assert s.incr("labops:x", 90) == 7 and s.shared
    assert sent[0][0] == "https://example.upstash.io/pipeline" and sent[0][1] == "Bearer tok"
    assert sent[0][2] == [["INCR", "labops:x"], ["EXPIRE", "labops:x", "90", "NX"]]


def test_the_policy_fails_closed_when_the_shared_store_is_down():
    def boom(req):
        raise httpx.ConnectError("down")
    p = LivePolicy(UpstashStore("https://example.upstash.io", "t", transport=httpx.MockTransport(boom)))
    ok, why = p.allow("1.2.3.4")
    assert ok is False and "paused" in why


def test_store_selection_follows_the_environment(monkeypatch):
    for k in ("UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN", "KV_REST_API_URL", "KV_REST_API_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert isinstance(store_from_env(), MemoryStore)
    monkeypatch.setenv("KV_REST_API_URL", "https://a.upstash.io")
    monkeypatch.setenv("KV_REST_API_TOKEN", "t")
    assert isinstance(store_from_env(), UpstashStore)


def test_bad_numbers_in_the_environment_fall_back_to_safe_defaults(monkeypatch):
    monkeypatch.setenv("LABOPS_LIVE_IP_DAILY", "lots")
    monkeypatch.setenv("LABOPS_LIVE_MAX_STEPS", "0")
    p = LivePolicy.from_env(MemoryStore())
    assert p.ip_daily == 6 and p.max_steps == 1
