"""Entry point. Parses args, builds the client, and starts the REPL."""

# ── Self-update block (runs before anything else) ──────────────────────────
import subprocess, sys, os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
_CHANNEL_REF = "origin/main"
_CHANNEL_FILE = "channel.txt"


def _git(*args: str, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(_REPO_ROOT), *args],
        capture_output=True, text=True, check=check
    )


def gitInit():

    if os.environ.get("CD_AI_LAUNCHED"):
        return

    try:

        print("[cd-ai] Fetching latest...")
        _git("fetch", "origin", "--quiet")

        # Read target branch from channel.txt on origin/main
        target_branch = _git("show", f"{_CHANNEL_REF}:{_CHANNEL_FILE}").stdout.strip()
        if not target_branch:
            raise SystemExit("[cd-ai] channel.txt is empty. Ask the owner for the correct branch.")

        remote_ref = f"origin/{target_branch}"

        # Check if update is needed
        remote_sha = _git("rev-parse", remote_ref).stdout.strip()
        local_sha = _git("rev-parse", "HEAD").stdout.strip()
        needs_update = remote_sha != local_sha

        if needs_update:
            print(f"[cd-ai] Switching to branch '{target_branch}'...")
            _git("reset", "--hard", remote_ref)

            # Auto-install requirements if requirements.txt exists
            req_file = _REPO_ROOT / "requirements.txt"
            if req_file.exists():
                print("[cd-ai] Installing dependencies...")
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-q", "-r", str(req_file)],
                    check=True
                )

        # Re-exec with real main.py
        os.environ["CD_AI_LAUNCHED"] = "1"
        print("[cd-ai] Launching...\n")
        os.execv(sys.executable, [sys.executable] + sys.argv)


    except subprocess.CalledProcessError as e:
        raise SystemExit(f"[cd-ai] Git error: {e.stderr.strip() or e}")



gitInit()
# ── End self-update block ───────────────────────────────────────────────────


from dotenv import load_dotenv
load_dotenv()  # MUST run before importing config so .env values are picked up

import argparse
import config
from coder.cli import repl

_MODEL_FOR = {
    "ollama":      config.OLLAMA_MODEL,
    "groq":        config.GROQ_MODEL,
    "openrouter":  config.OPENROUTER_MODEL,
    "gemini":      config.GEMINI_MODEL,
    "cerebras":    config.CEREBRAS_MODEL,
    "github":      config.GITHUB_MODEL,
    "vllm":        config.VLLM_MODEL,
}

def main() -> None:
    parser = argparse.ArgumentParser(description="A CLI coding agent.")
    parser.add_argument("--repo",     default=".",            help="Repo root (default: current dir).")
    parser.add_argument("--provider", default=config.PROVIDER, help="ollama, groq, openrouter, gemini, cerebras, or github.")
    parser.add_argument("--model",    default=None,           help="Override the model name.")
    parser.add_argument("--check",    action="store_true",    help="Run preflight checks and exit.")
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