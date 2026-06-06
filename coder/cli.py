"""The REPL: read your input, run a turn, repeat. Holds the conversation and
lets you switch provider/model live with /provider and /model (persisted to
.env, effective on the next message)."""

import os
from pathlib import Path

import config
from coder import agent, tools
from coder.context import build_system_prompt
from coder.llm import BackendError, make_client

_BOLD, _DIM, _BLUE, _RED, _GRN, _RESET = (
    "\033[1m", "\033[2m", "\033[34m", "\033[31m", "\033[32m", "\033[0m")


def _build(provider, model):
    return make_client(provider, model, num_ctx=config.NUM_CTX, ollama_host=config.OLLAMA_HOST)


def _list_providers(current):
    print(f"{_BOLD}providers{_RESET} (current: {current}):")
    for p in config.PROVIDERS:
        keyenv = config.PROVIDER_KEY_ENV.get(p)
        ok = (keyenv is None) or bool(os.getenv(keyenv))
        mark = "*" if p == current else " "
        status = "" if ok else f" {_DIM}(set {keyenv}){_RESET}"
        print(f"  {mark} {p}{status}")
    print(f"{_DIM}  switch: /provider <name>{_RESET}")


def _list_models(provider, current):
    print(f"{_BOLD}models for {provider}{_RESET} (current: {current}):")
    for mid, note in config.MODEL_CATALOG.get(provider, []):
        mark = "*" if mid == current else " "
        print(f"  {mark} {mid}  {_DIM}— {note}{_RESET}")
    print(f"{_DIM}  switch: /model <id>   (any id works, not just these){_RESET}")


def repl(repo: Path, provider: str, model: str) -> None:
    client = _build(provider, model)
    system = build_system_prompt(repo)
    messages = [{"role": "system", "content": system}]

    print(f"{_BOLD}cd-coder{_RESET} on {_BLUE}{repo}{_RESET} ({provider}/{client.model})")
    print(f"{_DIM}/exit  /reset  /provider [name]  /model [id]{_RESET}")

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

        # ---- /provider [name] ----
        if user.split()[0] == "/provider":
            parts = user.split(maxsplit=1)
            if len(parts) == 1:
                _list_providers(provider)
                continue
            name = parts[1].strip().lower()
            if name not in config.PROVIDERS:
                print(f"{_RED}unknown provider '{name}'.{_RESET}")
                _list_providers(provider)
                continue
            keyenv = config.PROVIDER_KEY_ENV.get(name)
            if keyenv and not os.getenv(keyenv):
                print(f"{_RED}{keyenv} is not set in .env — add it before switching to {name}.{_RESET}")
                continue
            new_model = config.model_for(name)
            try:
                client = _build(name, new_model)
            except BackendError as e:
                print(f"{_RED}can't switch: {e}{_RESET}")
                continue
            provider, model = name, new_model
            config.set_env_var("PROVIDER", provider)
            # switching providers changes the tool-call message format, so start clean
            tools.reset_read_tracking()
            messages = [{"role": "system", "content": system}]
            print(f"{_GRN}→ now using {provider}/{model}{_RESET} {_DIM}(context cleared){_RESET}")
            continue

        # ---- /model [id] ----
        if user.split()[0] == "/model":
            parts = user.split(maxsplit=1)
            if len(parts) == 1:
                _list_models(provider, model)
                continue
            new_model = parts[1].strip()
            try:
                client = _build(provider, new_model)
            except BackendError as e:
                print(f"{_RED}can't switch: {e}{_RESET}")
                continue
            model = new_model
            config.set_env_var(config.model_env_var(provider), model)
            print(f"{_GRN}→ now using {provider}/{model}{_RESET}")
            continue

        messages.append({"role": "user", "content": user})
        try:
            agent.run_turn(client, messages, repo)
        except BackendError as e:
            print(f"{_RED}backend error:{_RESET} {e}")
        except Exception as e:
            print(f"{_RED}error: {e}{_RESET}")