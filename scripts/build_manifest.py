"""Write web/py_manifest.json: the engine files the in-browser demo loads."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
files = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "expansion_signals").glob("*.py"))
(ROOT / "web" / "py_manifest.json").write_text(json.dumps({"files": files}, indent=1) + "\n")
print(f"{len(files)} files")
