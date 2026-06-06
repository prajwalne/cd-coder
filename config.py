"""Central config. Reads from environment / .env, with sensible defaults so a
fresh checkout runs against Groq once GROQ_API_KEY is set."""

import os

# Default to the hosted backend so a new user doesn't need a local model.
PROVIDER = os.getenv("PROVIDER", "groq")          # "groq" or "ollama"

GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "qwen/qwen3-coder:free")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
CEREBRAS_MODEL = os.getenv("CEREBRAS_MODEL", "llama-3.3-70b")
GITHUB_MODEL = os.getenv("GITHUB_MODEL", "gpt-4o")

NUM_CTX = int(os.getenv("NUM_CTX", "32768"))      # context window (Ollama only)
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")


def active_model() -> str:
    return {"groq": GROQ_MODEL, "openrouter": OPENROUTER_MODEL, "gemini": GEMINI_MODEL,
            "cerebras": CEREBRAS_MODEL, "github": GITHUB_MODEL}.get(PROVIDER, OLLAMA_MODEL)