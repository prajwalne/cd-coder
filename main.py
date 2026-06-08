"""Launcher. Checks out the target branch from channel.txt and re-execs."""
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


def main():
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
        print("[cd-ai] Launching...\n")
        os.execv(sys.executable, [sys.executable] + sys.argv)

    except subprocess.CalledProcessError as e:
        raise SystemExit(f"[cd-ai] Git error: {e.stderr.strip() or e}")


if __name__ == "__main__":
    main()