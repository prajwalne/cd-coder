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
    'pace' (limit hit across requests; wait and retry), or None.

    Only fires on genuine rate signals \u2014 a rate phrase, or 429/413 as an
    actual HTTP/status code \u2014 NOT on a stray '429'/'413' that happens to
    appear in a token count, id, or byte size (the old substring check did, which
    mislabeled unrelated errors like 503s as rate limits)."""
    s = str(err).lower()
    rate_words = ("rate_limit", "rate limit", "too many requests", "resource_exhausted",
                  "quota exceeded", "quota", "too large", "reduce your message")
    status = re.search(r"(?:http|code|status)\D{0,8}(429|413)\b", s)
    if not any(w in s for w in rate_words) and not status:
        return None
    m = re.search(r"limit\s+(\d+).*?requested\s+(\d+)", s)
    if m and int(m.group(2)) > int(m.group(1)):
        return "too_big"
    if "too large" in s or "reduce your message" in s or (status and status.group(1) == "413"):
        return "too_big"
    return "pace"


def _clean_err(err) -> str:
    """A readable one-liner from a provider error: JSON 'message' + status code
    if present, else a trimmed string. Replaces mid-JSON truncation so you can
    actually see what failed (a 503 overload vs a real 429, etc.)."""
    s = str(err)
    mcode = re.search(r"http (\d{3})|\bcode[\"']?\s*[:=]\s*(\d{3})", s.lower())
    code = (mcode.group(1) or mcode.group(2)) if mcode else None
    mmsg = re.search(r'"message"\s*:\s*"([^"]+)"', s)
    msg = (mmsg.group(1) if mmsg else s).strip()
    if len(msg) > 240:
        msg = msg[:240] + "\u2026"
    return f"{code + ': ' if code else ''}{msg}"


def _retry_after_seconds(err):
    """Pull the provider's stated wait from the error (Retry-After header or a
    'try again in Xs' / 'retryDelay: Ns' in the body). None if not present."""
    s = str(err).lower()
    m = re.search(r"try again in\s+(?:(\d+)\s*m)?\s*([\d.]+)\s*s", s)
    if m:
        return (int(m.group(1)) if m.group(1) else 0) * 60 + float(m.group(2))
    m = re.search(r"retry[\s_-]*after[\"']?\s*[:=]?\s*[\"']?\s*(\d+(?:\.\d+)?)", s)
    if m:
        return float(m.group(1))
    m = re.search(r"retrydelay[\"']?\s*[:=]\s*[\"']?\s*(\d+(?:\.\d+)?)\s*s", s)
    if m:
        return float(m.group(1))
    m = re.search(r"in\s+([\d.]+)\s*seconds", s)
    if m:
        return float(m.group(1))
    return None


def _switch_hint():
    return ("    don't want to wait? switch model/provider:  /provider <name>   or   "
            "/model <id>   (run them with no argument to list options)")


def _pace_notice(provider, model, wait):
    return (f"\033[33m  \u26a0 rate limit on {provider}/{model}. retrying automatically in "
            f"{wait:.0f}s.\033[0m\n\033[2m{_switch_hint()}\033[0m")


def _too_big_msg(provider, model):
    return (f"request too large for {provider}/{model}'s per-minute limit \u2014 waiting won't "
            f"help. Shrink the context, or switch: /provider <name> / /model <id>.")


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
                print(f"\033[33m  \u21bb {label} hiccup: {_clean_err(e)} \u2014 retry {i+1}/{attempts-1} in {wait:.0f}s\033[0m")
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
                options={"num_ctx": self.num_ctx}
            ),
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
                    raise BackendError(_too_big_msg(getattr(self, "provider", "?"), self.model)
                                       + " (" + _clean_err(e) + ")") from e
                if kind == "pace" and attempt < 2:
                    wait = _retry_after_seconds(e) or 20
                    print(_pace_notice(getattr(self, "provider", "?"), self.model, wait))
                    time.sleep(wait)
                    last_err = e
                    continue
                malformed = "tool_use_failed" in err or "output_parse_failed" in err
                if (malformed or _is_transient(e)) and attempt < 2:
                    if _is_transient(e):
                        print(f"\033[33m  \u21bb Groq hiccup: {_clean_err(e)}; retrying\u2026\033[0m")
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
            ra = e.headers.get("Retry-After") or e.headers.get("retry-after") or ""
            extra = f" retry-after: {ra}" if ra else ""
            raise Exception(f"HTTP {e.code}: {body_txt}{extra}") from e

    def chat(self, messages, tools) -> Reply:
        body = {"model": self.model, "messages": messages,
                "tools": tools, "max_tokens": 4096}
        last_err = None
        print("in chat")
        for attempt in range(3):
            try:
                resp = self._post(body)
                break
            except Exception as e:
                kind = _rate_limit_kind(e)
                if kind == "too_big":
                    raise BackendError(_too_big_msg(getattr(self, "provider", "?"), self.model)
                                       + " (" + _clean_err(e) + ")") from e
                if kind == "pace" and attempt < 2:
                    wait = _retry_after_seconds(e) or 20
                    print(_pace_notice(getattr(self, "provider", "?"), self.model, wait))
                    time.sleep(wait)
                    last_err = e
                    continue
                if _is_transient(e) and attempt < 2:
                    print(f"\033[33m  \u21bb backend hiccup: {_clean_err(e)}; retrying\u2026\033[0m")
                    time.sleep(2 ** attempt)
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
        c = OllamaClient(model, num_ctx=num_ctx, host=ollama_host)
    elif provider == "groq":
        c = GroqClient(model)
    elif provider == "openrouter":
        c = OpenAICompatClient(model, "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY")
    elif provider == "gemini":
        c = OpenAICompatClient(
            model, "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY")
    elif provider == "cerebras":
        c = OpenAICompatClient(model, "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY")
    elif provider == "github":
        c = OpenAICompatClient(model, "https://models.github.ai/inference", "GITHUB_TOKEN")
    else:
        raise ValueError(f"Unknown provider '{provider}'. Use ollama, groq, openrouter, "
                         f"gemini, cerebras, or github.")
    c.provider = provider
    return c

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