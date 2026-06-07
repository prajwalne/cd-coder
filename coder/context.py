"""Context: what the model knows about the repo before it starts searching.

The repo map lists real file PATHS (not contents). It grounds the model so it
uses paths that actually exist instead of inventing them. On a local model
(no token-per-minute cap) we can afford a fuller list; raise/lower MAX_MAP_FILES
and the model's num_ctx together for very large repos.
"""

import subprocess
from pathlib import Path

IGNORE = {".git", "__pycache__", "node_modules", ".venv", "venv", ".DS_Store"}
MAX_MAP_FILES = 1000


MAX_MAP_DIRS = 40  # directory summary, not a file dump — keeps the prompt small


def build_repo_map(repo: Path) -> str:
    """A COMPACT directory summary (folders + file counts), not a file dump.
    Enumerating every file blows the token budget on a token-per-minute-limited
    backend; the model discovers files with find_file/search/outline instead."""
    try:
        out = subprocess.run(["git", "ls-files"], cwd=repo,
                             capture_output=True, text=True, timeout=15)
        files = out.stdout.splitlines() if out.returncode == 0 else []
    except (FileNotFoundError, subprocess.TimeoutExpired):
        files = []
    if not files:
        files = [str(p.relative_to(repo)) for p in repo.rglob("*")
                 if p.is_file() and not any(part in IGNORE for part in p.parts)]
    counts = {}
    for f in files:
        d = f.rsplit("/", 1)[0] + "/" if "/" in f else "(root)"
        counts[d] = counts.get(d, 0) + 1
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:MAX_MAP_DIRS]
    lines = [f"{d}  ({n} files)" for d, n in sorted(top)]
    extra = len(counts) - len(top)
    summary = "\n".join(lines)
    if extra > 0:
        summary += f"\n... ({extra} more directories; use find_file/search to locate files)"
    return summary or "(empty)"


def build_system_prompt(repo: Path) -> str:
    prompt = (
        "You are an autonomous coding agent working inside a git repository.\n"
        "You may read, search and modify files ONLY through the provided tools.\n\n"

        "====================\n"
        "GENERAL RULES\n"
        "====================\n"
        "- The repository file map below contains the REAL paths.\n"
        "- NEVER invent filenames, package names or directories.\n"
        "- If unsure about a path, use find_file.\n"
        "- Search before making assumptions.\n"
        "- Read files before editing them.\n"
        "- Make minimal, focused changes.\n"
        "- Preserve existing code style.\n"
        "- Keep explanations short.\n"
        "- NEVER output code directly to the user.\n"
        "- Apply changes using tools.\n\n"

       "====================\n"
        "FILE DISCOVERY WORKFLOW — FOLLOW THIS ORDER\n"
        "====================\n"
        "Step 1 — you don't know the method name yet:\n"
        "  -> find_symbol('describe what it does in plain english')\n"
        "  -> this returns the exact method name, file, and line range\n\n"
        "Step 2 — you have a method name, need to understand it:\n"
        "  -> code_bundle('methodName')\n"
        "  -> this returns: file location, callers, callees, @annotations\n"
        "  -> costs ZERO file reads\n\n"
        "Step 3 — you need to read the actual code:\n"
        "  -> read_symbol(path, 'methodName') — reads ONE method, not the whole file\n"
        "  -> only use read_file if you need the full file\n\n"
        "Step 4 — editing:\n"
        "  -> apply_patch first, replace_lines second, write_file only for new files\n\n"
        "NEVER start with search or read_file. Always find_symbol first if unsure.\n\n"

        "====================\n"
        "PATCH WORKFLOW\n"
        "====================\n"
        "For existing files:\n"
        "- Prefer apply_patch.\n"
        "- Read the file first.\n"
        "- Copy the exact existing code into old_text.\n"
        "- Put the modified code into new_text.\n"
        "- Include enough surrounding context to make the patch unique.\n"
        "- Prefer method-level patches.\n"
        "- Avoid large rewrites.\n\n"

        "Use replace_lines only when:\n"
        "- apply_patch fails.\n"
        "- A line-number based edit is clearly easier.\n\n"

        "Use edit_file only when:\n"
        "- Replacing a small unique string.\n\n"

        "Use write_file only when:\n"
        "- Creating a genuinely new file.\n"
        "- NEVER recreate an existing file using write_file.\n\n"

        "====================\n"
        "CODE QUALITY RULES\n"
        "====================\n"
        "- Ensure generated code compiles.\n"
        "- Ensure brackets and braces are balanced.\n"
        "- Avoid duplicate methods.\n"
        "- Avoid duplicate imports.\n"
        "- Avoid duplicate classes.\n"
        "- Preserve existing functionality unless explicitly requested otherwise.\n"
        "- Think carefully about imports, types and method signatures.\n\n"

        "====================\n"
        "COMPILATION\n"
        "====================\n"
        "- After editing, STOP. The project is compiled automatically and any\n"
        "  errors are returned to you to fix. You do not need to call compile_project.\n"
        "- When compiler errors come back, fix ONLY those errors, then stop again.\n\n"

        "====================\n"
        "ERROR RECOVERY\n"
        "====================\n"
        "- When compilation fails, trust compiler output.\n"
        "- Do not guess.\n"
        "- Fix reported errors first.\n"
        "- If a patch fails, read the file again.\n"
        "- Generate a new patch based on the latest file contents.\n"
        "- Re-read files before repeatedly attempting the same fix.\n\n"

        "====================\n"
        "TOOL USAGE RULES\n"
        "====================\n"
        "- Prefer apply_patch over replace_lines.\n"
        "- Prefer replace_lines over write_file.\n"
        "- Read before editing.\n"
        "- Search before assuming.\n"
        "- Compile after modifying code.\n"
        "- Begin your reply with ONE short sentence saying what you will do, then call tools.\n"
        "- Use the EXACT path returned by find_file. If unsure of a path, just pass the file\n"
        "  name; tools resolve it automatically. Do not invent placeholder paths.\n"
        "- Make one logical change at a time.\n"
        "- Do NOT page through large files. To inspect one method use read_symbol(path, name);\n"
        "  the truncated read shows a method index with line ranges \u2014 use it.\n"
        "- To find a construct (else, catch, switch, method...) use find_code, then read/edit\n"
        "  that line range. Use outline to see a file's structure.\n"
        "- To add a case to a switch use add_case. To add a method/field use insert_in_class.\n"
        "- To learn where a method is used (callers/callees/annotations) WITHOUT reading files,\n"
        "  call code_bundle(name) \u2014 it is token-cheap; prefer it over reading whole files.\n"
        "- If you cannot find something by keyword (find_file/search) and only have a vague\n"
        "  description, use find_symbol(query) \u2014 semantic search by intent.\n"
        "- Emit tool calls as STRUCTURED calls only \u2014 never as text/JSON, and never use +\n"
        "  to join strings; put the whole value in one JSON string with \\n.\n\n"

        f"Repo root: {repo}\n\n"

        "====================\n"
        "REPOSITORY FILE MAP\n"
        "====================\n"
        f"{build_repo_map(repo)}\n"
    )

    for name in ("CLAUDE.md", "AGENTS.md"):
        f = repo / name
        if f.exists():
            prompt += (
                f"\n\n====================\n"
                f"{name}\n"
                f"====================\n"
                f"{f.read_text(encoding='utf-8')[:4000]}\n"
            )

    return prompt