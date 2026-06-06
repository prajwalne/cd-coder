"""Preflight checks. Run with:  python main.py --check
Tells the user exactly what's missing before they hit a confusing runtime error."""

import os
import shutil
import sys

OK, WARN, BAD = "\033[32m✓\033[0m", "\033[33m!\033[0m", "\033[31m✗\033[0m"


def _line(sym, msg):
    print(f"  {sym} {msg}")


def doctor() -> bool:
    import config
    print("\ncd-coder preflight\n" + "-" * 40)
    ok = True

    # Python
    v = sys.version_info
    if v >= (3, 9):
        _line(OK, f"Python {v.major}.{v.minor}")
    else:
        _line(BAD, f"Python {v.major}.{v.minor} — need 3.9+"); ok = False

    # Python deps
    for mod, why in [("ollama", "Ollama backend"), ("groq", "Groq backend"),
                     ("dotenv", "reads .env"), ("tree_sitter", "Java syntax/structure"),
                     ("tree_sitter_java", "Java grammar")]:
        try:
            __import__(mod)
            _line(OK, f"python package '{mod}'")
        except ImportError:
            sym = WARN if mod in ("ollama", "tree_sitter", "tree_sitter_java") else BAD
            _line(sym, f"missing '{mod}' ({why}) — run: pip install -r requirements.txt")
            if sym == BAD:
                ok = False

    # ripgrep
    if shutil.which("rg"):
        _line(OK, "ripgrep (rg)")
    else:
        _line(WARN, "ripgrep (rg) not found — search/find_file degraded. Install: https://github.com/BurntSushi/ripgrep#installation")

    # Java toolchain (only needed for compile features)
    _line(OK if shutil.which("javac") else WARN, "javac (JDK)" + ("" if shutil.which("javac") else " — only needed for Java compile"))
    _line(OK if shutil.which("mvn") else WARN, "mvn (Maven)" + ("" if shutil.which("mvn") else " — only needed for compile_project"))

    # Backend reachability
    print("-" * 40)
    print(f"  backend: {config.PROVIDER}  model: {config.active_model()}")
    if config.PROVIDER == "groq":
        if os.getenv("GROQ_API_KEY"):
            _line(OK, "GROQ_API_KEY is set")
        else:
            _line(BAD, "GROQ_API_KEY not set — get one free at https://console.groq.com/keys, put it in .env"); ok = False
    else:
        try:
            import urllib.request
            urllib.request.urlopen(config.OLLAMA_HOST, timeout=3)
            _line(OK, f"Ollama reachable at {config.OLLAMA_HOST}")
        except Exception:
            _line(BAD, f"Ollama not reachable at {config.OLLAMA_HOST} — is `ollama serve` running? pulled the model?"); ok = False

    print("-" * 40)
    print(("All set — run:  python main.py" if ok else "Fix the ✗ items above, then re-run --check") + "\n")
    return ok


if __name__ == "__main__":
    sys.exit(0 if doctor() else 1)