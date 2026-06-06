"""Type-aware call resolution for Java. Removes the name-based ambiguity by
resolving `receiver.method()` against the receiver's declared type. Pure
tree-sitter (no JVM). Resolves the common cases — field/param/local receivers,
this/implicit — and marks the rest unresolved instead of guessing."""

import tree_sitter_java as _tsjava
from tree_sitter import Language, Parser

_JAVA = Language(_tsjava.language())
_P = Parser(_JAVA)


def _txt(n, b):
    return b[n.start_byte:n.end_byte].decode("utf-8", "replace")


def _name(n, b):
    x = n.child_by_field_name("name")
    return _txt(x, b) if x else None


def _type_name(type_node, b):
    """Simple type name, generics/array stripped: List<Offer> -> List."""
    if type_node is None:
        return None
    t = _txt(type_node, b)
    return t.split("<")[0].split("[")[0].strip()


def _field_or_var_type(decl, b):
    """(name -> type) pairs from a field_declaration / local_variable_declaration /
    formal_parameter."""
    out = {}
    tn = _type_name(decl.child_by_field_name("type"), b)
    if decl.type == "formal_parameter":
        nm = _name(decl, b)
        if nm and tn:
            out[nm] = tn
        return out
    for c in decl.children:
        if c.type == "variable_declarator":
            nm = _name(c, b)
            if nm and tn:
                out[nm] = tn
    return out


def build_class_methods(repo):
    """Global map {class_name: set(method_names)} so a field of type BaseApi
    resolves to BaseApi's methods even from another file."""
    cm = {}
    for path in repo.rglob("*.java"):
        if any(p in (".git", "target", "build") for p in path.parts):
            continue
        b = path.read_text(encoding="utf-8", errors="replace").encode()
        t = _P.parse(b)
        stack = [(t.root_node, None)]
        while stack:
            n, cls = stack.pop()
            if n.type in ("class_declaration", "interface_declaration",
                          "enum_declaration", "record_declaration"):
                cls = _name(n, b)
                cm.setdefault(cls, set())
            elif n.type == "method_declaration" and cls:
                m = _name(n, b)
                if m:
                    cm[cls].add(m)
            for c in n.children:
                stack.append((c, cls))
    return cm


def resolve_calls(repo):
    """Yield dicts: {file, caller, callee, receiver_type, resolved}.
    resolved=True means we know the exact owning type; False = couldn't (kept,
    but flagged, never silently name-matched)."""
    class_methods = build_class_methods(repo)
    edges = []
    for path in repo.rglob("*.java"):
        if any(p in (".git", "target", "build") for p in path.parts):
            continue
        rel = str(path.relative_to(repo))
        b = path.read_text(encoding="utf-8", errors="replace").encode()
        tree = _P.parse(b)

        def walk(n, cls, fields, method, locals_):
            if n.type in ("class_declaration", "interface_declaration",
                          "enum_declaration", "record_declaration"):
                cls = _name(n, b)
                fields = {}
                body = n.child_by_field_name("body")
                if body:
                    for c in body.children:
                        if c.type == "field_declaration":
                            fields.update(_field_or_var_type(c, b))
            if n.type in ("method_declaration", "constructor_declaration"):
                method = _name(n, b)
                locals_ = {}
                params = n.child_by_field_name("parameters")
                if params:
                    for p in params.children:
                        if p.type == "formal_parameter":
                            locals_.update(_field_or_var_type(p, b))
            if n.type == "local_variable_declaration":
                locals_.update(_field_or_var_type(n, b))

            if n.type == "method_invocation":
                callee = _name(n, b)
                obj = n.child_by_field_name("object")
                rtype = None
                if obj is None:
                    rtype = cls                                  # implicit this
                elif obj.type == "this":
                    rtype = cls
                elif obj.type == "identifier":
                    key = _txt(obj, b)
                    rtype = locals_.get(key) or fields.get(key)  # var/param, then field
                resolved = bool(rtype and callee in class_methods.get(rtype, set()))
                if callee:
                    edges.append({"file": rel, "caller": method, "callee": callee,
                                  "receiver_type": rtype, "resolved": resolved})
            for c in n.children:
                walk(c, cls, fields, method, locals_)

        walk(tree.root_node, None, {}, None, {})
    return edges