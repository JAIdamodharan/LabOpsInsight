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
    c = client_for([completion(calls=[("run_sql", '{"sql":"SELECT count(*) FROM tests"}')]),
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


def test_xai_preset_needs_key_and_an_explicit_model(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="XAI_API_KEY"):
        llm.make_client("xai", model="some-model")
    monkeypatch.setenv("XAI_API_KEY", "xai-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6")
    with pytest.raises(SystemExit, match="--model"):
        llm.make_client("xai")                                   # no guessed default: model names change
    c, model = llm.make_client("xai", model="some-model")
    assert model == "some-model" and str(c.http.base_url).rstrip("/") == "https://api.x.ai/v1"


@pytest.mark.parametrize("fake", ["paste-your-key-here", "your-groq-key", "<key>", "short", "xxxxxxxxxxxxxxxxxxxxxxxx", "gsk_changeme_changeme_changeme"])
def test_placeholder_keys_are_rejected_with_a_helpful_message(monkeypatch, fake):
    monkeypatch.setenv("GROQ_API_KEY", fake)
    with pytest.raises(SystemExit, match="placeholder text, not a real key"):
        llm.make_client("groq")


def test_a_realistic_key_is_accepted(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6")
    client, model = llm.make_client("groq")
    assert model and client.http.headers["authorization"].startswith("Bearer gsk_")


# ---- model discovery + helpful errors ----
def _models_transport(ids):
    return httpx.MockTransport(lambda req: httpx.Response(200, json={"data": [{"id": i} for i in ids]}) if req.url.path.endswith("/models") else httpx.Response(404))


def test_list_models_sorts_and_hides_non_chat_models(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6")
    ids = ["whisper-large-v3", "openai/gpt-oss-120b", "llama-guard-4", "llama-3.1-8b-instant", "playai-tts"]
    assert llm.list_models("groq", transport=_models_transport(ids)) == ["llama-3.1-8b-instant", "openai/gpt-oss-120b"]
    assert len(llm.list_models("groq", show_all=True, transport=_models_transport(ids))) == 5


def test_list_models_needs_a_real_key_and_a_compatible_provider(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="not set"):
        llm.list_models("groq", transport=_models_transport([]))
    monkeypatch.setenv("GROQ_API_KEY", "paste-your-key-here")
    with pytest.raises(SystemExit, match="placeholder"):
        llm.list_models("groq", transport=_models_transport([]))
    with pytest.raises(SystemExit, match="OpenAI-compatible"):
        llm.list_models("anthropic")


def test_list_models_surfaces_a_rejected_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6")
    t = httpx.MockTransport(lambda req: httpx.Response(401, json={"error": {"message": "Invalid API Key"}}))
    with pytest.raises(SystemExit, match="401"):
        llm.list_models("groq", transport=t)


def test_model_not_found_error_tells_the_user_how_to_fix_it():
    body = {"error": {"message": "The model `gone-model` does not exist or you do not have access to it.", "code": "model_not_found"}}
    c = OpenAICompatClient("http://x/v1", transport=httpx.MockTransport(lambda req: httpx.Response(404, json=body)))
    with pytest.raises(RuntimeError) as e:
        c.messages.create(model="gone-model", max_tokens=5, system="s", tools=TOOLS, messages=[{"role": "user", "content": "q"}])
    msg = str(e.value)
    assert "gone-model" in msg and "analyst.llm models" in msg and "--model" in msg and "ANALYST_MODEL" in msg


def test_analyst_model_from_dotenv_becomes_the_cli_default(tmp_path, monkeypatch):
    import argparse
    for k in ("ANALYST_MODEL", "ANALYST_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    p = tmp_path / ".env"
    p.write_text("ANALYST_MODEL=openai/gpt-oss-120b\nANALYST_PROVIDER=groq\n")
    p.chmod(0o600)
    monkeypatch.chdir(tmp_path)
    ap = argparse.ArgumentParser()
    llm.add_cli_args(ap)
    a = ap.parse_args([])
    assert a.model == "openai/gpt-oss-120b" and a.provider == "groq"
    monkeypatch.delenv("ANALYST_MODEL"); monkeypatch.delenv("ANALYST_PROVIDER")
