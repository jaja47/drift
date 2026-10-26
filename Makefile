# Every target that needs Warrant goes through scripts/dev.sh, which starts it from ./warrant/,
# waits for /health, runs the command and stops Warrant on exit.
#
# run / approve / sync / show / app each start their own Warrant, so they use --persist: the ledger
# (state/ledger.jsonl) then lives exactly as long as Drift's own state (state/drift.db), and a
# request held by `run` still exists when `approve` decides it. `make reset` clears both.
# `make demo` runs its whole flow inside one Warrant, on the in-memory store and its own state.

PYTHON ?= python3
VENV   := .venv
PY     := $(VENV)/bin/python
DEV    := scripts/dev.sh

.PHONY: setup data test warrant run approve sync show app demo reset

setup:
	@$(PYTHON) -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11+ is required (found " + sys.version.split()[0] + ")")'
	@command -v deno >/dev/null || { echo "Deno is required: https://deno.com" >&2; exit 1; }
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -r requirements.txt
	cd warrant && deno install

data:
	$(PY) -m scripts.generate_data

# Python tests, then Warrant's full suite. Neither needs a running Warrant: the integration
# tests start their own on a free port (tests/conftest.py).
test:
	$(PY) -m pytest -q
	cd warrant && deno task test

warrant:
	$(DEV)

run:
	$(DEV) --persist $(PY) -m drift.cli run

# make approve REF=W10026 AS="Owen Castellanos" [DECISION=rejected] [NOTE="..."]
DECISION ?= approved
approve:
	@test -n "$(REF)" -a -n "$(AS)" || { echo 'usage: make approve REF=<employee ref or fingerprint> AS="<approver name>" [DECISION=approved|rejected] [NOTE="..."]' >&2; exit 2; }
	$(DEV) --persist $(PY) -m drift.cli approve "$(REF)" --as "$(AS)" --decision "$(DECISION)" $(if $(NOTE),--note "$(NOTE)")

sync:
	$(DEV) --persist $(PY) -m drift.cli sync

# make show REF=W10015 — one correction as Warrant holds it (or a wrt_… / apr_… id)
show:
	@test -n "$(REF)" || { echo 'usage: make show REF=<employee ref, fingerprint, wrt_… or apr_… id>' >&2; exit 2; }
	$(DEV) --persist $(PY) -m drift.cli show "$(REF)"

app:
	$(DEV) --persist $(PY) -m streamlit run drift/app.py

# The demo runs in one Warrant lifetime: fresh data, an in-memory ledger, a fresh random token
# and state of its own (state/demo, reports/demo — the state `run` and `app` keep is never
# touched). The narration pauses before each beat; then the review page opens on the same run,
# until Ctrl-C stops the page and Warrant together.
DEMO_ENV := DRIFT_STATE_DIR=state/demo DRIFT_REPORTS_DIR=reports/demo
demo:
	rm -rf state/demo reports/demo
	$(PY) -m scripts.generate_data
	WARRANT_TOKEN=$$(openssl rand -hex 32) $(DEMO_ENV) $(DEV) sh -c '\
	  $(PY) -m drift.cli demo --pause && \
	  printf "\n  Opening the review page on this run: http://localhost:8501\n  Ctrl-C stops the page and Warrant.\n\n" && \
	  exec $(PY) -m streamlit run drift/app.py' || test $$? -eq 130  # Ctrl-C is how it ends

# Generated state only. data/source/ and warrant/ are never touched here.
reset:
	rm -rf state reports
