"""Semantic symbol search for the code index. Bridges conceptual queries
("delete po cron") to differently-named symbols (pushDeletionIdstoKafka) by
embedding each symbol's name+body and cosine-matching the query embedding.

No pip installs: embeddings come from your existing Ollama (pull an embedding
model once, e.g. `ollama pull nomic-embed-text`), vectors live in the same
SQLite index, and cosine similarity is pure Python.
"""

import json
import math
import re
import sqlite3
import urllib.request
from pathlib import Path

from coder import java_ast

EMBED_MODEL_DEFAULT = "nomic-embed-text"


def _camel_words(name: str) -> str:
    return " ".join(w.lower() for w in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", name))


def _cos(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def ollama_embed(text: str, model: str, host: str):
    """Embed via Ollama's /api/embeddings (stdlib urllib, no package)."""
    body = json.dumps({"model": model, "prompt": text}).encode("utf-8")
    req = urllib.request.Request(host.rstrip("/") + "/api/embeddings", data=body,
                                 method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))["embedding"]


def _symbol_text(name, kind, body):
    """What we embed: the human-words of the name + kind + a body snippet.
    Including the body is what lets 'delete po cron' reach a method whose body
    deletes POs and is @Scheduled, even though the name is pushDeletionIdstoKafka."""
    return f"{kind} {name} ({_camel_words(name)})\n{body[:600]}"


def build_embeddings(repo: Path, db_path: str, embed_fn,
                     workers: int = 200) -> int:
    """Embed every method/class and store vectors. embed_fn(text)->list[float].
    Idempotent: rebuilds the embeddings table.

    Uses a thread pool so Ollama gets concurrent requests — the model stays warm
    (no per-call load/unload overhead) and throughput is workers× faster than
    the sequential version."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    # step 1: collect all tasks (fast — just tree-sitter parsing, no Ollama)
    SKIP = {".git", "target", "build", ".idea", ".gradle", "out", "logs", "log"}
    tasks = []
    for path in repo.rglob("*.java"):
        if any(p in SKIP for p in path.parts):
            continue
        rel = str(path.relative_to(repo))
        src = path.read_text(encoding="utf-8", errors="replace")
        lines = src.splitlines()
        for s in java_ast.list_symbols(src):
            if s["kind"] not in ("method", "constructor", "class", "interface"):
                continue
            body = "\n".join(lines[s["line_start"] - 1:s["line_end"]])
            tasks.append((s["name"], s["kind"], rel,
                          s["line_start"], s["line_end"],
                          _symbol_text(s["name"], s["kind"], body)))

    total = len(tasks)
    print(f"[INDEX] {total} symbols found — embedding with {workers} workers")

    # step 2: embed concurrently
    def _embed(task):
        name, kind, rel, ls, le, text = task
        try:
            return (name, kind, rel, ls, le, json.dumps(embed_fn(text)), None)
        except Exception as e:
            return (name, kind, rel, ls, le, None, str(e))

    done_count = 0
    lock = threading.Lock()
    rows = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_embed, t): t for t in tasks}
        for fut in as_completed(futs):
            name, kind, rel, ls, le, vec, err = fut.result()
            with lock:
                done_count += 1
                if done_count % 25 == 0 or done_count == total:
                    print(f"[INDEX] {done_count}/{total} embedded")
            if vec:
                rows.append((name, kind, rel, ls, le, vec))
            else:
                print(f"[WARN]  skip {name}: {err}")

    # step 3: single bulk insert (much faster than row-by-row)
    db = sqlite3.connect(db_path)
    db.execute("DROP TABLE IF EXISTS embeddings")
    db.execute("CREATE TABLE embeddings(name TEXT, kind TEXT, file TEXT, "
               "line_start INT, line_end INT, vec TEXT)")
    db.executemany("INSERT INTO embeddings VALUES (?,?,?,?,?,?)", rows)
    db.commit()
    db.close()
    print(f"[INDEX] Done: {len(rows)}/{total} symbols indexed.")
    return len(rows)


def semantic_search(db_path: str, query: str, embed_fn, top: int = 15):
    db = sqlite3.connect(db_path)
    rows = db.execute("SELECT name, kind, file, line_start, line_end, vec FROM embeddings").fetchall()
    db.close()
    if not rows:
        return []
    qv = embed_fn(query)
    scored = []
    for name, kind, file, ls, le, vec in rows:
        scored.append((_cos(qv, json.loads(vec)), name, kind, file, ls, le))
    scored.sort(reverse=True)
    return [{"score": round(sc, 3), "name": n, "kind": k, "file": f, "line_start": ls, "line_end": le}
            for sc, n, k, f, ls, le in scored[:top]]