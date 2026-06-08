# """The tool layer: the only way the model can touch your repo."""
#
# import difflib
# import subprocess
# from pathlib import Path
#
# _GREEN, _RED, _YELLOW, _DIM, _RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
# _IGNORE = {".git", "__pycache__", "node_modules", ".venv", "venv"}
#
# # Tools that mutate files — the agent uses this to know when to auto-compile.
# EDIT_TOOLS = {"write_file", "edit_file", "replace_lines", "apply_patch",
#               "insert_in_class", "add_import", "add_case"}
#
# MAX_READ_LINES = 250  # cap so a huge file can't flood a small model's context
#
# try:
#     from coder import java_ast
#     _JAVA_OK = True
# except Exception:
#     _JAVA_OK = False
#
#
# def _check_java(path: str, new_text: str):
#     """Return an error string if a .java edit would break syntax, else None.
#     Called before writing, so a broken edit is never saved."""
#     if not _JAVA_OK or not path.endswith(".java"):
#         return None
#     ok, msg = java_ast.syntax_check(new_text)
#     if ok:
#         return None
#     return (f"REJECTED: this edit would introduce a syntax error in {path}:\n{msg}\n"
#             "The file was NOT changed. Re-read the file and fix the braces/semicolons.")
#
# _READ_FILES = set()  # stores RESOLVED path strings, so ./a.java and a.java match
#
#
# def reset_read_tracking():
#     _READ_FILES.clear()
#
#
# def _safe_path(repo: Path, path: str) -> Path:
#     p = (repo / path).resolve()
#     if p != repo and repo not in p.parents:
#         raise ValueError(f"Path '{path}' is outside the repo root.")
#     return p
#
#
# def _resolve(repo: Path, path: str):
#     """Resolve a path the model gave us. If it exists, use it. If not — the model
#     passed a placeholder like 'path_to_X.java' or the wrong directory — find a
#     UNIQUE file by name instead. Returns (Path|None, relative_str|None, err|None)."""
#     import os
#     cleaned = (path or "").strip().strip("<>").replace("path_to_", "").replace("path/to/", "")
#     try:
#         p = _safe_path(repo, cleaned)
#         if p.exists() and p.is_file():
#             return p, str(p.relative_to(repo)), None
#     except ValueError:
#         pass
#     base = os.path.basename(cleaned)
#     stem = base.rsplit(".", 1)[0].lower()
#     if not stem:
#         return None, None, f"ERROR: '{path}' not found."
#     matches = [f for f in repo.rglob("*")
#                if f.is_file() and not any(x in _IGNORE for x in f.parts)
#                and (f.name.lower() == base.lower() or f.stem.lower() == stem)]
#     if len(matches) == 1:
#         return matches[0], str(matches[0].relative_to(repo)), None
#     if len(matches) > 1:
#         lst = ", ".join(str(m.relative_to(repo)) for m in matches[:8])
#         return None, None, f"ERROR: '{path}' matches multiple files: {lst}. Pass the full path."
#     return None, None, f"ERROR: no file matching '{path}'. Use find_file to locate it."
#
#
# def _mark_read(repo: Path, path: str) -> None:
#     p, _, _ = _resolve(repo, path)
#     if p is not None:
#         _READ_FILES.add(str(p))
#
#
# def _is_read(repo: Path, path: str) -> bool:
#     p, _, _ = _resolve(repo, path)
#     return p is not None and str(p) in _READ_FILES
#
#
# def _confirm(question: str) -> bool:
#     try:
#         return input(f"{_YELLOW}{question} [y/N] {_RESET}").strip().lower() == "y"
#     except (EOFError, KeyboardInterrupt):
#         return False
#
#
# def _show_diff(path: str, old: str, new: str) -> None:
#     diff = difflib.unified_diff(
#         old.splitlines(), new.splitlines(),
#         fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="",
#     )
#     print()
#     for line in diff:
#         if line.startswith("+") and not line.startswith("+++"):
#             print(f"{_GREEN}{line}{_RESET}")
#         elif line.startswith("-") and not line.startswith("---"):
#             print(f"{_RED}{line}{_RESET}")
#         else:
#             print(f"{_DIM}{line}{_RESET}")
#
#
# # --------------------------------------------------------------- fuzzy locate
# def _locate(original: str, needle: str):
#     """Find `needle` in `original`, tolerating whitespace drift the model adds.
#     Returns (matched_substring, note) or (None, reason). Only returns a match if
#     it is UNIQUE, so we never silently edit the wrong place."""
#     if needle == "":
#         return None, "empty"
#     # 1. exact
#     n = original.count(needle)
#     if n == 1:
#         return needle, None
#     if n > 1:
#         return None, f"appears {n} times"
#
#     o_lines = original.splitlines(keepends=True)
#     need = needle.splitlines()
#     k = len(need)
#     if k == 0 or k > len(o_lines):
#         return None, "not found"
#
#     # 2. ignore trailing whitespace per line, then 3. ignore all leading/trailing
#     for norm, label in ((lambda s: s.rstrip(), "trailing-space"),
#                         (lambda s: s.strip(), "indentation")):
#         need_n = [norm(x) for x in need]
#         hits = []
#         for i in range(len(o_lines) - k + 1):
#             window = [norm(o_lines[i + j].rstrip("\n")) for j in range(k)]
#             if window == need_n:
#                 hits.append(i)
#         if len(hits) == 1:
#             i = hits[0]
#             matched = "".join(o_lines[i:i + k])
#             if matched.endswith("\n") and not needle.endswith("\n"):
#                 matched = matched[:-1]
#             return matched, f"fuzzy match (ignored {label})"
#         if len(hits) > 1:
#             return None, f"appears {len(hits)} times (fuzzy)"
#     return None, "not found"
#
#
# def list_dir(repo: Path, path: str = ".") -> str:
#     p = _safe_path(repo, path)
#     if not p.is_dir():
#         return f"ERROR: {path} is not a directory."
#     entries = [c.name + ("/" if c.is_dir() else "")
#                for c in sorted(p.iterdir()) if c.name not in _IGNORE]
#     return "\n".join(entries) or "(empty)"
#
#
# def read_file(repo: Path, path: str, line_start: int = None, line_end: int = None) -> str:
#     p = _safe_path(repo, path)
#     _mark_read(repo, path)
#     if not p.exists():
#         p2, _rel, _msg = _resolve(repo, path)
#         if p2 is None:
#             return _msg
#         p, path = p2, _rel
#     lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
#     n = len(lines)
#     if line_start or line_end:  # explicit range: honor it, capped to a window
#         s = max(0, (line_start or 1) - 1)
#         e = min(n, line_end or n, s + MAX_READ_LINES)
#         return "\n".join(f"{s + i + 1:>5}\t{ln}" for i, ln in enumerate(lines[s:e]))
#     if n <= MAX_READ_LINES:
#         return "\n".join(f"{i + 1:>5}\t{ln}" for i, ln in enumerate(lines))
#     # large file, no range: head + a method index so the model reads targeted,
#     # instead of dumping thousands of lines into a small context window.
#     head = "\n".join(f"{i + 1:>5}\t{ln}" for i, ln in enumerate(lines[:MAX_READ_LINES]))
#     index = ""
#     if _JAVA_OK and path.endswith(".java"):
#         names = ", ".join(f"{sym['name']} ({sym['line_start']}-{sym['line_end']})"
#                           for sym in java_ast.list_symbols("\n".join(lines))
#                           if sym["kind"] in ("method", "constructor"))
#         if names:
#             index = f"\nMethods: {names[:1500]}"
#     return (head + f"\n\n... TRUNCATED: {path} has {n} lines; showing 1-{MAX_READ_LINES}. "
#             f"Read a specific range with line_start/line_end, or call read_symbol "
#             f"for a single method.{index}")
#
#
# def read_symbol(repo: Path, path: str, name: str) -> str:
#     """Read ONE class/method/field by name instead of the whole file. Use this to
#     inspect a single function (e.g. savePaymentLink) in a large file."""
#     p = _safe_path(repo, path)
#     if not p.exists():
#         p2, _rel, _msg = _resolve(repo, path)
#         if p2 is None:
#             return _msg
#         p, path = p2, _rel
#     if not _JAVA_OK:
#         return "ERROR: java_ast unavailable; use read_file with line_start/line_end."
#     src = p.read_text(encoding="utf-8", errors="replace")
#     _mark_read(repo, path)
#     syms = [sym for sym in java_ast.list_symbols(src) if sym["name"] == name]
#     if not syms:
#         avail = ", ".join(sym["name"] for sym in java_ast.list_symbols(src)
#                           if sym["kind"] in ("method", "constructor"))
#         return f"No symbol named '{name}' in {path}. Methods: {avail[:800]}"
#     lines = src.splitlines()
#     out = []
#     for sym in syms:
#         body = "\n".join(f"{sym['line_start'] + i:>5}\t{ln}"
#                          for i, ln in enumerate(lines[sym['line_start'] - 1:sym['line_end']]))
#         out.append(f"// {sym['kind']} {sym['name']} (lines {sym['line_start']}-{sym['line_end']})\n{body}")
#     return "\n\n".join(out)
#
#
# def search(repo: Path, query: str) -> str:
#     try:
#         out = subprocess.run(
#             ["rg", "--line-number", "--no-heading", "--max-count", "20",
#              "--max-columns", "200", query, "."],
#             cwd=repo, capture_output=True, text=True, timeout=30)
#     except FileNotFoundError:
#         return "ERROR: ripgrep (rg) is not installed."
#     if out.returncode not in (0, 1):
#         return f"ERROR: {out.stderr.strip()}"
#     return out.stdout.strip()[:6000] or f"No matches for '{query}'."
#
#
# _NOISE_WORDS = {"file", "the", "a", "an", "class", "java"}
#
#
# def find_file(repo: Path, name: str) -> str:
#     """Find files by name, tolerant of loose phrasing."""
#     import re
#     try:
#         out = subprocess.run(["rg", "--files"], cwd=repo,
#                              capture_output=True, text=True, timeout=30)
#         files = out.stdout.splitlines() if out.returncode == 0 else []
#     except FileNotFoundError:
#         files = [str(p.relative_to(repo)) for p in repo.rglob("*") if p.is_file()]
#     files = [f for f in files
#              if not any(part in _IGNORE for part in f.replace("\\", "/").split("/"))]
#     raw = [t for t in re.split(r"[\s/_.-]+", name.lower()) if t]
#     tokens = [t for t in raw if t not in _NOISE_WORDS] or raw or [name.lower()]
#     matches = [f for f in files if name.lower() in f.lower()]
#     if not matches:
#         matches = [f for f in files if all(t in f.lower() for t in tokens)]
#     if not matches:
#         matches = [f for f in files if any(t in f.lower() for t in tokens)]
#     return "\n".join(sorted(matches)[:50]) or f"No files matching '{name}'."
#
#
# def write_file(repo: Path, path: str, content: str) -> str:
#     p = _safe_path(repo, path)
#     old = p.read_text(encoding="utf-8") if p.exists() else ""
#     err = _check_java(path, content)
#     if err:
#         return err
#     _show_diff(path, old, content)
#     if not _confirm(f"Write {path}?"):
#         return "User declined the write."
#     p.parent.mkdir(parents=True, exist_ok=True)
#     p.write_text(content, encoding="utf-8")
#     _mark_read(repo, path)  # writing means we know its current contents
#     return f"{'Overwrote' if old else 'Created'} {path} ({len(content)} bytes)."
#
#
# def edit_file(repo: Path, path: str, old_str: str, new_str: str) -> str:
#     if not _is_read(repo, path):
#         return f"ERROR: {path} must be read with read_file before it can be modified."
#     p = _safe_path(repo, path)
#     if not p.exists():
#         p2, _rel, _msg = _resolve(repo, path)
#         if p2 is None:
#             return _msg
#         p, path = p2, _rel
#     if old_str == "":
#         return ("ERROR: old_str is empty. edit_file REPLACES text and cannot insert. "
#                 "To add new code use replace_lines (read the file first for line numbers).")
#     text = p.read_text(encoding="utf-8")
#     matched, note = _locate(text, old_str)
#     if matched is None:
#         return (f"ERROR: old_str {note}. Re-read the file to copy the exact current text, "
#                 "or use replace_lines to edit by line number.")
#     updated = text.replace(matched, new_str, 1)
#     err = _check_java(path, updated)
#     if err:
#         return err
#     _show_diff(path, text, updated)
#     extra = f" ({note})" if note else ""
#     if not _confirm(f"Apply edit to {path}{extra}?"):
#         return "User declined the edit."
#     p.write_text(updated, encoding="utf-8")
#     return f"Edited {path}{(' — ' + note) if note else ''}."
#
#
# def replace_lines(repo: Path, path: str, start_line: int, end_line: int, new_text: str) -> str:
#     if not _is_read(repo, path):
#         return f"ERROR: {path} must be read with read_file before it can be modified."
#     p = _safe_path(repo, path)
#     if not p.exists():
#         p2, _rel, _msg = _resolve(repo, path)
#         if p2 is None:
#             return _msg
#         p, path = p2, _rel
#     original = p.read_text(encoding="utf-8")
#     lines = original.splitlines()
#     if start_line < 1 or end_line > len(lines) or start_line > end_line:
#         return f"ERROR: invalid range {start_line}-{end_line}; file has {len(lines)} lines."
#     updated_lines = lines[:start_line - 1] + new_text.splitlines() + lines[end_line:]
#     updated = "\n".join(updated_lines) + ("\n" if original.endswith("\n") else "")
#     err = _check_java(path, updated)
#     if err:
#         return err
#     _show_diff(path, original, updated)
#     if not _confirm(f"Replace lines {start_line}-{end_line} in {path}?"):
#         return "User declined the edit."
#     p.write_text(updated, encoding="utf-8")
#     return f"Replaced lines {start_line}-{end_line} in {path}."
#
#
# def apply_patch(repo: Path, path: str, old_text: str, new_text: str) -> str:
#     """Preferred edit tool: replace an exact (or whitespace-fuzzy) unique block."""
#     if not _is_read(repo, path):
#         return f"ERROR: {path} must be read with read_file before it can be modified."
#     p = _safe_path(repo, path)
#     if not p.exists():
#         p2, _rel, _msg = _resolve(repo, path)
#         if p2 is None:
#             return _msg
#         p, path = p2, _rel
#     if old_text == "":
#         return ("ERROR: old_text is empty. apply_patch replaces existing text; "
#                 "to insert new code use replace_lines.")
#     original = p.read_text(encoding="utf-8")
#     matched, note = _locate(original, old_text)
#     if matched is None:
#         return (f"ERROR: patch context {note}. Re-read the file and copy the exact current "
#                 "text into old_text, or use replace_lines to edit by line number.")
#     updated = original.replace(matched, new_text, 1)
#     err = _check_java(path, updated)
#     if err:
#         return err
#     _show_diff(path, original, updated)
#     extra = f" ({note})" if note else ""
#     if not _confirm(f"Apply patch to {path}{extra}?"):
#         return "User declined the patch."
#     p.write_text(updated, encoding="utf-8")
#     return f"Patched {path}{(' — ' + note) if note else ''}."
#
#
# # ----------------------------------------------------------------- compilation
# def is_maven(repo: Path) -> bool:
#     return (repo / "pom.xml").exists()
#
#
# def _run_mvn(repo: Path) -> subprocess.CompletedProcess:
#     return subprocess.run(["mvn", "-q", "-DskipTests", "compile"],
#                           cwd=repo, capture_output=True, text=True, timeout=300)
#
#
# def compile_project(repo: Path) -> str:
#     """Manual compile (model-invoked). Auto-compile after edits is in the agent."""
#     print(f"\n{_YELLOW}compile:{_RESET} mvn -q -DskipTests compile")
#     if not _confirm("Allow compilation?"):
#         return "User declined compilation."
#     try:
#         out = _run_mvn(repo)
#     except subprocess.TimeoutExpired:
#         return "ERROR: compilation timed out after 300s."
#     except Exception as e:
#         return f"ERROR: {e}"
#     result = f"exit code: {out.returncode}\n"
#     if out.stdout:
#         result += f"stdout:\n{out.stdout[-8000:]}\n"
#     if out.stderr:
#         result += f"stderr:\n{out.stderr[-8000:]}\n"
#     if out.returncode == 0:
#         result += "\nCOMPILATION SUCCESSFUL"
#     return result.strip()
#
#
# def auto_compile(repo: Path):
#     """Non-interactive compile for the agent to run after edits.
#     Returns (ok: bool, output: str)."""
#     print(f"\n{_YELLOW}auto-compile:{_RESET} mvn -q -DskipTests compile")
#     try:
#         out = _run_mvn(repo)
#     except subprocess.TimeoutExpired:
#         return False, "ERROR: compilation timed out after 300s."
#     except Exception as e:
#         return False, f"ERROR: {e}"
#     body = (out.stdout or "") + "\n" + (out.stderr or "")
#     return out.returncode == 0, body.strip()[-8000:]
#
#
# def compile_signature(output: str) -> str:
#     """A stable fingerprint of the compiler errors, to detect the model failing
#     to make progress on the same error."""
#     errs = [ln.strip() for ln in output.splitlines()
#             if "ERROR" in ln or "error:" in ln]
#     return "\n".join(errs[:5])
#
#
# def run_command(repo: Path, command: str) -> str:
#     print(f"\n{_YELLOW}run:{_RESET} {command}")
#     if not _confirm("Allow this command?"):
#         return "User declined the command."
#     try:
#         out = subprocess.run(command, shell=True, cwd=repo,
#                              capture_output=True, text=True, timeout=120)
#     except subprocess.TimeoutExpired:
#         return "ERROR: command timed out after 120s."
#     result = f"exit code: {out.returncode}\n"
#     if out.stdout:
#         result += f"stdout:\n{out.stdout[-4000:]}\n"
#     if out.stderr:
#         result += f"stderr:\n{out.stderr[-4000:]}\n"
#     return result.strip()
#
#
#
# # ===== Java structural tools =====
#
# _INDEX_FILE = ".cdindex.sqlite"
#
#
# def code_bundle(repo: Path, name: str) -> str:
#     """Where a symbol is, who calls it, what it calls, and its annotations — from
#     a prebuilt index, NO file reading and NO model tokens spent exploring. Builds
#     the index on first use. Use this to understand a symbol before editing."""
#     if not _JAVA_OK:
#         return "ERROR: java_ast unavailable."
#     from coder import codeindex
#     db = str(repo / _INDEX_FILE)
#     try:
#         if not (repo / _INDEX_FILE).exists():
#             codeindex.build_index(repo, db)
#         return codeindex.bundle(db, name, repo)
#     except Exception as e:
#         return f"ERROR building/reading index: {e}"
#
#
# def find_symbol(repo: Path, query: str) -> str:
#     """Find a method/class by DESCRIPTION or intent, not exact name — e.g.
#     'cron that deletes expired POs' -> pushDeletionIdstoKafka. Use this when
#     find_file/search can't locate something by keyword. Semantic search over the
#     code (needs an Ollama embedding model; builds embeddings on first use)."""
#     if not _JAVA_OK:
#         return "ERROR: java_ast unavailable."
#     import os
#     import sqlite3
#     from coder import semindex
#     host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
#     model = os.getenv("EMBED_MODEL", semindex.EMBED_MODEL_DEFAULT)
#     db = str(repo / _INDEX_FILE)
#     embed = lambda text: semindex.ollama_embed(text, model, host)
#     try:
#         need_build = True
#         if (repo / _INDEX_FILE).exists():
#             try:
#                 c = sqlite3.connect(db).execute("SELECT count(*) FROM embeddings").fetchone()[0]
#                 need_build = c == 0
#             except sqlite3.OperationalError:
#                 need_build = True
#         if need_build:
#             print("  (building semantic index — one-time, needs Ollama embedding model)")
#             semindex.build_embeddings(repo, db, embed)
#         hits = semindex.semantic_search(db, query, embed)
#     except Exception as e:
#         return ("ERROR: semantic search needs an Ollama embedding model. Run "
#                 "`ollama pull nomic-embed-text` and ensure OLLAMA_HOST is reachable. " + str(e)[:120])
#     if not hits:
#         return f"No semantic matches for '{query}'."
#     return "\n".join(f"{h['score']:.2f}  {h['kind']} {h['name']}  ({h['file']}:{h['line_start']}-{h['line_end']})"
#                      for h in hits)
#
#
#
# def outline(repo: Path, path: str) -> str:
#     """List classes/methods/fields (and switches) with line ranges. Find WHERE to
#     add a method or case before editing. Counts as reading the file."""
#     p = _safe_path(repo, path)
#     if not p.exists():
#         p2, _rel, _msg = _resolve(repo, path)
#         if p2 is None:
#             return _msg
#         p, path = p2, _rel
#     if not _JAVA_OK:
#         return "ERROR: java_ast (tree-sitter) is not available."
#     src = p.read_text(encoding="utf-8", errors="replace")
#     _mark_read(repo, path)
#     syms = java_ast.list_symbols(src)
#     lines = [f"{x['line_start']:>4}-{x['line_end']:<4} {x['kind']:<12} {x['name']}" for x in syms]
#     for sw in java_ast.list_switches(src):
#         lines.append(f"{sw['line']:>4}      switch       switch({sw['discriminant']}) in {sw['method']}()")
#     return "\n".join(lines) or "(no symbols found)"
#
#
# def insert_in_class(repo: Path, path: str, class_name: str, code: str) -> str:
#     """Add a method/field at the END of a named class (before its closing brace).
#     Placement and indentation are computed for you."""
#     if not _is_read(repo, path):
#         return f"ERROR: {path} must be read (read_file/outline) before editing."
#     p = _safe_path(repo, path)
#     if not p.exists():
#         p2, _rel, _msg = _resolve(repo, path)
#         if p2 is None:
#             return _msg
#         p, path = p2, _rel
#     if not _JAVA_OK:
#         return "ERROR: java_ast unavailable."
#     src = p.read_text(encoding="utf-8")
#     off, ok, msg = java_ast.insertion_offset_in_type(src, class_name)
#     if not ok:
#         return f"ERROR: {msg}"
#     body = "\n".join(("    " + ln if ln.strip() else ln) for ln in code.splitlines())
#     updated = src[:off] + "\n" + body + "\n" + src[off:]
#     err = _check_java(path, updated)
#     if err:
#         return err
#     _show_diff(path, src, updated)
#     if not _confirm(f"Insert into class {class_name} in {path}?"):
#         return "User declined the insert."
#     p.write_text(updated, encoding="utf-8")
#     return f"Inserted code into class {class_name} in {path}."
#
#
# def add_import(repo: Path, path: str, statement: str) -> str:
#     """Add an import in the right place, skipping duplicates."""
#     if not _is_read(repo, path):
#         return f"ERROR: {path} must be read (read_file/outline) before editing."
#     p = _safe_path(repo, path)
#     if not p.exists():
#         p2, _rel, _msg = _resolve(repo, path)
#         if p2 is None:
#             return _msg
#         p, path = p2, _rel
#     if not _JAVA_OK:
#         return "ERROR: java_ast unavailable."
#     stmt = statement.strip()
#     if not stmt.startswith("import "):
#         stmt = "import " + stmt
#     if not stmt.endswith(";"):
#         stmt += ";"
#     fqcn = stmt[len("import "):].rstrip(";").replace("static ", "").strip()
#     src = p.read_text(encoding="utf-8")
#     if java_ast.has_import(src, fqcn):
#         return f"Import for '{fqcn}' already present."
#     off = java_ast.import_block_offset(src)
#     updated = src[:off] + "\n" + stmt + src[off:]
#     err = _check_java(path, updated)
#     if err:
#         return err
#     _show_diff(path, src, updated)
#     if not _confirm(f"Add '{stmt}' to {path}?"):
#         return "User declined the import."
#     p.write_text(updated, encoding="utf-8")
#     return f"Added {stmt} to {path}."
#
#
# def add_case(repo: Path, path: str, case_code: str,
#              discriminant: str = None, method: str = None, index: int = None) -> str:
#     """Add a case to an existing switch, before 'default', matching indentation.
#     Use this for 'add a case' requests (NOT insert_in_class)."""
#     if not _is_read(repo, path):
#         return f"ERROR: {path} must be read (read_file/outline) before editing."
#     p = _safe_path(repo, path)
#     if not p.exists():
#         p2, _rel, _msg = _resolve(repo, path)
#         if p2 is None:
#             return _msg
#         p, path = p2, _rel
#     if not _JAVA_OK:
#         return "ERROR: java_ast unavailable."
#     src = p.read_text(encoding="utf-8")
#     off, indent, ok, msg = java_ast.case_insert_point(src, discriminant, method, index)
#     if not ok:
#         return f"ERROR: {msg}"
#     lines = case_code.splitlines()
#     nonblank = [ln for ln in lines if ln.strip()]
#     common = min((len(ln) - len(ln.lstrip()) for ln in nonblank), default=0)
#     block = "\n".join((indent + ln[common:] if ln.strip() else "") for ln in lines) + "\n"
#     updated = src[:off] + block + src[off:]
#     err = _check_java(path, updated)
#     if err:
#         return err
#     _show_diff(path, src, updated)
#     if not _confirm(f"Add case to {path}?"):
#         return "User declined the case."
#     p.write_text(updated, encoding="utf-8")
#     return f"Added case to {path}."
#
#
# def find_code(repo: Path, path: str, kind: str,
#               in_method: str = None, in_class: str = None) -> str:
#     """Locate constructs (else, catch, finally, if, for, while, try, switch,
#     method, class, field, return, lambda) and get their line ranges to read/edit.
#     Counts as reading the file."""
#     p = _safe_path(repo, path)
#     if not p.exists():
#         p2, _rel, _msg = _resolve(repo, path)
#         if p2 is None:
#             return _msg
#         p, path = p2, _rel
#     if not _JAVA_OK:
#         return "ERROR: java_ast unavailable."
#     src = p.read_text(encoding="utf-8", errors="replace")
#     _mark_read(repo, path)
#     hits = java_ast.find_code(src, kind, in_method, in_class)
#     if hits and "error" in hits[0]:
#         return f"ERROR: {hits[0]['error']}"
#     if not hits:
#         scope = f" in {in_method or in_class}" if (in_method or in_class) else ""
#         return f"No '{kind}' found{scope}."
#     return "\n".join(f"lines {h['line_start']}-{h['line_end']}: {h['snippet']}" for h in hits)
#
#
# _FUNCS = {
#     "list_dir": list_dir, "read_file": read_file, "search": search,
#     "find_file": find_file, "read_symbol": read_symbol,
#     "outline": outline, "insert_in_class": insert_in_class,
#     "add_import": add_import, "add_case": add_case, "find_code": find_code,
#     "code_bundle": code_bundle, "find_symbol": find_symbol,
#     "write_file": write_file, "edit_file": edit_file,
#     "replace_lines": replace_lines, "apply_patch": apply_patch,
#     "compile_project": compile_project, "run_command": run_command,
# }
#
# TOOL_SCHEMAS = [
#     {"type": "function", "function": {
#         "name": "list_dir",
#         "description": "List files and folders in a directory, relative to the repo root.",
#         "parameters": {"type": "object",
#                        "properties": {"path": {"type": "string", "description": "Default '.'"}},
#                        "required": []}}},
#     {"type": "function", "function": {
#         "name": "read_file",
#         "description": "Read a file (lines prefixed with line numbers). Optionally pass "
#                        "line_start and line_end (1-based, inclusive) to read a slice.",
#         "parameters": {"type": "object",
#                        "properties": {"path": {"type": "string"},
#                                       "line_start": {"type": "integer"},
#                                       "line_end": {"type": "integer"}},
#                        "required": ["path"]}}},
#     {"type": "function", "function": {
#         "name": "read_symbol",
#         "description": "Read a single method/class/field by NAME instead of the whole file. "
#                        "Use this for large files (e.g. read_symbol(path, 'savePaymentLink')) "
#                        "so you don't flood context. Counts as reading the file.",
#         "parameters": {"type": "object",
#                        "properties": {"path": {"type": "string"}, "name": {"type": "string"}},
#                        "required": ["path", "name"]}}},
#     {"type": "function", "function": {
#         "name": "outline",
#         "description": "List classes/methods/fields and switches with line ranges. Use to find "
#                        "WHERE to add a method or case before editing. Counts as reading the file.",
#         "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
#                        "required": ["path"]}}},
#     {"type": "function", "function": {
#         "name": "find_code",
#         "description": "Locate a construct and get its line range, then read/edit it. kind: method, "
#                        "class, field, if, else, for, while, try, catch, finally, switch, return, "
#                        "lambda. Optional in_method / in_class scope. e.g. find the else block in "
#                        "run() -> find_code(path, kind='else', in_method='run').",
#         "parameters": {"type": "object",
#                        "properties": {"path": {"type": "string"}, "kind": {"type": "string"},
#                                       "in_method": {"type": "string"}, "in_class": {"type": "string"}},
#                        "required": ["path", "kind"]}}},
#     {"type": "function", "function": {
#         "name": "code_bundle",
#         "description": "Get a symbol's location, callers, callees, and annotations from the "
#                        "code index without reading files. Use this to understand where/how a "
#                        "method (e.g. savePaymentLink) is used before editing.",
#         "parameters": {"type": "object", "properties": {"name": {"type": "string"}},
#                        "required": ["name"]}}},
#     {"type": "function", "function": {
#         "name": "find_symbol",
#         "description": "Find a method/class by DESCRIPTION or intent when you don't know the "
#                        "exact name \u2014 e.g. find_symbol('cron that deletes expired POs'). "
#                        "Semantic search over the code. Use when find_file/search fail to locate it.",
#         "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
#                        "required": ["query"]}}},
#     {"type": "function", "function": {
#         "name": "add_case",
#         "description": "Add a case to an EXISTING switch (use this for 'add a case', NOT "
#                        "insert_in_class). case_code is the full case, e.g. "
#                        "'case 84:\\n    foo();\\n    break;'. If the file has multiple switches, "
#                        "pass discriminant (the variable switched on) or method (enclosing method).",
#         "parameters": {"type": "object",
#                        "properties": {"path": {"type": "string"}, "case_code": {"type": "string"},
#                                       "discriminant": {"type": "string"}, "method": {"type": "string"}},
#                        "required": ["path", "case_code"]}}},
#     {"type": "function", "function": {
#         "name": "insert_in_class",
#         "description": "Add a method or field at the end of a named class. Supply class_name and "
#                        "code; placement/indentation are computed. Prefer over replace_lines for members.",
#         "parameters": {"type": "object",
#                        "properties": {"path": {"type": "string"}, "class_name": {"type": "string"},
#                                       "code": {"type": "string"}},
#                        "required": ["path", "class_name", "code"]}}},
#     {"type": "function", "function": {
#         "name": "add_import",
#         "description": "Add an import to a Java file in the correct place, skipping duplicates.",
#         "parameters": {"type": "object",
#                        "properties": {"path": {"type": "string"}, "statement": {"type": "string"}},
#                        "required": ["path", "statement"]}}},
#     {"type": "function", "function": {
#         "name": "search",
#         "description": "Search inside file CONTENTS for a keyword or pattern (ripgrep).",
#         "parameters": {"type": "object",
#                        "properties": {"query": {"type": "string"}},
#                        "required": ["query"]}}},
#     {"type": "function", "function": {
#         "name": "find_file",
#         "description": "Find a file by its NAME (or part of it). search = contents; "
#                        "find_file = filenames.",
#         "parameters": {"type": "object",
#                        "properties": {"name": {"type": "string"}},
#                        "required": ["name"]}}},
#     {"type": "function", "function": {
#         "name": "apply_patch",
#         "description": "Preferred editing tool. Read the file first, copy the exact existing "
#                        "code into old_text, and patch only the smallest required block. "
#                        "Whitespace drift is tolerated automatically.",
#         "parameters": {"type": "object",
#                        "properties": {"path": {"type": "string"},
#                                       "old_text": {"type": "string"},
#                                       "new_text": {"type": "string"}},
#                        "required": ["path", "old_text", "new_text"]}}},
#     {"type": "function", "function": {
#         "name": "write_file",
#         "description": "Create a new file or completely overwrite an existing one.",
#         "parameters": {"type": "object",
#                        "properties": {"path": {"type": "string"},
#                                       "content": {"type": "string", "description": "Full file contents."}},
#                        "required": ["path", "content"]}}},
#     {"type": "function", "function": {
#         "name": "edit_file",
#         "description": "Replace an exact unique string in a file. Read the file first; "
#                        "old_str must come from read_file output.",
#         "parameters": {"type": "object",
#                        "properties": {"path": {"type": "string"},
#                                       "old_str": {"type": "string"},
#                                       "new_str": {"type": "string"}},
#                        "required": ["path", "old_str", "new_str"]}}},
#     {"type": "function", "function": {
#         "name": "replace_lines",
#         "description": "Replace a range of lines (by number) with new text. Prefer this over "
#                        "edit_file when whitespace makes exact matching hard. Read the file first.",
#         "parameters": {"type": "object",
#                        "properties": {"path": {"type": "string"},
#                                       "start_line": {"type": "integer"},
#                                       "end_line": {"type": "integer"},
#                                       "new_text": {"type": "string"}},
#                        "required": ["path", "start_line", "end_line", "new_text"]}}},
#     {"type": "function", "function": {
#         "name": "compile_project",
#         "description": "Compile the Maven project ('mvn -q -DskipTests compile'). The agent "
#                        "also compiles automatically after edits, so you rarely need this.",
#         "parameters": {"type": "object", "properties": {}, "required": []}}},
#     {"type": "function", "function": {
#         "name": "run_command",
#         "description": "Run a shell command in the repo (tests, git, linters). User approves it.",
#         "parameters": {"type": "object",
#                        "properties": {"command": {"type": "string"}},
#                        "required": ["command"]}}},
# ]
#
#
# def run_tool(repo: Path, name: str, args: dict) -> str:
#     fn = _FUNCS.get(name)
#     if fn is None:
#         return f"ERROR: unknown tool '{name}'."
#     try:
#         return fn(repo, **args)
#     except TypeError as e:
#         return f"ERROR: bad arguments for {name}: {e}"
#     except Exception as e:
#         return f"ERROR: {e}"
"""The tool layer: the only way the model can touch your repo."""

import difflib
import subprocess
from pathlib import Path

_GREEN, _RED, _YELLOW, _DIM, _RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
_IGNORE = {".git", "__pycache__", "node_modules", ".venv", "venv"}

# Tools that mutate files — the agent uses this to know when to auto-compile.
EDIT_TOOLS = {"edit_file", "add_case"}  # trimmed: other editors still work if called, just not advertised

MAX_READ_LINES = 600  # cap so a huge file can't flood a small model's context

try:
    from coder import java_ast
    _JAVA_OK = True
except Exception:
    _JAVA_OK = False


def _check_java(path: str, new_text: str):
    """Return an error string if a .java edit would break syntax, else None.
    Called before writing, so a broken edit is never saved."""
    if not _JAVA_OK or not path.endswith(".java"):
        return None
    ok, msg = java_ast.syntax_check(new_text)
    if ok:
        return None
    return (f"REJECTED: this edit would introduce a syntax error in {path}:\n{msg}\n"
            "The file was NOT changed. Re-read the file and fix the braces/semicolons.")

_READ_FILES = set()  # stores RESOLVED path strings, so ./a.java and a.java match
_RESOLVE_CACHE = {}  # raw path -> resolved Path (populated by _resolve, used by _mark_read/_is_read)


def reset_read_tracking():
    _READ_FILES.clear()
    _RESOLVE_CACHE.clear()


def _safe_path(repo: Path, path: str) -> Path:
    p = (repo / path).resolve()
    if p != repo and repo not in p.parents:
        raise ValueError(f"Path '{path}' is outside the repo root.")
    return p


def _resolve(repo: Path, path: str):
    """Resolve a path the model gave us. If it exists, use it. If not — the model
    passed a placeholder like 'path_to_X.java' or the wrong directory — find a
    UNIQUE file by name instead. Returns (Path|None, relative_str|None, err|None).
    Caches successful resolutions so rglob only fires once per unique path."""
    if path in _RESOLVE_CACHE:
        p = _RESOLVE_CACHE[path]
        return p, str(p.relative_to(repo)), None
    import os
    cleaned = (path or "").strip().strip("<>").replace("path_to_", "").replace("path/to/", "")
    try:
        p = _safe_path(repo, cleaned)
        if p.exists() and p.is_file():
            _RESOLVE_CACHE[path] = p
            return p, str(p.relative_to(repo)), None
    except ValueError:
        pass
    base = os.path.basename(cleaned)
    stem = base.rsplit(".", 1)[0].lower()
    if not stem:
        return None, None, f"ERROR: '{path}' not found."
    matches = [f for f in repo.rglob("*")
               if f.is_file() and not any(x in _IGNORE for x in f.parts)
               and (f.name.lower() == base.lower() or f.stem.lower() == stem)]
    if len(matches) == 1:
        _RESOLVE_CACHE[path] = matches[0]
        return matches[0], str(matches[0].relative_to(repo)), None
    if len(matches) > 1:
        lst = ", ".join(str(m.relative_to(repo)) for m in matches[:8])
        return None, None, f"ERROR: '{path}' matches multiple files: {lst}. Pass the full path."
    return None, None, f"ERROR: no file matching '{path}'. Use find_file to locate it."


def _mark_read(repo: Path, path: str) -> None:
    if path in _RESOLVE_CACHE:
        _READ_FILES.add(str(_RESOLVE_CACHE[path]))
        return
    try:
        _READ_FILES.add(str(_safe_path(repo, path)))
    except ValueError:
        pass


def _is_read(repo: Path, path: str) -> bool:
    if path in _RESOLVE_CACHE:
        return str(_RESOLVE_CACHE[path]) in _READ_FILES
    try:
        return str(_safe_path(repo, path)) in _READ_FILES
    except ValueError:
        return False


def _confirm(question: str) -> bool:
    try:
        return input(f"{_YELLOW}{question} [y/N] {_RESET}").strip().lower() == "y"
    except (EOFError, KeyboardInterrupt):
        return False


def _show_diff(path: str, old: str, new: str) -> None:
    diff = difflib.unified_diff(
        old.splitlines(), new.splitlines(),
        fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="",
    )
    print()
    for line in diff:
        if line.startswith("+") and not line.startswith("+++"):
            print(f"{_GREEN}{line}{_RESET}")
        elif line.startswith("-") and not line.startswith("---"):
            print(f"{_RED}{line}{_RESET}")
        else:
            print(f"{_DIM}{line}{_RESET}")


# --------------------------------------------------------------- fuzzy locate
def _locate(original: str, needle: str):
    """Find `needle` in `original`, tolerating whitespace drift the model adds.
    Returns (matched_substring, note) or (None, reason). Only returns a match if
    it is UNIQUE, so we never silently edit the wrong place."""
    if needle == "":
        return None, "empty"
    # 1. exact
    n = original.count(needle)
    if n == 1:
        return needle, None
    if n > 1:
        return None, f"appears {n} times"

    o_lines = original.splitlines(keepends=True)
    need = needle.splitlines()
    k = len(need)
    if k == 0 or k > len(o_lines):
        return None, "not found"

    # 2. ignore trailing whitespace per line, then 3. ignore all leading/trailing
    for norm, label in ((lambda s: s.rstrip(), "trailing-space"),
                        (lambda s: s.strip(), "indentation")):
        need_n = [norm(x) for x in need]
        hits = []
        for i in range(len(o_lines) - k + 1):
            window = [norm(o_lines[i + j].rstrip("\n")) for j in range(k)]
            if window == need_n:
                hits.append(i)
        if len(hits) == 1:
            i = hits[0]
            matched = "".join(o_lines[i:i + k])
            if matched.endswith("\n") and not needle.endswith("\n"):
                matched = matched[:-1]
            return matched, f"fuzzy match (ignored {label})"
        if len(hits) > 1:
            return None, f"appears {len(hits)} times (fuzzy)"
    return None, "not found"


def list_dir(repo: Path, path: str = ".") -> str:
    p = _safe_path(repo, path)
    if not p.is_dir():
        return f"ERROR: {path} is not a directory."
    entries = [c.name + ("/" if c.is_dir() else "")
               for c in sorted(p.iterdir()) if c.name not in _IGNORE]
    return "\n".join(entries) or "(empty)"


def read_file(repo: Path, path: str, line_start: int = None, line_end: int = None) -> str:
    p = _safe_path(repo, path)
    _mark_read(repo, path)
    if not p.exists():
        p2, _rel, _msg = _resolve(repo, path)
        if p2 is None:
            return _msg
        p, path = p2, _rel
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    n = len(lines)
    if line_start or line_end:  # explicit range: honor it, capped to a window
        s = max(0, (line_start or 1) - 1)
        e = min(n, line_end or n, s + MAX_READ_LINES)
        return "\n".join(f"{s + i + 1:>5}\t{ln}" for i, ln in enumerate(lines[s:e]))
    if n <= MAX_READ_LINES:
        return "\n".join(f"{i + 1:>5}\t{ln}" for i, ln in enumerate(lines))
    # large file, no range: head + a method index so the model reads targeted,
    # instead of dumping thousands of lines into a small context window.
    head = "\n".join(f"{i + 1:>5}\t{ln}" for i, ln in enumerate(lines[:MAX_READ_LINES]))
    index = ""
    if _JAVA_OK and path.endswith(".java"):
        names = ", ".join(f"{sym['name']} ({sym['line_start']}-{sym['line_end']})"
                          for sym in java_ast.list_symbols("\n".join(lines))
                          if sym["kind"] in ("method", "constructor"))
        if names:
            index = f"\nMethods: {names[:1500]}"
    return (head + f"\n\n... TRUNCATED: {path} has {n} lines; showing 1-{MAX_READ_LINES}. "
            f"Read a specific range with line_start/line_end, or call read_symbol "
            f"for a single method.{index}")


def read_symbol(repo: Path, path: str, name: str) -> str:
    """Read ONE class/method/field by name instead of the whole file. Use this to
    inspect a single function (e.g. savePaymentLink) in a large file."""
    p, rel, err = _resolve(repo, path)
    if p is None:
        return err
    path = rel
    if not _JAVA_OK:
        return "ERROR: java_ast unavailable; use read_file with line_start/line_end."
    src = p.read_text(encoding="utf-8", errors="replace")
    _mark_read(repo, path)
    syms = [sym for sym in java_ast.list_symbols(src) if sym["name"] == name]
    if not syms:
        avail = ", ".join(sym["name"] for sym in java_ast.list_symbols(src)
                          if sym["kind"] in ("method", "constructor"))
        return f"No symbol named '{name}' in {path}. Methods: {avail[:800]}"
    lines = src.splitlines()
    out = []
    for sym in syms:
        body = "\n".join(f"{sym['line_start'] + i:>5}\t{ln}"
                         for i, ln in enumerate(lines[sym['line_start'] - 1:sym['line_end']]))
        out.append(f"// {sym['kind']} {sym['name']} (lines {sym['line_start']}-{sym['line_end']})\n{body}")
    return "\n\n".join(out)


def search(repo: Path, query: str) -> str:
    try:
        out = subprocess.run(
            ["rg", "--line-number", "--no-heading", "--max-count", "20",
             "--max-columns", "200", query, "."],
            cwd=repo, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return "ERROR: ripgrep (rg) is not installed."
    if out.returncode not in (0, 1):
        return f"ERROR: {out.stderr.strip()}"
    return out.stdout.strip()[:6000] or f"No matches for '{query}'."


_NOISE_WORDS = {"file", "the", "a", "an", "class", "java"}


def find_file(repo: Path, name: str) -> str:
    """Find files by name, tolerant of loose phrasing."""
    import re
    try:
        out = subprocess.run(["rg", "--files"], cwd=repo,
                             capture_output=True, text=True, timeout=30)
        files = out.stdout.splitlines() if out.returncode == 0 else []
    except FileNotFoundError:
        files = [str(p.relative_to(repo)) for p in repo.rglob("*") if p.is_file()]
    files = [f for f in files
             if not any(part in _IGNORE for part in f.replace("\\", "/").split("/"))]
    raw = [t for t in re.split(r"[\s/_.-]+", name.lower()) if t]
    tokens = [t for t in raw if t not in _NOISE_WORDS] or raw or [name.lower()]
    matches = [f for f in files if name.lower() in f.lower()]
    if not matches:
        matches = [f for f in files if all(t in f.lower() for t in tokens)]
    if not matches:
        matches = [f for f in files if any(t in f.lower() for t in tokens)]
    return "\n".join(sorted(matches)[:50]) or f"No files matching '{name}'."


def write_file(repo: Path, path: str, content: str) -> str:
    p = _safe_path(repo, path)
    old = p.read_text(encoding="utf-8") if p.exists() else ""
    err = _check_java(path, content)
    if err:
        return err
    _show_diff(path, old, content)
    if not _confirm(f"Write {path}?"):
        return "User declined the write."
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    _mark_read(repo, path)  # writing means we know its current contents
    return f"{'Overwrote' if old else 'Created'} {path} ({len(content)} bytes)."


def edit_file(repo: Path, path: str, old_str: str, new_str: str) -> str:
    if not _is_read(repo, path):
        return f"ERROR: {path} must be read with read_file before it can be modified."
    p = _safe_path(repo, path)
    if not p.exists():
        p2, _rel, _msg = _resolve(repo, path)
        if p2 is None:
            return _msg
        p, path = p2, _rel
    if old_str == "":
        return ("ERROR: old_str is empty. edit_file REPLACES text and cannot insert. "
                "To add new code use replace_lines (read the file first for line numbers).")
    text = p.read_text(encoding="utf-8")
    matched, note = _locate(text, old_str)
    if matched is None:
        return (f"ERROR: old_str {note}. Re-read the file to copy the exact current text, "
                "or use replace_lines to edit by line number.")
    updated = text.replace(matched, new_str, 1)
    err = _check_java(path, updated)
    if err:
        return err
    _show_diff(path, text, updated)
    extra = f" ({note})" if note else ""
    if not _confirm(f"Apply edit to {path}{extra}?"):
        return "User declined the edit."
    p.write_text(updated, encoding="utf-8")
    return f"Edited {path}{(' — ' + note) if note else ''}."


def replace_lines(repo: Path, path: str, start_line: int, end_line: int, new_text: str) -> str:
    if not _is_read(repo, path):
        return f"ERROR: {path} must be read with read_file before it can be modified."
    p = _safe_path(repo, path)
    if not p.exists():
        p2, _rel, _msg = _resolve(repo, path)
        if p2 is None:
            return _msg
        p, path = p2, _rel
    original = p.read_text(encoding="utf-8")
    lines = original.splitlines()
    if start_line < 1 or end_line > len(lines) or start_line > end_line:
        return f"ERROR: invalid range {start_line}-{end_line}; file has {len(lines)} lines."
    updated_lines = lines[:start_line - 1] + new_text.splitlines() + lines[end_line:]
    updated = "\n".join(updated_lines) + ("\n" if original.endswith("\n") else "")
    err = _check_java(path, updated)
    if err:
        return err
    _show_diff(path, original, updated)
    if not _confirm(f"Replace lines {start_line}-{end_line} in {path}?"):
        return "User declined the edit."
    p.write_text(updated, encoding="utf-8")
    return f"Replaced lines {start_line}-{end_line} in {path}."


def apply_patch(repo: Path, path: str, old_text: str, new_text: str) -> str:
    """Preferred edit tool: replace an exact (or whitespace-fuzzy) unique block."""
    if not _is_read(repo, path):
        return f"ERROR: {path} must be read with read_file before it can be modified."
    p = _safe_path(repo, path)
    if not p.exists():
        p2, _rel, _msg = _resolve(repo, path)
        if p2 is None:
            return _msg
        p, path = p2, _rel
    if old_text == "":
        return ("ERROR: old_text is empty. apply_patch replaces existing text; "
                "to insert new code use replace_lines.")
    original = p.read_text(encoding="utf-8")
    matched, note = _locate(original, old_text)
    if matched is None:
        return (f"ERROR: patch context {note}. Re-read the file and copy the exact current "
                "text into old_text, or use replace_lines to edit by line number.")
    updated = original.replace(matched, new_text, 1)
    err = _check_java(path, updated)
    if err:
        return err
    _show_diff(path, original, updated)
    extra = f" ({note})" if note else ""
    if not _confirm(f"Apply patch to {path}{extra}?"):
        return "User declined the patch."
    p.write_text(updated, encoding="utf-8")
    return f"Patched {path}{(' — ' + note) if note else ''}."


# ----------------------------------------------------------------- compilation
def is_maven(repo: Path) -> bool:
    return (repo / "pom.xml").exists()


def _run_mvn(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["mvn", "-q", "-DskipTests", "compile"],
                          cwd=repo, capture_output=True, text=True, timeout=300)


def compile_project(repo: Path) -> str:
    """Manual compile (model-invoked). Auto-compile after edits is in the agent."""
    print(f"\n{_YELLOW}compile:{_RESET} mvn -q -DskipTests compile")
    if not _confirm("Allow compilation?"):
        return "User declined compilation."
    try:
        out = _run_mvn(repo)
    except subprocess.TimeoutExpired:
        return "ERROR: compilation timed out after 300s."
    except Exception as e:
        return f"ERROR: {e}"
    result = f"exit code: {out.returncode}\n"
    if out.stdout:
        result += f"stdout:\n{out.stdout[-8000:]}\n"
    if out.stderr:
        result += f"stderr:\n{out.stderr[-8000:]}\n"
    if out.returncode == 0:
        result += "\nCOMPILATION SUCCESSFUL"
    return result.strip()


def auto_compile(repo: Path):
    """Non-interactive compile for the agent to run after edits.
    Returns (ok: bool, output: str)."""
    print(f"\n{_YELLOW}auto-compile:{_RESET} mvn -q -DskipTests compile")
    try:
        out = _run_mvn(repo)
    except subprocess.TimeoutExpired:
        return False, "ERROR: compilation timed out after 300s."
    except Exception as e:
        return False, f"ERROR: {e}"
    body = (out.stdout or "") + "\n" + (out.stderr or "")
    return out.returncode == 0, body.strip()[-8000:]


def compile_signature(output: str) -> str:
    """A stable fingerprint of the compiler errors, to detect the model failing
    to make progress on the same error."""
    errs = [ln.strip() for ln in output.splitlines()
            if "ERROR" in ln or "error:" in ln]
    return "\n".join(errs[:5])


def run_command(repo: Path, command: str) -> str:
    print(f"\n{_YELLOW}run:{_RESET} {command}")
    if not _confirm("Allow this command?"):
        return "User declined the command."
    try:
        out = subprocess.run(command, shell=True, cwd=repo,
                             capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out after 120s."
    result = f"exit code: {out.returncode}\n"
    if out.stdout:
        result += f"stdout:\n{out.stdout[-4000:]}\n"
    if out.stderr:
        result += f"stderr:\n{out.stderr[-4000:]}\n"
    return result.strip()



# ===== Java structural tools =====

_INDEX_FILE = ".cdindex.sqlite"


def code_bundle(repo: Path, name: str) -> str:
    """Where a symbol is, who calls it, what it calls, and its annotations — from
    a prebuilt index, NO file reading and NO model tokens spent exploring. Builds
    the index on first use. Use this to understand a symbol before editing."""
    if not _JAVA_OK:
        return "ERROR: java_ast unavailable."
    from coder import codeindex
    db = str(repo / _INDEX_FILE)
    try:
        if not (repo / _INDEX_FILE).exists():
            codeindex.build_index(repo, db)
        return codeindex.bundle(db, name, repo)
    except Exception as e:
        return f"ERROR building/reading index: {e}"


def find_symbol(repo: Path, query: str) -> str:
    """Find a method/class by DESCRIPTION or intent, not exact name — e.g.
    'cron that deletes expired POs' -> pushDeletionIdstoKafka. Use this when
    find_file/search can't locate something by keyword. Semantic search over the
    code (needs an Ollama embedding model; builds embeddings on first use)."""
    if not _JAVA_OK:
        return "ERROR: java_ast unavailable."
    import os
    import sqlite3
    from coder import semindex
    host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    model = os.getenv("EMBED_MODEL", semindex.EMBED_MODEL_DEFAULT)
    db = str(repo / _INDEX_FILE)
    embed = lambda text: semindex.ollama_embed(text, model, host)
    try:
        need_build = True
        if (repo / _INDEX_FILE).exists():
            try:
                c = sqlite3.connect(db).execute("SELECT count(*) FROM embeddings").fetchone()[0]
                need_build = c == 0
            except sqlite3.OperationalError:
                need_build = True
        if need_build:
            print("  (building semantic index — one-time, needs Ollama embedding model)")
            semindex.build_embeddings(repo, db, embed)
        hits = semindex.semantic_search(db, query, embed)
    except Exception as e:
        return ("ERROR: semantic search needs an Ollama embedding model. Run "
                "`ollama pull nomic-embed-text` and ensure OLLAMA_HOST is reachable. " + str(e)[:120])
    # if not hits:
    #     return f"No semantic matches for '{query}'."
    # return "\n".join(f"{h['score']:.2f}  {h['kind']} {h['name']}  ({h['file']}:{h['line_start']}-{h['line_end']})"
    #                  for h in hits)
# AFTER
    if not hits:
        return f"No semantic matches for '{query}'."
    lines = []
    for i, h in enumerate(hits):
        lines.append(
            f"{h['score']:.2f}  {h['kind']} {h['name']}  "
            f"({h['file']}:{h['line_start']}-{h['line_end']})"
        )
        # inline the top hit's body so the model doesn't need another round trip
        if i == 0 and repo is not None:
            try:
                fpath = repo / h["file"]
                src_lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
                body = src_lines[h["line_start"] - 1 : h["line_end"]]
                if len(body) <= 60:          # don't inline giant classes
                    lines.append("  --- body ---")
                    lines.extend("  " + l for l in body)
                    lines.append("  --- end ---")
            except Exception:
                pass
    return "\n".join(lines)


def outline(repo: Path, path: str) -> str:
    """List classes/methods/fields (and switches) with line ranges. Find WHERE to
    add a method or case before editing. Counts as reading the file."""
    p = _safe_path(repo, path)
    if not p.exists():
        p2, _rel, _msg = _resolve(repo, path)
        if p2 is None:
            return _msg
        p, path = p2, _rel
    if not _JAVA_OK:
        return "ERROR: java_ast (tree-sitter) is not available."
    src = p.read_text(encoding="utf-8", errors="replace")
    _mark_read(repo, path)
    syms = java_ast.list_symbols(src)
    lines = [f"{x['line_start']:>4}-{x['line_end']:<4} {x['kind']:<12} {x['name']}" for x in syms]
    for sw in java_ast.list_switches(src):
        lines.append(f"{sw['line']:>4}      switch       switch({sw['discriminant']}) in {sw['method']}()")
    return "\n".join(lines) or "(no symbols found)"


def insert_in_class(repo: Path, path: str, class_name: str, code: str) -> str:
    """Add a method/field at the END of a named class (before its closing brace).
    Placement and indentation are computed for you."""
    if not _is_read(repo, path):
        return f"ERROR: {path} must be read (read_file/outline) before editing."
    p = _safe_path(repo, path)
    if not p.exists():
        p2, _rel, _msg = _resolve(repo, path)
        if p2 is None:
            return _msg
        p, path = p2, _rel
    if not _JAVA_OK:
        return "ERROR: java_ast unavailable."
    src = p.read_text(encoding="utf-8")
    off, ok, msg = java_ast.insertion_offset_in_type(src, class_name)
    if not ok:
        return f"ERROR: {msg}"
    body = "\n".join(("    " + ln if ln.strip() else ln) for ln in code.splitlines())
    updated = src[:off] + "\n" + body + "\n" + src[off:]
    err = _check_java(path, updated)
    if err:
        return err
    _show_diff(path, src, updated)
    if not _confirm(f"Insert into class {class_name} in {path}?"):
        return "User declined the insert."
    p.write_text(updated, encoding="utf-8")
    return f"Inserted code into class {class_name} in {path}."


def add_import(repo: Path, path: str, statement: str) -> str:
    """Add an import in the right place, skipping duplicates."""
    if not _is_read(repo, path):
        return f"ERROR: {path} must be read (read_file/outline) before editing."
    p = _safe_path(repo, path)
    if not p.exists():
        p2, _rel, _msg = _resolve(repo, path)
        if p2 is None:
            return _msg
        p, path = p2, _rel
    if not _JAVA_OK:
        return "ERROR: java_ast unavailable."
    stmt = statement.strip()
    if not stmt.startswith("import "):
        stmt = "import " + stmt
    if not stmt.endswith(";"):
        stmt += ";"
    fqcn = stmt[len("import "):].rstrip(";").replace("static ", "").strip()
    src = p.read_text(encoding="utf-8")
    if java_ast.has_import(src, fqcn):
        return f"Import for '{fqcn}' already present."
    off = java_ast.import_block_offset(src)
    updated = src[:off] + "\n" + stmt + src[off:]
    err = _check_java(path, updated)
    if err:
        return err
    _show_diff(path, src, updated)
    if not _confirm(f"Add '{stmt}' to {path}?"):
        return "User declined the import."
    p.write_text(updated, encoding="utf-8")
    return f"Added {stmt} to {path}."


def add_case(repo: Path, path: str, case_code: str,
             discriminant: str = None, method: str = None, index: int = None) -> str:
    """Add a case to an existing switch, before 'default', matching indentation.
    Use this for 'add a case' requests (NOT insert_in_class)."""
    if not _is_read(repo, path):
        return f"ERROR: {path} must be read (read_file/outline) before editing."
    p = _safe_path(repo, path)
    if not p.exists():
        p2, _rel, _msg = _resolve(repo, path)
        if p2 is None:
            return _msg
        p, path = p2, _rel
    if not _JAVA_OK:
        return "ERROR: java_ast unavailable."
    src = p.read_text(encoding="utf-8")
    off, indent, ok, msg = java_ast.case_insert_point(src, discriminant, method, index)
    if not ok:
        return f"ERROR: {msg}"
    lines = case_code.splitlines()
    nonblank = [ln for ln in lines if ln.strip()]
    common = min((len(ln) - len(ln.lstrip()) for ln in nonblank), default=0)
    block = "\n".join((indent + ln[common:] if ln.strip() else "") for ln in lines) + "\n"
    updated = src[:off] + block + src[off:]
    err = _check_java(path, updated)
    if err:
        return err
    _show_diff(path, src, updated)
    if not _confirm(f"Add case to {path}?"):
        return "User declined the case."
    p.write_text(updated, encoding="utf-8")
    return f"Added case to {path}."


def find_code(repo: Path, path: str, kind: str,
              in_method: str = None, in_class: str = None) -> str:
    """Locate constructs (else, catch, finally, if, for, while, try, switch,
    method, class, field, return, lambda) and get their line ranges to read/edit.
    Counts as reading the file."""
    p = _safe_path(repo, path)
    if not p.exists():
        p2, _rel, _msg = _resolve(repo, path)
        if p2 is None:
            return _msg
        p, path = p2, _rel
    if not _JAVA_OK:
        return "ERROR: java_ast unavailable."
    src = p.read_text(encoding="utf-8", errors="replace")
    _mark_read(repo, path)
    hits = java_ast.find_code(src, kind, in_method, in_class)
    if hits and "error" in hits[0]:
        return f"ERROR: {hits[0]['error']}"
    if not hits:
        scope = f" in {in_method or in_class}" if (in_method or in_class) else ""
        return f"No '{kind}' found{scope}."
    return "\n".join(f"lines {h['line_start']}-{h['line_end']}: {h['snippet']}" for h in hits)


_FUNCS = {
    "list_dir": list_dir, "read_file": read_file, "search": search,
    "find_file": find_file, "read_symbol": read_symbol,
    "outline": outline, "insert_in_class": insert_in_class,
    "add_import": add_import, "add_case": add_case, "find_code": find_code,
    "code_bundle": code_bundle, "find_symbol": find_symbol,
    "write_file": write_file, "edit_file": edit_file,
    "replace_lines": replace_lines, "apply_patch": apply_patch,
    "compile_project": compile_project, "run_command": run_command,
}

# Slim tool set: only the tools the model SEES. All 18 functions remain in _FUNCS
# (callable if the model emits a call via text recovery), but only these 11 are
# advertised as schemas. This cuts ~40% of per-request token overhead and fixes
# the Gemini hang / Groq 413 caused by 18 verbose schemas.
TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "list_dir",
        "description": "List files/folders in a directory.",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string"}}, "required": []}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a file. Pass line_start/line_end for a range.",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string"},
                                      "line_start": {"type": "integer"},
                                      "line_end": {"type": "integer"}},
                       "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "read_symbol",
        "description": "Read one method/class by name (avoids loading the whole file).",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string"}, "name": {"type": "string"}},
                       "required": ["path", "name"]}}},
    {"type": "function", "function": {
        "name": "outline",
        "description": "Show all classes/methods/switches with line ranges.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                       "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "find_code",
        "description": "Find a construct (if, else, catch, switch, method, etc.) with line range.",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string"}, "kind": {"type": "string"},
                                      "in_method": {"type": "string"}, "in_class": {"type": "string"}},
                       "required": ["path", "kind"]}}},
    {"type": "function", "function": {
        "name": "code_bundle",
        "description": "Index lookup: symbol location + callers + callees. No file read needed.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}},
                       "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "find_symbol",
        "description": "Semantic search by description (e.g. 'cron that deletes POs').",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                       "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "Replace an exact text block in a file. Read the file first; old_str must match.",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string"},
                                      "old_str": {"type": "string"},
                                      "new_str": {"type": "string"}},
                       "required": ["path", "old_str", "new_str"]}}},
    {"type": "function", "function": {
        "name": "add_case",
        "description": "Add a case to a switch. Pass case_code; optionally discriminant or method.",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string"}, "case_code": {"type": "string"},
                                      "discriminant": {"type": "string"}, "method": {"type": "string"}},
                       "required": ["path", "case_code"]}}},
    {"type": "function", "function": {
        "name": "find_file",
        "description": "Find files by name.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}},
                       "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "run_command",
        "description": "Run a shell command (user approves). For tests, git, linters.",
        "parameters": {"type": "object",
                       "properties": {"command": {"type": "string"}},
                       "required": ["command"]}}},
]


def run_tool(repo: Path, name: str, args: dict) -> str:
    fn = _FUNCS.get(name)
    if fn is None:
        return f"ERROR: unknown tool '{name}'."
    try:
        return fn(repo, **args)
    except TypeError as e:
        return f"ERROR: bad arguments for {name}: {e}"
    except Exception as e:
        return f"ERROR: {e}"