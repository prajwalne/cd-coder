"""Central config. Reads from environment / .env, with sensible defaults so a
fresh checkout runs against Groq once GROQ_API_KEY is set."""

import os

# Default to the hosted backend so a new user doesn't need a local model.
PROVIDER = os.getenv("PROVIDER", "groq")          # "groq" or "ollama"

GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "qwen/qwen3-coder:free")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
CEREBRAS_MODEL = os.getenv("CEREBRAS_MODEL", "llama-3.3-70b")
GITHUB_MODEL = os.getenv("GITHUB_MODEL", "gpt-4o")

NUM_CTX = int(os.getenv("NUM_CTX", "32768"))      # context window (Ollama only)
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")


def active_model() -> str:
    return {"groq": GROQ_MODEL, "openrouter": OPENROUTER_MODEL, "gemini": GEMINI_MODEL,
            "cerebras": CEREBRAS_MODEL, "github": GITHUB_MODEL,
            "vllm": VLLM_MODEL}.get(PROVIDER, OLLAMA_MODEL)


# ---- Provider / model catalog (edit + ship via git; this is the source of the
# /provider and /model command lists). The user's CURRENT pick is persisted in
# .env, not here, so updates never clobber it. ----

PROVIDERS = ["ollama", "groq", "openrouter", "gemini", "vllm"]

# Which env var holds each provider's key (None = no key needed). Used to refuse
# a switch into a provider whose key isn't set yet.
PROVIDER_KEY_ENV = {
    "ollama": None,
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "github": "GITHUB_TOKEN",
    "vllm": None,   # no key needed
}

# Suggested models per provider (the /model list). Free to extend.
MODEL_CATALOG = {
    "gemini": [
        ("gemini-2.5-flash", "reliable tools, good coder"),
        ("gemma-4-26b-a4b-it", "fast MoE coder; test tool format"),
        ("gemini-2.5-flash-lite", "highest free RPD, weaker"),
        ("gemini-2.5-pro", "strongest, but ~50-100/day"),
    ],
    "groq": [
        ("qwen/qwen3-32b", "reliable tool calls"),
        ("llama-3.3-70b-versatile", "strong general"),
        ("llama-3.1-8b-instant", "fast, weak; high volume"),
    ],
    "openrouter": [("qwen/qwen3-coder:free", "real coder; 50-1000/day")],
    "cerebras": [("llama-3.3-70b", "fast; 8k context cap")],
    "github": [("gpt-4o", "GPT-4o quality; ~150/day")],
    "ollama": [("qwen3-coder", "local, unlimited, slow"),
               ("qwen2.5-coder:7b", "local, smaller")],
   "vllm": [
        ("Qwen/Qwen3-Coder-30B-A3B-Instruct", "30B MoE, 2x T4 Kaggle"),
        ("Qwen/Qwen3-8B", "single T4"),
    ],
}


def model_env_var(provider: str) -> str:
    """The .env variable holding the model for a provider, e.g. GEMINI_MODEL."""
    return f"{provider.upper()}_MODEL"


def model_for(provider: str) -> str:
    """Current model for a provider: its *_MODEL env value, else catalog default."""
    env = os.getenv(model_env_var(provider))
    if env:
        return env
    cat = MODEL_CATALOG.get(provider)
    return cat[0][0] if cat else OLLAMA_MODEL


def _env_path():
    """Locate the .env we should persist into (same one dotenv loaded)."""
    from pathlib import Path
    try:
        from dotenv import find_dotenv
        found = find_dotenv(usecwd=True)
        if found:
            return Path(found)
    except Exception:
        pass
    return Path(".env")


def set_env_var(key: str, value: str) -> None:
    """Persist KEY=value in .env (dedupes existing uncommented lines) and update
    the live process env so the change is effective immediately."""
    import re
    from pathlib import Path
    path = _env_path()
    lines = path.read_text().splitlines() if path.exists() else []
    kept = [ln for ln in lines if not re.match(rf"\s*{re.escape(key)}\s*=", ln)]
    kept.append(f"{key}={value}")
    path.write_text("\n".join(kept) + "\n")
    os.environ[key] = value

VLLM_MODEL = os.getenv("VLLM_MODEL", "Qwen/Qwen3-Coder-30B-A3B-Instruct")
VLLM_HOST = os.getenv("VLLM_HOST", "http://localhost:8000")