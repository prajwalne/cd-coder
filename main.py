"""Entry point. Parses args, builds the client, and starts the REPL."""

from dotenv import load_dotenv
load_dotenv()  # MUST run before importing config so .env values are picked up

import argparse
from pathlib import Path

import config
from coder.cli import repl

# Default model per provider (overridable with --model or the *_MODEL env vars).
_MODEL_FOR = {
    "ollama": config.OLLAMA_MODEL,
    "groq": config.GROQ_MODEL,
    "openrouter": config.OPENROUTER_MODEL,
    "gemini": config.GEMINI_MODEL,
    "cerebras": config.CEREBRAS_MODEL,
    "github": config.GITHUB_MODEL,
    "vllm": config.VLLM_MODEL,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="A CLI coding agent.")
    parser.add_argument("--repo", default=".", help="Repo root (default: current dir).")
    parser.add_argument("--provider", default=config.PROVIDER,
                        help="ollama, groq, openrouter, gemini, cerebras, or github.")
    parser.add_argument("--model", default=None, help="Override the model name.")
    parser.add_argument("--check", action="store_true", help="Run preflight checks and exit.")
    args = parser.parse_args()

    if args.check:
        from doctor import doctor
        raise SystemExit(0 if doctor() else 1)

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        raise SystemExit(f"{repo} is not a directory.")

    provider = args.provider
    model = args.model or _MODEL_FOR.get(provider, config.OLLAMA_MODEL)
    repl(repo, provider, model)


if __name__ == "__main__":
    main()