"""Java structural layer (tree-sitter). Syntax checking + symbol finding +
insertion-point location. No JVM required; parses partially-broken files."""

import tree_sitter_java as _tsjava
from tree_sitter import Language, Parser

_JAVA = Language(_tsjava.language())
_PARSER = Parser(_JAVA)


def _parse(src: str):
    return _PARSER.parse(src.encode("utf-8"))


def syntax_check(src: str):
    """Return (ok, message). ok=False if the parse tree has ERROR/MISSING nodes."""
    tree = _parse(src)
    problems = []
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.is_error or n.type == "ERROR":
            problems.append((n.start_point[0] + 1, "syntax error"))
        elif n.is_missing:
            problems.append((n.start_point[0] + 1, f"missing '{n.type}'"))
        else:
            stack.extend(n.children)
    if not problems:
        return True, "ok"
    problems.sort()
    lines = "\n".join(f"  line {ln}: {msg}" for ln, msg in problems[:10])
    return False, f"{len(problems)} syntax problem(s):\n{lines}"


_DECL = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
    "method_declaration": "method",
    "constructor_declaration": "constructor",
    "field_declaration": "field",
}


def _name_of(node, src_bytes):
    n = node.child_by_field_name("name")
    if n is not None:
        return src_bytes[n.start_byte:n.end_byte].decode("utf-8", "replace")
    if node.type == "field_declaration":
        for c in node.children:
            if c.type == "variable_declarator":
                vn = c.child_by_field_name("name")
                if vn is not None:
                    return src_bytes[vn.start_byte:vn.end_byte].decode("utf-8", "replace")
    return "?"


def list_symbols(src: str):
    """Flat list of declarations with line ranges, e.g.
    [{'kind':'class','name':'Foo','line_start':3,'line_end':40}, ...]"""
    src_bytes = src.encode("utf-8")
    tree = _parse(src_bytes.decode("utf-8"))
    out, stack = [], [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type in _DECL:
            out.append({
                "kind": _DECL[n.type],
                "name": _name_of(n, src_bytes),
                "line_start": n.start_point[0] + 1,
                "line_end": n.end_point[0] + 1,
            })
        stack.extend(n.children)
    out.sort(key=lambda d: d["line_start"])
    return out


def _find_type_body(src_bytes, type_name):
    """Return the body node ({...}) of a class/interface/enum/record by name."""
    tree = _parse(src_bytes.decode("utf-8"))
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type in ("class_declaration", "interface_declaration",
                      "enum_declaration", "record_declaration"):
            if _name_of(n, src_bytes) == type_name:
                return n.child_by_field_name("body")
        stack.extend(n.children)
    return None


def insertion_offset_in_type(src: str, type_name: str):
    """Byte offset just before the closing '}' of the named type's body, plus
    the column indent to use. Returns (offset, ok, msg)."""
    src_bytes = src.encode("utf-8")
    body = _find_type_body(src_bytes, type_name)
    if body is None:
        return None, False, f"type '{type_name}' not found"
    # body spans { ... }; insert right before the final '}'
    close = body.end_byte - 1  # the '}'
    return close, True, "ok"


def import_block_offset(src: str):
    """Byte offset where a new import line should go (after the last existing
    import, else after the package statement, else file start)."""
    src_bytes = src.encode("utf-8")
    tree = _parse(src_bytes.decode("utf-8"))
    last_import_end = None
    package_end = None
    for n in tree.root_node.children:
        if n.type == "import_declaration":
            last_import_end = n.end_byte
        elif n.type == "package_declaration":
            package_end = n.end_byte
    if last_import_end is not None:
        return last_import_end
    if package_end is not None:
        return package_end
    return 0


def has_import(src: str, fqcn: str) -> bool:
    src_bytes = src.encode("utf-8")
    tree = _parse(src_bytes.decode("utf-8"))
    for n in tree.root_node.children:
        if n.type == "import_declaration":
            txt = src_bytes[n.start_byte:n.end_byte].decode("utf-8", "replace")
            if fqcn in txt:
                return True
    return False


# ------------------------------------------------------------ switch / cases
def _enclosing_method(node, src_bytes):
    n = node.parent
    while n is not None:
        if n.type in ("method_declaration", "constructor_declaration"):
            return _name_of(n, src_bytes)
        n = n.parent
    return None


def _all_switches(tree):
    out, stack = [], [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type == "switch_expression":
            out.append(n)
        stack.extend(n.children)
    out.sort(key=lambda n: n.start_byte)
    return out


def _disc(node, src_bytes):
    cond = node.child_by_field_name("condition")
    if cond is None:
        return "?"
    return src_bytes[cond.start_byte:cond.end_byte].decode("utf-8", "replace").strip("() \t")


def list_switches(src: str):
    """[{index, discriminant, method, line}] for every switch in the file."""
    src_bytes = src.encode("utf-8")
    tree = _parse(src_bytes.decode("utf-8"))
    res = []
    for i, n in enumerate(_all_switches(tree)):
        res.append({"index": i,
                    "discriminant": _disc(n, src_bytes),
                    "method": _enclosing_method(n, src_bytes),
                    "line": n.start_point[0] + 1})
    return res


def case_insert_point(src: str, discriminant=None, method=None, index=None):
    """Pick a switch and return (line_start_offset, label_indent, ok, msg).
    line_start_offset is the byte offset at the start of the line to insert
    before (the 'default:' group if present, else the switch's closing brace).
    label_indent is the whitespace each case label should start with."""
    src_bytes = src.encode("utf-8")
    tree = _parse(src_bytes.decode("utf-8"))
    switches = _all_switches(tree)
    if not switches:
        return None, "", False, "no switch statement found in this file"

    cand = list(range(len(switches)))
    if discriminant is not None:
        d = discriminant.strip()
        cand = [i for i in cand if _disc(switches[i], src_bytes) == d]
    if method is not None:
        cand = [i for i in cand if _enclosing_method(switches[i], src_bytes) == method]

    if len(cand) == 1:
        chosen = switches[cand[0]]
    elif discriminant is None and method is None and len(switches) == 1:
        chosen = switches[0]
    elif index is not None and 0 <= index < len(switches):
        chosen = switches[index]
    else:
        listing = "; ".join(
            f"[{i}] switch({_disc(switches[i], src_bytes)}) in {_enclosing_method(switches[i], src_bytes)}()"
            for i in range(len(switches)))
        return None, "", False, f"ambiguous/not found — pass discriminant or method. switches: {listing}"

    body = chosen.child_by_field_name("body")
    groups = [c for c in body.children
              if c.type in ("switch_block_statement_group", "switch_rule")]
    label_indent = " " * (groups[0].start_point[1] if groups else body.start_point[1] + 4)

    target = None
    for g in groups:
        lbl = next((c for c in g.children if c.type == "switch_label"), None)
        if lbl is not None and src_bytes[lbl.start_byte:lbl.end_byte].decode().strip().startswith("default"):
            target = g
            break
    if target is None:
        target_byte = body.end_byte - 1  # the closing '}'
    else:
        target_byte = target.start_byte

    # back up to the start of the line the target sits on
    line_start = src_bytes.rfind(b"\n", 0, target_byte) + 1
    return line_start, label_indent, True, "ok"


# ------------------------------------------------------- general structural find
# A small, model-friendly vocabulary mapped to tree-sitter node types. This is
# what lets one tool answer "find the else block in method X" without a bespoke
# tool per construct. 'else'/'finally' are fields, handled specially below.
_KIND_NODES = {
    "method": ("method_declaration",),
    "constructor": ("constructor_declaration",),
    "class": ("class_declaration", "interface_declaration",
              "enum_declaration", "record_declaration"),
    "field": ("field_declaration",),
    "if": ("if_statement",),
    "for": ("for_statement", "enhanced_for_statement"),
    "while": ("while_statement", "do_statement"),
    "try": ("try_statement",),
    "catch": ("catch_clause",),
    "switch": ("switch_expression",),
    "return": ("return_statement",),
    "finally": ("finally_clause",),
    "lambda": ("lambda_expression",),
}


def _within(node, src_bytes, in_method, in_class):
    if in_method is not None:
        n, ok = node.parent, False
        while n is not None:
            if n.type in ("method_declaration", "constructor_declaration") \
                    and _name_of(n, src_bytes) == in_method:
                ok = True
                break
            n = n.parent
        if not ok:
            return False
    if in_class is not None:
        n, ok = node.parent, False
        while n is not None:
            if n.type in _KIND_NODES["class"] and _name_of(n, src_bytes) == in_class:
                ok = True
                break
            n = n.parent
        if not ok:
            return False
    return True


def _span(node, src_bytes):
    snippet = src_bytes[node.start_byte:node.end_byte].decode("utf-8", "replace")
    first = snippet.splitlines()[0] if snippet.splitlines() else ""
    return {"line_start": node.start_point[0] + 1,
            "line_end": node.end_point[0] + 1,
            "snippet": first[:70]}


def find_code(src: str, kind: str, in_method: str = None, in_class: str = None):
    """Locate constructs by kind, optionally scoped to a method/class. Returns a
    list of {line_start, line_end, snippet}. Editing is then done with
    replace_lines/apply_patch on the returned range — no per-construct tool."""
    kind = kind.lower().strip()
    src_bytes = src.encode("utf-8")
    tree = _parse(src_bytes.decode("utf-8"))

    # 'else' and 'finally' are FIELDS of another node, not their own node type.
    special = {"else": ("if_statement", "alternative")}
    if kind in special:
        host_type, field = special[kind]
        results = []
        stack = [tree.root_node]
        while stack:
            n = stack.pop()
            if n.type == host_type:
                branch = n.child_by_field_name(field)
                if branch is not None and _within(n, src_bytes, in_method, in_class):
                    results.append(_span(branch, src_bytes))
            stack.extend(n.children)
        results.sort(key=lambda d: d["line_start"])
        return results

    node_types = _KIND_NODES.get(kind)
    if node_types is None:
        return [{"error": f"unknown kind '{kind}'. known: "
                          + ", ".join(sorted(list(_KIND_NODES) + ['else']))}]
    results, stack = [], [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type in node_types and _within(n, src_bytes, in_method, in_class):
            results.append(_span(n, src_bytes))
        stack.extend(n.children)
    results.sort(key=lambda d: d["line_start"])
    return results