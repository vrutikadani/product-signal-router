"""Write web/py_manifest.json: the engine files the in-browser demo loads."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# sqlite_store.py is not loaded: the demo uses the in-memory store, and Pyodide
# ships sqlite3 as a separate package.
SKIP = {"sqlite_store.py"}
files = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "product_signal_router").glob("*.py") if p.name not in SKIP)
(ROOT / "web" / "py_manifest.json").write_text(json.dumps({"files": files}, indent=1) + "\n")
print(f"{len(files)} files")
