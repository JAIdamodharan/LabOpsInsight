import json

import httpx
import pytest

from analyst import llm
from analyst.agent import Analyst, TOOLS
from analyst.llm import OpenAICompatClient, from_openai_response, to_openai_messages
from tests.test_analyst import db  # noqa: F401


def completion(content=None, calls=None):
    msg = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = [{"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": a}}
                             for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": msg}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def client_for(responses, seen=None, status=None):
    it = iter(responses)

    def handler(req):
        if seen is not None:
            seen.append(json.loads(req.content))
        code = status.pop(0) if status else 200
        if code != 200:
            return httpx.Response(code, headers={"retry-after": "0"}, text="slow down")
        return httpx.Response(200, json=next(it))
    return OpenAICompatClient("http://x/v1", transport=httpx.MockTransport(handler))


def test_response_translation_tool_call_and_bad_json():
    r = from_openai_response(completion(calls=[("run_sql", '{"sql":"select 1"}'), ("run_sql", "{not json")]))
    assert r.stop_reason == "tool_use" and r.usage.input_tokens == 10
    assert r.content[0].input == {"sql": "select 1"} and r.content[1].input == {}


def test_message_translation_roundtrip():
    from types import SimpleNamespace as NS
    msgs = [{"role": "user", "content": "q"},
            {"role": "assistant", "content": [NS(type="text", text="hm"), NS(type="tool_use", id="c0", name="run_sql", input={"sql": "s"})]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c0", "content": "rows", "is_error": False}]}]
    out = to_openai_messages("SYS", msgs)
    assert [m["role"] for m in out] == ["system", "user", "assistant", "tool"]
    assert out[2]["tool_calls"][0]["function"]["arguments"] == '{"sql": "s"}'
    assert out[3] == {"role": "tool", "tool_call_id": "c0", "content": "rows"}


def test_agent_runs_end_to_end_over_openai_protocol(db):
    seen = []
    c = client_for([completion(calls=[("run_sql", '{"sql":"SELECT count(*) FROM orders"}')]),
                    completion("There are many orders.")], seen)
    agent = Analyst(db, client=c, model="m")
    list(agent.run("how many orders?"))
    assert agent.last_stats.stop_reason == "answered" and agent.last_stats.sql_calls == 1
    assert seen[0]["tools"][0]["type"] == "function" and seen[0]["model"] == "m"
    assert seen[1]["messages"][-1]["role"] == "tool"          # tool result fed back correctly


def test_retries_on_429_then_succeeds(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    c = client_for([completion("ok")], status=[429, 503, 200])
    r = c.messages.create(model="m", max_tokens=10, system="s", tools=TOOLS, messages=[{"role": "user", "content": "q"}])
    assert r.content[0].text == "ok"


def test_hard_error_raises(monkeypatch):
    c = client_for([], status=[401])
    with pytest.raises(RuntimeError, match="401"):
        c.messages.create(model="m", max_tokens=10, system="s", tools=TOOLS, messages=[{"role": "user", "content": "q"}])


def test_make_client_requires_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="GROQ_API_KEY"):
        llm.make_client("groq")
    c, model = llm.make_client("ollama")
    assert model == "llama3.2"


def test_rescues_tool_call_written_as_text():
    txt = 'The fix is:\n\n{"name": "run_sql", "parameters": {"sql": "SELECT 1"}}'
    r = from_openai_response(completion(txt), tool_names=["run_sql"])
    assert r.stop_reason == "tool_use" and r.content[0].input == {"sql": "SELECT 1"}
    # unknown tool names and ordinary JSON answers are left alone
    assert from_openai_response(completion('{"name": "bob"}'), ["run_sql"]).stop_reason == "end_turn"
    assert from_openai_response(completion("plain answer"), ["run_sql"]).stop_reason == "end_turn"
