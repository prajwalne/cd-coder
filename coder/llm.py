"""Model backends behind one small interface.

The agent loop only needs three things from a backend: the model's text, the
tool calls it wants, and a message it can append back to history. Both Ollama
and Groq are normalized to that shape here, so switching providers is a config
change, not a code change.
"""

import json
import re
import time


class BackendError(Exception):
    """The model backend was unreachable after retries (transport/5xx, not a code bug)."""


_TRANSIENT_HINTS = (
    "unexpected eof", "xml syntax", "connection", "timed out", "timeout",
    "temporarily", "bad gateway", "gateway", "reset by peer", "max retries",
    "connection refused", "502", "503", "504", "500", "remotedisconnected",
)


def _is_transient(err: Exception) -> bool:
    return any(h in str(err).lower() for h in _TRANSIENT_HINTS)


def _rate_limit_kind(err: Exception):
    """Return 'too_big' (one request exceeds the limit; retrying is futile),
    'pace' (limit hit across requests; wait and retry), or None."""
    s = str(err).lower()
    if "rate_limit" not in s and "413" not in s and "429" not in s and "too large" not in s:
        return None
    import re as _re
    m = _re.search(r"limit\s+(\d+).*?requested\s+(\d+)", s)
    if m and int(m.group(2)) > int(m.group(1)):
        return "too_big"
    if "too large" in s or "413" in s or "reduce your message" in s:
        return "too_big"
    return "pace"


def _call_with_retries(fn, label="backend", attempts=3, base_delay=1.0):
    last = None
    for i in range(attempts):
        try:
            return fn()
        except BackendError:
            raise
        except Exception as e:
            last = e
            if i < attempts - 1 and _is_transient(e):
                wait = base_delay * (2 ** i)
                print(f"\033[33m  \u21bb {label} hiccup ({str(e)[:70]}); retry {i+1}/{attempts-1} in {wait:.0f}s\033[0m")
                time.sleep(wait)
                continue
            break
    raise BackendError(
        f"{label} unreachable: {last}. If you're using a trycloudflare tunnel it may have "
        f"dropped \u2014 check the tunnel/Ollama, or set PROVIDER=groq.") from last


class ToolCall:
    """A normalized tool-call request, provider-agnostic."""
    def __init__(self, id, name, args: dict):
        self.id = id          # Groq needs this; Ollama leaves it None
        self.name = name
        self.args = args


class Reply:
    """A normalized model response."""
    def __init__(self, text, tool_calls, assistant_msg):
        self.text = text  # str | None
        self.tool_calls = tool_calls  # list[ToolCall]
        self.assistant_msg = assistant_msg  # message to append to history
        self.finish_reason = None  # why the model stopped (Groq) # message to append to history


class OllamaClient:
    def __init__(self, model: str, num_ctx: int = 32768, host: str = None):
        from ollama import Client
        # host=None -> default localhost:11434; pass a URL for a remote Ollama
        self._client = Client(host=host) if host else Client()
        self.model = model
        self.num_ctx = num_ctx

    def chat(self, messages, tools) -> Reply:
        resp = _call_with_retries(
            lambda: self._client.chat(
                model=self.model, messages=messages, tools=tools,
                options={"num_ctx": self.num_ctx}),
            label="Ollama")
        msg = resp.message
        calls = [
            ToolCall(None, c.function.name, dict(c.function.arguments))
            for c in (msg.tool_calls or [])
        ]
        text_out = msg.content
        if not calls and msg.content:  # model typed the call as text
            recovered = _extract_tool_calls_from_text(msg.content)
            if recovered:
                calls = recovered
                text_out = None              # don't print the raw JSON
                msg = {"role": "assistant", "content": ""}  # keep raw JSON out of history
        reply = Reply(text_out, calls, msg)
        reply.finish_reason = getattr(resp, "done_reason", None)
        return reply

    def tool_message(self, call: ToolCall, output: str) -> dict:
        return {"role": "tool", "tool_name": call.name, "content": output}


class GroqClient:
    def __init__(self, model: str):
        from groq import Groq
        self.client = Groq()  # reads GROQ_API_KEY from the environment
        self.model = model

    def chat(self, messages, tools) -> Reply:
        # Hosted open models occasionally emit a malformed tool call that Groq
        # rejects (tool_use_failed / output_parse_failed). Sampling varies per
        # request, so retrying usually yields a valid call.
        last_err = None
        for attempt in range(3):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model, messages=messages, tools=tools,
                    max_completion_tokens=4096,
                )
                break
            except Exception as e:
                err = str(e)
                kind = _rate_limit_kind(e)
                if kind == "too_big":
                    raise BackendError(
                        "request too large for this model's per-minute token limit. "
                        "The repo map / context is too big, or the model's TPM is too low. "
                        "Shrink context or use a higher-TPM model. Original: " + err[:160]) from e
                if kind == "pace" and attempt < 2:
                    print("\033[33m  \u21bb rate limit hit; waiting 20s\u2026\033[0m")
                    time.sleep(20)
                    last_err = e
                    continue
                malformed = "tool_use_failed" in err or "output_parse_failed" in err
                if (malformed or _is_transient(e)) and attempt < 2:
                    if _is_transient(e):
                        print(f"\033[33m  \u21bb Groq hiccup ({err[:70]}); retrying\u2026\033[0m")
                        time.sleep(1.0 * (2 ** attempt))
                    else:
                        print("\033[2m  \u21bb retrying (model emitted a malformed tool call)\033[0m")
                    last_err = e
                    continue
                if _is_transient(e):
                    raise BackendError(f"Groq unreachable: {e}") from e
                raise
        else:
            raise last_err
        choice = resp.choices[0]
        msg = choice.message
        calls = [
            ToolCall(tc.id, tc.function.name, json.loads(tc.function.arguments or "{}"))
            for tc in (msg.tool_calls or [])
        ]
        assistant = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            assistant["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ]
        reply = Reply(msg.content, calls, assistant)
        reply.finish_reason = choice.finish_reason
        return reply

    def tool_message(self, call: ToolCall, output: str) -> dict:
        return {"role": "tool", "tool_call_id": call.id,
                "name": call.name, "content": output}


class OpenAICompatClient:
    """OpenAI-compatible endpoint (OpenRouter / Together / Fireworks / DeepInfra)
    over Python's stdlib urllib — NO extra package to install. Default config
    targets OpenRouter's free Qwen3-Coder."""

    def __init__(self, model: str, base_url: str, api_key_env: str):
        import os
        self.key = os.getenv(api_key_env)
        if not self.key:
            raise BackendError(f"{api_key_env} is not set.")
        self.model = model
        self.url = base_url.rstrip("/") + "/chat/completions"

    def _post(self, body: dict) -> dict:
        import urllib.request
        import urllib.error
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.url, data=data, method="POST",
            headers={"Authorization": f"Bearer {self.key}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body_txt = e.read().decode("utf-8", "replace")
            raise Exception(f"HTTP {e.code}: {body_txt}") from e

    def chat(self, messages, tools) -> Reply:
        body = {"model": self.model, "messages": messages,
                "tools": tools, "max_tokens": 4096}
        last_err = None
        for attempt in range(3):
            try:
                resp = self._post(body)
                break
            except Exception as e:
                kind = _rate_limit_kind(e)
                if kind == "too_big":
                    raise BackendError("request too large for this model/tier. Shrink context. "
                                       + str(e)[:160]) from e
                if (kind == "pace" or _is_transient(e)) and attempt < 2:
                    print("\033[33m  \u21bb backend hiccup/limit; waiting 20s\u2026\033[0m")
                    time.sleep(20)
                    last_err = e
                    continue
                raise
        else:
            raise last_err
        choice = resp["choices"][0]
        msg = choice.get("message", {})
        tcs = msg.get("tool_calls") or []
        calls = [ToolCall(tc["id"], tc["function"]["name"],
                          json.loads(tc["function"].get("arguments") or "{}")) for tc in tcs]
        assistant = {"role": "assistant", "content": msg.get("content") or ""}
        if tcs:
            assistant["tool_calls"] = [
                {"id": tc["id"], "type": "function",
                 "function": {"name": tc["function"]["name"],
                              "arguments": tc["function"].get("arguments") or "{}"}}
                for tc in tcs]
        reply = Reply(msg.get("content"), calls, assistant)
        reply.finish_reason = choice.get("finish_reason")
        return reply

    def tool_message(self, call: ToolCall, output: str) -> dict:
        return {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": output}


def make_client(provider: str, model: str, num_ctx: int = 32768, ollama_host: str = None):
    provider = provider.lower()
    if provider == "ollama":
        return OllamaClient(model, num_ctx=num_ctx, host=ollama_host)
    if provider == "groq":
        return GroqClient(model)
    if provider == "openrouter":
        return OpenAICompatClient(model, "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY")
    if provider == "gemini":
        return OpenAICompatClient(
            model, "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY")
    if provider == "cerebras":
        return OpenAICompatClient(model, "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY")
    if provider == "github":
        return OpenAICompatClient(model, "https://models.github.ai/inference", "GITHUB_TOKEN")
    raise ValueError(f"Unknown provider '{provider}'. Use ollama, groq, openrouter, "
                     f"gemini, cerebras, or github.")

def _sanitize_jsonish(text: str) -> str:
    """Repair JS/Java-isms small models emit when typing a tool call as text."""
    text = re.sub(r"```[a-zA-Z]*", "", text).replace("```", "")  # code fences
    text = re.sub(r'"\s*\+\s*"', "", text)        # join  "a" + "b" -> "ab"
    text = re.sub(r",(\s*[}\]])", r"\1", text)      # trailing commas
    return text


def _extract_tool_calls_from_text(text):
    """Recover tool calls a model emitted as plain text instead of a structured
    call, e.g. {"name": "edit_file", "arguments": {...}}. Tolerates code fences
    and JS-style string concatenation in argument values."""
    if not text or "{" not in text:
        return []
    text = _sanitize_jsonish(text)
    dec = json.JSONDecoder()
    found, i = [], 0
    while i < len(text):
        j = text.find("{", i)
        if j == -1:
            break
        try:
            obj, end = dec.raw_decode(text, j)
        except json.JSONDecodeError:
            i = j + 1
            continue
        i = end
        if isinstance(obj, dict) and "name" in obj and "arguments" in obj:
            args = obj["arguments"]
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            found.append(ToolCall(None, obj["name"], args if isinstance(args, dict) else {}))
    return found