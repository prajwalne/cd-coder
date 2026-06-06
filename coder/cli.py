"""The REPL: read your input, run a turn, repeat. Holds the conversation."""

from pathlib import Path

from coder import agent, tools
from coder.context import build_system_prompt
from coder.llm import BackendError

_BOLD, _DIM, _BLUE, _RED, _RESET = "\033[1m", "\033[2m", "\033[34m", "\033[31m", "\033[0m"


def repl(client, repo: Path) -> None:
    system = build_system_prompt(repo)
    messages = [{"role": "system", "content": system}]

    print(f"{_BOLD}ollama-coder{_RESET} on {_BLUE}{repo}{_RESET} ({client.model})")
    print(f"{_DIM}/exit to quit, /reset to clear context{_RESET}")

    while True:
        try:
            user = input(f"\n{_BOLD}you ›{_RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if not user:
            continue
        if user == "/exit":
            return
        if user == "/reset":
            tools.reset_read_tracking()
            messages = [{"role": "system", "content": system}]
            print(f"{_DIM}context cleared.{_RESET}")
            continue

        messages.append({"role": "user", "content": user})
        try:
            agent.run_turn(client, messages, repo)
        except BackendError as e:
            print(f"{_RED}backend error:{_RESET} {e}")
            print(f"{_DIM}the backend dropped; just send your request again.{_RESET}")
        except Exception as e:
            print(f"{_RED}error: {e}{_RESET}")