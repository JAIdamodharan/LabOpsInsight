"""Rate-limit waits must be visible and cancellable, long streams must stay alive, and the model must not be flooded with rows."""
import asyncio
import json
import threading
import time
from types import SimpleNamespace as NS

import httpx
import pytest
from fastapi.testclient import TestClient

from analyst import llm, server
from analyst.agent import TOOLS, Analyst
from analyst.llm import OpenAICompatClient
from analyst.server import create_app
from tests.test_analyst import FakeClient, db, msg, text, tool  # noqa: F401
from tests.test_llm import completion
from tests.test_server import events, path  # noqa: F401


def throttled(retry_after="30", then_ok=False):
    n = {"calls": 0}

    def handler(req):
        n["calls"] += 1
        if then_ok and n["calls"] > 1:
            return httpx.Response(200, json=completion("hello"))
        return httpx.Response(429, headers={"retry-after": retry_after}, json={"error": {"message": "rate limit"}})
    return OpenAICompatClient("http://x/v1", transport=httpx.MockTransport(handler)), n


def ask(c):
    return c.messages.create(model="m", max_tokens=5, system="s", tools=TOOLS, messages=[{"role": "user", "content": "q"}])


# ---------------- the client: visible, cancellable waiting
def test_waits_are_announced_on_stderr_by_default(monkeypatch, capsys):
    slept = []
    monkeypatch.setattr(llm.time, "sleep", lambda s: slept.append(s))
    c, n = throttled("7", then_ok=True)
    assert ask(c).content[0].text == "hello" and slept == [7.0]
    err = capsys.readouterr().err
    assert "rate limiting" in err and "7s" in err and "1/4" in err and "Ctrl+C" in err


def test_wait_hook_receives_seconds_attempt_and_status(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    c, _ = throttled("2", then_ok=True)
    seen = []
    c.on_wait = lambda *a: seen.append(a)
    ask(c)
    assert seen == [(2.0, 1, 4, 429)]


def test_a_wait_is_capped_at_a_minute_and_survives_a_bad_retry_after(monkeypatch):
    slept = []
    monkeypatch.setattr(llm.time, "sleep", lambda s: slept.append(s))
    c, _ = throttled("9999", then_ok=True); ask(c)
    c2, _ = throttled("soon", then_ok=True); ask(c2)
    assert slept[0] == 60 and slept[1] == 2                                  # non-numeric header falls back to the exponential default


def test_cancelling_ends_a_long_wait_immediately():
    c, n = throttled("30")
    c.on_wait = lambda *a: None
    c.cancel = threading.Event()
    threading.Timer(0.2, c.cancel.set).start()
    t0 = time.perf_counter()
    with pytest.raises(RuntimeError, match="cancelled"):
        ask(c)
    assert time.perf_counter() - t0 < 3 and n["calls"] == 1                  # did not sleep 30 s and did not retry


# ---------------- the server stream
class Waiter:
    """Pretends the provider rate-limited once: tells the server hook, then answers."""
    def __init__(self, delay=0.0):
        self.messages = NS(create=self._create); self.on_wait = None; self.cancel = None; self.delay = delay

    def _create(self, **kw):
        if self.on_wait:
            self.on_wait(3.0, 1, 4, 429)
        time.sleep(self.delay)
        return NS(stop_reason="end_turn", content=[NS(type="text", text="done")], usage=NS(input_tokens=1, output_tokens=1))


def make(path, tmp_path, client):
    return TestClient(create_app(path, client=client, artifacts_dir=str(tmp_path / "a"), upload_dir=str(tmp_path / "up")))


def test_stream_relays_waiting_notices_before_the_answer(path, tmp_path):
    ev = events(make(path, tmp_path, Waiter()).get("/api/ask", params={"q": "hello there"}))
    kinds = [e["kind"] for e in ev]
    assert kinds == ["waiting", "answer", "done"]
    assert ev[0] == {"kind": "waiting", "seconds": 3, "attempt": 1, "of": 4, "status": 429}


def test_stream_sends_keepalive_comments_during_long_silences(path, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "KEEPALIVE_S", 0.05)
    r = make(path, tmp_path, Waiter(delay=0.4)).get("/api/ask", params={"q": "hello there"})
    assert r.text.count(": keepalive") >= 3
    assert [e["kind"] for e in events(r)][-1] == "done"                          # keepalives do not disturb the real events


def test_agent_errors_still_reach_the_browser_as_an_error_event(path, tmp_path):
    class Boom:
        def __init__(self): self.messages = NS(create=self._c)
        def _c(self, **kw): raise RuntimeError("provider exploded")
    ev = events(make(path, tmp_path, Boom()).get("/api/ask", params={"q": "hello there"}))
    assert ev[-1]["kind"] == "error" and "provider exploded" in ev[-1]["message"]


def test_slots_are_released_after_a_stream_so_the_next_question_runs(path, tmp_path):
    c = make(path, tmp_path, Waiter())
    assert [c.get("/api/ask", params={"q": "hello there"}).status_code for _ in range(5)] == [200] * 5


def test_disconnecting_mid_analysis_frees_the_lock_at_once(path, tmp_path):
    """Stop / clicking another example closes the stream; the very next question must not get a 429."""
    calls = {"n": 0}

    class FirstSlow(Waiter):
        def _create(self, **kw):
            calls["n"] += 1
            time.sleep(3.0 if calls["n"] == 1 else 0.0)
            return NS(stop_reason="end_turn", content=[NS(type="text", text="done")], usage=NS(input_tokens=1, output_tokens=1))

    app = create_app(path, client=FirstSlow(), artifacts_dir=str(tmp_path / "a"), upload_dir=str(tmp_path / "up"))

    async def first_then_disconnect():
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http", "path": "/api/ask",
                 "raw_path": b"/api/ask", "query_string": b"q=hello+there", "root_path": "", "client": ("127.0.0.1", 5000),
                 "server": ("testserver", 80), "headers": [(b"host", b"testserver")]}

        async def receive():
            await asyncio.sleep(0.4)
            return {"type": "http.disconnect"}

        async def send(msg):
            pass
        await app(scope, receive, send)

    t0 = time.perf_counter()
    asyncio.run(first_then_disconnect())
    assert time.perf_counter() - t0 < 2.5                                       # returned on disconnect, not after the 3 s model call
    assert TestClient(app).get("/api/ask", params={"q": "hello there"}).status_code == 200


# ---------------- what the model is sent
def test_the_model_gets_a_trimmed_result_while_the_user_gets_every_row(db):
    client = FakeClient([msg("tool_use", tool("1", "run_sql", sql="SELECT test_id, method, status FROM tests LIMIT 120")), msg("end_turn", text("ok"))])
    ev = [e for e in Analyst(db, client=client).run("q") if e.kind == "tool_result"][0]
    assert len(ev.data["table"]["rows"]) == 120                                    # the grid in the UI shows all of them
    lines = ev.data["text"].splitlines()
    assert len(lines) <= 1 + 40 + 1 and "showing 40 of 120 rows" in ev.data["text"] and "Aggregate" in ev.data["text"]


def test_small_results_are_sent_whole_and_huge_text_is_capped(db):
    small = db.run("SELECT method, count(*) n FROM tests GROUP BY 1 ORDER BY 1").to_text(40)
    assert "showing" not in small and small.count("\n") == 5
    wide = db.run("SELECT repeat('x', 500) AS c FROM range(30)").to_text(40, max_chars=2000)
    assert len(wide) < 2100 and wide.endswith("(output shortened)")
