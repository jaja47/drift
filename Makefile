# Every target that needs Warrant goes through scripts/dev.sh, which starts it from ./warrant/,
# waits for /health, runs the command and stops Warrant on exit.
#
# run / approve / sync / show / app each start their own Warrant, so they use --persist: the ledger
# (state/ledger.jsonl) then lives exactly as long as Drift's own state (state/drift.db), and a
# request held by `run` still exists when `approve` decides it. `make reset` clears both.
# `make demo` runs its whole flow inside one Warrant, on the in-memory store.

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

demo: reset
	$(DEV) $(PY) -m drift.cli demo

# Generated state only. data/source/ and warrant/ are never touched here.
reset:
	rm -rf state reports
