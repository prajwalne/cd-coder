"""Offline code index for Java repos. DETERMINISTIC backbone only — symbols,
call graph, and Spring wiring, extracted with tree-sitter and stored in SQLite.
No LLM. The online agent queries bundle() instead of spending model calls to
explore the codebase. LLM summaries/embeddings are a separate enrichment layer
that can be added on top of this table later."""

import sqlite3
from pathlib import Path

import tree_sitter_java as _tsjava
from tree_sitter import Language, Parser

from coder import java_ast, resolver

_JAVA = Language(_tsjava.language())
_PARSER = Parser(_JAVA)
_DECL = ("class_declaration", "interface_declaration", "enum_declaration",
         "record_declaration", "method_declaration", "constructor_declaration",
         "field_declaration")


def _name(node, b):
    n = node.child_by_field_name("name")
    return b[n.start_byte:n.end_byte].decode("utf-8", "replace") if n else None


def _annotations_of(decl, b):
    out = []
    mods = next((c for c in decl.children if c.type == "modifiers"), None)
    if mods:
        for c in mods.children:
            if c.type in ("annotation", "marker_annotation"):
                out.append(_name(c, b) or b[c.start_byte:c.end_byte].decode()[:40])
    return out


def _enclosing_decl_name(node, b):
    n = node.parent
    while n is not None:
        if n.type in ("method_declaration", "constructor_declaration"):
            return _name(n, b)
        n = n.parent
    return None


def build_index(repo: Path, db_path: str) -> dict:
    db = sqlite3.connect(db_path)
    db.executescript("""
        DROP TABLE IF EXISTS symbols; DROP TABLE IF EXISTS calls; DROP TABLE IF EXISTS annotations;
        CREATE TABLE symbols(file TEXT, name TEXT, kind TEXT, line_start INT, line_end INT);
        CREATE TABLE calls(file TEXT, caller TEXT, callee TEXT, receiver_type TEXT, resolved INT);
        CREATE TABLE annotations(file TEXT, symbol TEXT, annotation TEXT);
        CREATE INDEX i_sym ON symbols(name);
        CREATE INDEX i_callee ON calls(callee);
        CREATE INDEX i_caller ON calls(caller);
    """)
    stats = {"files": 0, "symbols": 0, "calls": 0, "annotations": 0}
    for path in repo.rglob("*.java"):
        if any(p in (".git", "target", "build") for p in path.parts):
            continue
        rel = str(path.relative_to(repo))
        src = path.read_text(encoding="utf-8", errors="replace")
        b = src.encode("utf-8")
        tree = _PARSER.parse(b)
        stats["files"] += 1

        for sym in java_ast.list_symbols(src):
            db.execute("INSERT INTO symbols VALUES (?,?,?,?,?)",
                       (rel, sym["name"], sym["kind"], sym["line_start"], sym["line_end"]))
            stats["symbols"] += 1

        stack = [tree.root_node]
        while stack:
            n = stack.pop()
            if n.type in _DECL:
                nm = _name(n, b)
                for ann in _annotations_of(n, b):
                    db.execute("INSERT INTO annotations VALUES (?,?,?)", (rel, nm, ann))
                    stats["annotations"] += 1
            stack.extend(n.children)

    # precise, type-aware call edges from the resolver (not name-based)
    for e in resolver.resolve_calls(repo):
        db.execute("INSERT INTO calls VALUES (?,?,?,?,?)",
                   (e["file"], e["caller"], e["callee"], e["receiver_type"], int(e["resolved"])))
        stats["calls"] += 1
    db.commit()
    db.close()
    return stats


def bundle(db_path: str, name: str, repo: Path = None) -> str:
    """The context bundle the online agent feeds the model: where a symbol is,
    who calls it, what it calls, and its Spring annotations — all from the index,
    zero LLM calls."""
    db = sqlite3.connect(db_path)
    sym = db.execute("SELECT file, kind, line_start, line_end FROM symbols WHERE name=?",
                     (name,)).fetchall()
    if not sym:
        return f"'{name}' not in index."
    out = [f"# {name}"]
    for file, kind, ls, le in sym:
        out.append(f"  {kind} in {file}:{ls}-{le}")
        anns = db.execute("SELECT annotation FROM annotations WHERE symbol=? AND file=?",
                          (name, file)).fetchall()
        if anns:
            out.append("  annotations: " + ", ".join("@" + a[0] for a in anns))
    callers = db.execute("SELECT DISTINCT caller, file FROM calls WHERE callee=? AND caller IS NOT NULL",
                         (name,)).fetchall()
    callees = db.execute("SELECT DISTINCT callee, receiver_type, resolved FROM calls WHERE caller=?",
                         (name,)).fetchall()
    if callers:
        out.append("  called by: " + ", ".join(f"{c}() [{f}]" for c, f in callers))
    if callees:
        out.append("  calls: " + ", ".join(
            (f"{rt}.{c}()" if (res and rt) else f"{c}()") for c, rt, res in callees))
    db.close()
    return "\n".join(out)