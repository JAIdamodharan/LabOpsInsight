"""Model backends. The agent speaks Anthropic's messages format; OpenAICompatClient translates it to
any OpenAI-compatible /chat/completions endpoint (Ollama, Groq, Gemini, OpenRouter, vLLM...), so the
agent code is unchanged and benchmarks are comparable across providers."""
import json
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
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


def _print_wait(wait: float, attempt: int, of: int, status: int) -> None:
    why = "rate limiting you (normal on free tiers)" if status == 429 else f"returned HTTP {status}"
    print(f"note: the provider is {why}; waiting {wait:.0f}s before retry {attempt}/{of}. Press Ctrl+C to cancel.", file=sys.stderr, flush=True)


# tried in this order when a preset's default model is not available to the user's key (and they did not choose one themselves)
PREFERRED = ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "qwen/qwen3.8-27b", "qwen/qwen3.6-27b", "qwen/qwen3-32b", "openai/gpt-oss-20b", "minimaxai/minimax-m2.7", "llama-3.1-8b-instant"]


class OpenAICompatClient:
    def __init__(self, base_url: str, api_key: str | None = None, timeout: float = 300,
                 max_retries: int = 4, transport: httpx.BaseTransport | None = None, auto_model: bool = False):
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.http = httpx.Client(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout, transport=transport)
        self.max_retries = max_retries
        self.auto_model = auto_model          # True only when the model came from a preset default, never when the user chose it
        self.model_used: str | None = None
        self.on_wait = None                   # optional hook(seconds, attempt, of, status); default prints a note to stderr
        self.cancel: threading.Event | None = None   # when set (by the server), waits end early and the call is abandoned
        self.messages = NS(create=self._create)

    def _pick_available_model(self) -> str | None:
        r = self.http.get("/models")
        if r.status_code >= 400:
            return None
        ids = [m["id"] for m in r.json().get("data", []) if isinstance(m, dict) and "id" in m and not any(w in m["id"].lower() for w in NON_CHAT)]
        return next((m for m in PREFERRED if m in ids), None) or next((m for m in sorted(ids) if any(w in m.lower() for w in ("gpt-oss", "llama", "qwen", "instruct"))), None)

    def _create(self, *, model, max_tokens, system, tools, messages, **_):
        model = self.model_used or model
        body = {"model": model, "max_tokens": max_tokens, "temperature": 0,
                "messages": to_openai_messages(system, messages), "tools": to_openai_tools(tools)}
        for attempt in range(self.max_retries + 1):
            r = self.http.post("/chat/completions", json=body)
            if r.status_code in (429, 500, 502, 503) and attempt < self.max_retries:
                try:
                    wait = float(r.headers.get("retry-after") or 0) or min(2 ** attempt * 2, 30)
                except ValueError:
                    wait = min(2 ** attempt * 2, 30)
                wait = min(wait, 60)  # free tiers rate-limit; back off (visibly) instead of failing the run
                (self.on_wait or _print_wait)(wait, attempt + 1, self.max_retries, r.status_code)
                if self.cancel is not None:
                    if self.cancel.wait(wait):
                        raise RuntimeError("cancelled")
                else:
                    time.sleep(wait)
                continue
            if r.status_code == 404 and "model" in r.text.lower() and self.auto_model and not self.model_used:
                pick = self._pick_available_model()
                if pick and pick != model:
                    print(f"note: the default model '{model}' is not available to your key; using '{pick}' instead "
                          f"(choose one yourself with --model NAME or ANALYST_MODEL in .env).", file=sys.stderr)
                    self.model_used = pick
                    body["model"] = pick
                    continue
            if r.status_code == 404 and "model" in r.text.lower():
                raise RuntimeError(f"The model '{model}' is not available for this key ({r.status_code}). Providers retire model names, or restrict them per project. "
                                   f"List what your account can use:  .venv/bin/python -m analyst.llm models --provider <name>  "
                                   f"then pass --model NAME, or put ANALYST_MODEL=NAME in your .env file.")
            if r.status_code >= 400:
                raise RuntimeError(f"{r.status_code} from {self.http.base_url}: {r.text[:300]}")
            return from_openai_response(r.json(), [t["name"] for t in tools])


ENV_ALLOWED = {"ANALYST_PROVIDER", "ANALYST_MODEL", "LABOPS_AUTH"}


def load_dotenv(path: str | Path = ".env") -> list[str]:
    """Load KEY=value lines from a local .env into os.environ (never overriding what the shell already set).

    Only API-key variables and a few app settings are accepted, so a tampered .env cannot set PATH, PYTHONPATH,
    LD_PRELOAD and the like. Empty values are ignored. Returns the names it set (never the values).
    """
    p = Path(path)
    if not p.is_file():
        return []
    if p.stat().st_mode & 0o077:
        print(f"warning: {p} is readable by other users; run: chmod 600 {p}", file=sys.stderr)
    loaded = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if not v or k in os.environ or not (k.endswith("_API_KEY") or k in ENV_ALLOWED) or not k.replace("_", "").isalnum():
            continue
        os.environ[k] = v
        loaded.append(k)
    return loaded


def looks_like_placeholder(key: str) -> bool:
    k = key.strip().lower()
    return len(k) < 20 or any(w in k for w in ("paste", "your-", "your_", "<", "here", "xxxx", "changeme", "example", "placeholder"))


def make_client(provider: str = "anthropic", model: str | None = None, base_url: str | None = None):
    """Return (client, model). provider is 'anthropic', a PRESETS name, or 'openai' with --base-url."""
    load_dotenv()
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
        raise SystemExit(f"{key_env} is not set for provider '{provider}'. Put it in a .env file in the project folder (copy .env.example to .env) "
                         f"or run: export {key_env}=\"<your key>\" in this same terminal. Free keys: see the README.")
    if key and looks_like_placeholder(key):
        raise SystemExit(f"{key_env} is set to '{key[:24]}', which is placeholder text, not a real key. Copy the actual key from your provider's console "
                         f"and run: export {key_env}=\"<the real key>\"")
    chosen = model or (p.model if p else None)
    return OpenAICompatClient(url, key, auto_model=not model and bool(chosen)), chosen or _need_model()


def _need_model():
    raise SystemExit("--model is required for a custom --base-url")


def add_cli_args(ap) -> None:
    load_dotenv()                      # so ANALYST_PROVIDER / ANALYST_MODEL in .env become the defaults below
    ap.add_argument("--provider", default=os.environ.get("ANALYST_PROVIDER", "anthropic"),
                    help="anthropic | ollama | groq | gemini | openrouter | openai (with --base-url)")
    ap.add_argument("--model", default=os.environ.get("ANALYST_MODEL"))
    ap.add_argument("--base-url")


NON_CHAT = ("whisper", "tts", "guard", "embed", "orpheus", "playai", "transcri", "moderation")


def list_models(provider: str, base_url: str | None = None, show_all: bool = False, transport=None) -> list[str]:
    """Ask an OpenAI-compatible provider which model ids this key can use (prints nothing secret)."""
    load_dotenv()
    p = PRESETS.get(provider)
    if provider == "anthropic" or (p is None and not base_url):
        raise SystemExit("Listing models works for OpenAI-compatible providers: " + ", ".join(PRESETS) + ", or openai with --base-url.")
    url = base_url or p.base_url
    key_env = p.key_env if p else "OPENAI_API_KEY"
    key = os.environ.get(key_env) if key_env else None
    if key_env and not key:
        raise SystemExit(f"{key_env} is not set. Put it in your .env file (see .env.example).")
    if key and looks_like_placeholder(key):
        raise SystemExit(f"{key_env} holds placeholder text, not a real key.")
    with httpx.Client(base_url=url.rstrip("/"), headers={"Authorization": f"Bearer {key}"} if key else {}, timeout=30, transport=transport) as http:
        r = http.get("/models")
    if r.status_code >= 400:
        raise SystemExit(f"{r.status_code} from {url}: {r.text[:200]}")
    ids = sorted(m["id"] for m in r.json().get("data", []) if isinstance(m, dict) and "id" in m)
    return ids if show_all else [i for i in ids if not any(w in i.lower() for w in NON_CHAT)]


def main(argv=None) -> None:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m analyst.llm", description="Model helpers.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("models", help="list the chat models your API key can use")
    m.add_argument("--provider", default="groq")
    m.add_argument("--base-url")
    m.add_argument("--all", action="store_true", help="include speech / guard / embedding models")
    a = ap.parse_args(argv)
    ids = list_models(a.provider, a.base_url, a.all)
    print(f"{len(ids)} model(s) available to your key on '{a.provider}':")
    print("\n".join("  " + i for i in ids))
    print("\nUse one with:  --model NAME   (or ANALYST_MODEL=NAME in .env). Pick one that supports tool calling; large instruct models work best.")


if __name__ == "__main__":
    main()
