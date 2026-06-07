"""The agent loop — the heart of the whole thing.

Reliability is enforced here, not assumed from the model:
  - read-before-edit is checked in the tools (resolved-path aware)
  - the loop breaks out of repeated identical calls AND repeated edit failures
  - after edits, the project is auto-compiled; compiler errors are fed back so
    the model can fix them, and the SAME error repeating twice stops the turn
"""

import json
from pathlib import Path

from coder import tools

MAX_STEPS = 50
REPEAT_LIMIT = 3      # 4th identical call ends the turn
EDIT_FAIL_LIMIT = 3   # 3 failed edits to one file -> force a re-read
COMPILE_REPEAT = 2    # same compiler error this many times -> stop
REQUEST_TOKEN_BUDGET = 4500  # max input tokens per request (Groq free 8b TPM=6000;
                             # raise for local Ollama or a higher-TPM model)
KEEP_RECENT = 6          # most recent messages always kept in full
MAX_CONTINUE = 2         # how many times we nudge a truncated reply to continue


import config as _config
_BUDGETS = {"groq": 4500, "ollama": 24000, "openrouter": 12000,
            "gemini": 16000, "cerebras": 6000, "github": 16000}
REQUEST_TOKEN_BUDGET = _BUDGETS.get(_config.PROVIDER, 8000)

def _msg_field(m, key):
    return m.get(key) if isinstance(m, dict) else getattr(m, key, None)


def _est_tokens(messages) -> int:
    return sum(len(str(_msg_field(m, "content") or "")) for m in messages) // 4


def _fit_context(messages, max_tokens=REQUEST_TOKEN_BUDGET) -> None:
    """Guarantee the outgoing request fits under a token budget — the hard guard
    against a 413 / TPM overflow. Steps, least to most destructive:
      1. stub old tool results, 2. truncate any oversized content,
      3. drop oldest non-system messages until it fits."""
    cutoff = len(messages) - KEEP_RECENT
    for m in messages[:max(0, cutoff)]:
        if isinstance(m, dict) and m.get("role") == "tool":
            c = m.get("content") or ""
            if len(c) > 400 and not c.startswith("[elided"):
                m["content"] = f"[elided earlier tool output ({len(c)} chars) — re-read if needed]"
    if _est_tokens(messages) <= max_tokens:
        return
    for m in messages[1:]:  # keep the system prompt intact
        if isinstance(m, dict):
            c = m.get("content") or ""
            if len(c) > 1600:
                m["content"] = c[:1500] + f"\n[...trimmed {len(c) - 1500} chars; re-read if needed...]"
    # last resort: drop oldest history (keep system + recent) until under budget
    while _est_tokens(messages) > max_tokens and len(messages) > KEEP_RECENT + 1:
        del messages[1]


# backward-compatible alias
def _compact(messages) -> None:
    _fit_context(messages)

_GREEN, _DIM, _RED, _YELLOW, _RESET = (
    "\033[32m", "\033[2m", "\033[31m", "\033[33m", "\033[0m")


def _fmt_args(d: dict) -> str:
    parts = []
    for k, v in d.items():
        s = str(v).replace("\n", "\\n")
        parts.append(f"{k}={s[:40] + '…' if len(s) > 40 else s}")
    return ", ".join(parts)


def run_turn(client, messages: list, repo: Path) -> None:
    """Run one user turn to completion. Mutates `messages` in place."""
    seen = {}            # (name, args) -> count
    fail_streak = {}     # path -> consecutive failed edits
    compile_seen = {}    # compiler-error signature -> count
    edited = False       # any successful edit since the last compile?

    continues = 0
    for _ in range(MAX_STEPS):
        _fit_context(messages)
        reply = client.chat(messages, tools.TOOL_SCHEMAS)
        messages.append(reply.assistant_msg)

        if reply.text and reply.text.strip():
            print(f"\n{_GREEN}{reply.text.strip()}{_RESET}")

        # ---- response was cut off (ran out of output room): don't mistake it
        #      for "done"; free context and let the model continue ----
        if not reply.tool_calls and getattr(reply, "finish_reason", None) == "length":
            if continues < MAX_CONTINUE:
                continues += 1
                print(f"{_YELLOW}  ↳ response truncated — compacting context and continuing.{_RESET}")
                _compact(messages)
                messages.append({"role": "user",
                                 "content": "Your previous reply was cut off before you made a "
                                            "tool call. Continue and make the call you intended. "
                                            "For a single method in a large file, use read_symbol "
                                            "instead of reading the whole file."})
                continue
            print(f"\n{_RED}Stopping: the model keeps getting truncated without acting.{_RESET}")
            return

        # ---- model made no tool call: it thinks it's done ----
        if not reply.tool_calls:
            if edited and tools.is_maven(repo):
                ok, out = tools.auto_compile(repo)
                edited = False
                if ok:
                    print(f"{_GREEN}Build OK.{_RESET}")
                    return
                sig = tools.compile_signature(out)
                compile_seen[sig] = compile_seen.get(sig, 0) + 1
                if compile_seen[sig] >= COMPILE_REPEAT:
                    print(f"\n{_RED}Stopping: the same compiler error keeps coming back — "
                          f"the model isn't making progress.{_RESET}")
                    return
                messages.append({
                    "role": "user",
                    "content": ("Compilation failed. Fix these errors, reading the relevant "
                                "files first if needed:\n\n" + out)})
                continue
            if not (reply.text and reply.text.strip()):
                fr = getattr(reply, "finish_reason", None)
                extra = f" [finish_reason={fr}]" if fr else ""
                print(f"{_DIM}(model returned no text and no action){extra}{_RESET}")
            return

        # ---- service every tool call (keeps history valid for the API) ----
        stop_reason = None
        nudge_paths = []
        for call in reply.tool_calls:
            key = f"{call.name}:{json.dumps(call.args, sort_keys=True)}"
            seen[key] = seen.get(key, 0) + 1
            print(f"{_DIM}  → {call.name}({_fmt_args(call.args)}){_RESET}")

            if seen[key] > REPEAT_LIMIT:
                messages.append(client.tool_message(
                    call, "Skipped: this identical call was repeated too many times."))
                stop_reason = call.name
                continue

            result = tools.run_tool(repo, call.name, call.args)
            messages.append(client.tool_message(call, result))

            is_edit = call.name in tools.EDIT_TOOLS
            failed = result.startswith("ERROR")
            path = call.args.get("path")
            if is_edit and not failed:
                edited = True
                if path:
                    fail_streak[path] = 0
            elif is_edit and failed and path:
                fail_streak[path] = fail_streak.get(path, 0) + 1
                if fail_streak[path] >= EDIT_FAIL_LIMIT:
                    nudge_paths.append(path)
                    fail_streak[path] = 0

        # corrective nudges appended AFTER all tool results (valid ordering)
        for path in nudge_paths:
            print(f"{_YELLOW}  ↳ {path}: {EDIT_FAIL_LIMIT} failed edits — forcing a re-read.{_RESET}")
            messages.append({
                "role": "user",
                "content": (f"Your edits to {path} have failed {EDIT_FAIL_LIMIT} times. "
                            f"Stop guessing the text. Call read_file on {path} to get the "
                            f"exact current contents and line numbers, then use replace_lines.")})

        if stop_reason:
            print(f"\n{_RED}Stopping: the model keeps repeating `{stop_reason}` with the same "
                  f"arguments — it's stuck in a loop.{_RESET}")
            return

    print(f"{_RED}Reached the step limit for this turn.{_RESET}")