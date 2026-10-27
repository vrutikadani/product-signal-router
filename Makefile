PY ?= python3

.PHONY: test eval demo manifest check

test:
	$(PY) -m pytest -q

eval:            ## regenerate evals/REPORT.md and evals/report.json
	$(PY) evals/run_eval.py

manifest:        ## list of engine files the in-browser demo loads
	$(PY) scripts/build_manifest.py

demo: manifest   ## serve the demo on http://localhost:8000/web/
	$(PY) -m http.server 8000

check: test
	$(PY) evals/run_eval.py --check
