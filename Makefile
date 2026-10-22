# Every target that needs Warrant goes through scripts/dev.sh, which starts it from ./warrant/,
# waits for /health, runs the command and stops Warrant on exit.

PYTHON ?= python3
VENV   := .venv
PY     := $(VENV)/bin/python
DEV    := scripts/dev.sh

.PHONY: setup data test warrant run app demo reset

setup:
	@$(PYTHON) -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11+ is required (found " + sys.version.split()[0] + ")")'
	@command -v deno >/dev/null || { echo "Deno is required: https://deno.com" >&2; exit 1; }
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -r requirements.txt
	cd warrant && deno install

data:
	$(PY) scripts/generate_data.py

# Python tests, then Warrant's full suite. Neither needs a running Warrant: the integration
# tests (Step 8) start their own on a free port.
test:
	$(PY) -m pytest -q tests
	cd warrant && deno task test

warrant:
	$(DEV)

run:
	$(DEV) $(PY) -m drift.cli run

app:
	$(DEV) $(PY) -m streamlit run drift/app.py

demo: reset
	$(DEV) $(PY) -m drift.cli demo

# Generated state only. data/source/ and warrant/ are never touched here.
reset:
	rm -rf state reports
