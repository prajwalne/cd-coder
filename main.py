"""Entry point. Parses args, builds the client, and starts the REPL."""

# ── Self-update block (runs before anything else) ──────────────────────────
import subprocess, sys, os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent   # where main.py lives
_CHANNEL_REF = "origin/main"                   # branch that holds channel.txt
_CHANNEL_FILE = "channel.txt"                  # file inside that branch

def _git(*args: str, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(_REPO_ROOT), *args],
        capture_output=True, text=True, check=check
    )

def _self_update() -> None:
    """Fetch latest code from the channel branch; re-exec if anything changed."""
    # Skip if we're running inside a venv build / CI / explicit opt-out
    if os.environ.get("CD_AI_NO_UPDATE"):
        return

    try:
        # 1. Fetch quietly
        _git("fetch", "origin", "--quiet")

        # 2. Read target branch name from the fixed file on origin/main
        result = _git("show", f"{_CHANNEL_REF}:{_CHANNEL_FILE}")
        target_branch = result.stdout.strip()
        if not target_branch:
            return  # channel file empty — do nothing

        # 3. Find out what commit origin/<target_branch> is at
        remote_ref = f"origin/{target_branch}"
        remote_sha = _git("rev-parse", remote_ref).stdout.strip()
        local_sha  = _git("rev-parse", "HEAD").stdout.strip()

        if remote_sha == local_sha:
            return  # already up to date

        # 4. Check out the target branch and hard-reset to origin
        print(f"[cd-ai] Updating to branch '{target_branch}' ({remote_sha[:7]})…")
        _git("checkout", target_branch, check=False)   # may already be on it
        _git("reset", "--hard", remote_ref)

        # 5. Re-exec so the updated code actually runs
        print("[cd-ai] Restarting with updated code…\n")
        os.execv(sys.executable, [sys.executable] + sys.argv)

    except subprocess.CalledProcessError as e:
        # Never crash the agent over an update failure — just warn and continue
        print(f"[cd-ai] Auto-update skipped: {e.stderr.strip() or e}", file=sys.stderr)

_self_update()
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