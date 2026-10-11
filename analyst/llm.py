"""Model backends. The agent speaks Anthropic's messages format; OpenAICompatClient translates it to
any OpenAI-compatible /chat/completions endpoint (Ollama, Groq, Gemini, OpenRouter, vLLM...), so the
agent code is unchanged and benchmarks are comparable across providers."""
import json
import os
import time
import uuid
from dataclasses import dataclass
from types import SimpleNamespace as NS

import httpx


@dataclass(frozen=True)
class Preset:
    base_url: str
    key_env: str | None
    model: str
    note: str


# Default model names change over time: override with --model if a provider has retired one.
PRESETS = {
    "ollama": Preset("http://localhost:11434/v1", None, "llama3.2", "free, local; needs `ollama serve`"),
    "groq": Preset("https://api.groq.com/openai/v1", "GROQ_API_KEY", "llama-3.3-70b-versatile", "free tier, rate limited"),
    "gemini": Preset("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY", "gemini-2.5-flash", "free tier, rate limited"),
    "xai": Preset("https://api.x.ai/v1", "XAI_API_KEY", "", "Grok (xAI); pass --model with a model name from your xAI console"),
    "openrouter": Preset("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "meta-llama/llama-3.3-70b-instruct:free", "has :free models"),
}


def _get(o, k, default=None):
    return o.get(k, default) if isinstance(o, dict) else getattr(o, k, default)


def to_openai_messages(system: str, messages: list) -> list[dict]:
    out = [{"role": "system", "content": system}]
    for m in messages:
        role, content = _get(m, "role"), _get(m, "content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
        elif role == "assistant":
            text = "".join(_get(b, "text", "") for b in content if _get(b, "type") == "text")
            calls = [{"id": _get(b, "id"), "type": "function",
                      "function": {"name": _get(b, "name"), "arguments": json.dumps(_get(b, "input", {}))}}
                     for b in content if _get(b, "type") == "tool_use"]
            msg = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        else:  # user turn made of tool_result blocks
            for b in content:
                out.append({"role": "tool", "tool_call_id": _get(b, "tool_use_id"), "content": str(_get(b, "content", ""))})
    return out


def to_openai_tools(tools: list[dict]) -> list[dict]:
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                              "parameters": t["input_schema"]}} for t in tools]


def _rescue_text_tool_call(text: str, tool_names) -> dict | None:
    """Small models often emit a tool call as JSON text instead of a structured call. Recover it."""
    dec, i = json.JSONDecoder(), 0
    while (i := text.find("{", i)) != -1:
        try:
            obj, end = dec.raw_decode(text[i:])
        except json.JSONDecodeError:
            i += 1
            continue
        if isinstance(obj, dict) and obj.get("name") in tool_names:
            args = obj.get("parameters", obj.get("arguments", {}))
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            return {"id": f"call_{uuid.uuid4().hex[:8]}", "function": {"name": obj["name"], "arguments": args if isinstance(args, dict) else {}}}
        i += end if end > 0 else 1
    return None


def from_openai_response(data: dict, tool_names=()) -> NS:
    msg = data["choices"][0]["message"]
    blocks = []
    calls = list(msg.get("tool_calls") or [])
    if not calls and msg.get("content") and tool_names:
        rescued = _rescue_text_tool_call(msg["content"], set(tool_names))
        if rescued:
            calls, msg = [rescued], {**msg, "content": None}
    if msg.get("content"):
        blocks.append(NS(type="text", text=msg["content"]))
    for c in calls:
        raw = c["function"].get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            args = {}  # malformed args from a weak model: the tool will reject and the agent can retry
        blocks.append(NS(type="tool_use", id=c.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                         name=c["function"]["name"], input=args if isinstance(args, dict) else {}))
    u = data.get("usage") or {}
    return NS(stop_reason="tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn",
              content=blocks,
              usage=NS(input_tokens=u.get("prompt_tokens", 0), output_tokens=u.get("completion_tokens", 0)))


class OpenAICompatClient:
    def __init__(self, base_url: str, api_key: str | None = None, timeout: float = 300,
                 max_retries: int = 4, transport: httpx.BaseTransport | None = None):
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.http = httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout, transport=transport)
        self.max_retries = max_retries
        self.messages = NS(create=self._create)

    def _create(self, *, model, max_tokens, system, tools, messages, **_):
        body = {"model": model, "max_tokens": max_tokens, "temperature": 0,
                "messages": to_openai_messages(system, messages), "tools": to_openai_tools(tools)}
        for attempt in range(self.max_retries + 1):
            r = self.http.post("/chat/completions", json=body)
            if r.status_code in (429, 500, 502, 503) and attempt < self.max_retries:
                wait = float(r.headers.get("retry-after") or 0) or min(2 ** attempt * 2, 30)
                time.sleep(min(wait, 60))  # free tiers rate-limit; back off instead of failing the run
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"{r.status_code} from {self.http.base_url}: {r.text[:300]}")
            return from_openai_response(r.json(), [t["name"] for t in tools])


def looks_like_placeholder(key: str) -> bool:
    k = key.strip().lower()
    return len(k) < 20 or any(w in k for w in ("paste", "your-", "your_", "<", "here", "xxxx", "changeme", "example", "placeholder"))


def make_client(provider: str = "anthropic", model: str | None = None, base_url: str | None = None):
    """Return (client, model). provider is 'anthropic', a PRESETS name, or 'openai' with --base-url."""
    if provider == "anthropic":
        import anthropic
        from .agent import MODEL
        return anthropic.Anthropic(), model or MODEL
    p = PRESETS.get(provider)
    if p is None and not base_url:
        raise SystemExit(f"unknown provider '{provider}'. choose: anthropic, {', '.join(PRESETS)}, or openai with --base-url")
    url = base_url or p.base_url
    key_env = p.key_env if p else "OPENAI_API_KEY"
    key = os.environ.get(key_env) if key_env else None
    if key_env and not key and (p is None or p.key_env):
        raise SystemExit(f"set {key_env} for provider '{provider}' (free keys: see README)")
    if key and looks_like_placeholder(key):
        raise SystemExit(f"{key_env} is set to '{key[:24]}', which is placeholder text, not a real key. Copy the actual key from your provider's console "
                         f"and run: export {key_env}=\"<the real key>\"")
    return OpenAICompatClient(url, key), model or (p.model if p else None) or _need_model()


def _need_model():
    raise SystemExit("--model is required for a custom --base-url")


def add_cli_args(ap) -> None:
    ap.add_argument("--provider", default=os.environ.get("ANALYST_PROVIDER", "anthropic"),
                    help="anthropic | ollama | groq | gemini | openrouter | openai (with --base-url)")
    ap.add_argument("--model", default=os.environ.get("ANALYST_MODEL"))
    ap.add_argument("--base-url")
