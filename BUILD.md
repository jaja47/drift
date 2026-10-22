# Drift — Build Steps

Execute in order. Each step ends with its **Done when** checks passing and a commit. Update
`HANDOVER.md` at the end of every step. **GATE** steps stop for the human — do not continue past
a gate until the human has answered.

Time budget: 5 days. The day markers are targets, not rules. If a day slips, use the **Cut line**
at the bottom rather than rushing the governance steps.

---

## Day 1 — foundations

### Step 1 · Scaffold

- `git init` in `~/Documents/drift`. Python 3.11+ in `.venv`. Check `deno --version` works; if
  not, stop and tell the human to install Deno.
- **Copy Warrant in:** copy `~/Documents/warrant` to `./warrant/`, excluding `.git/`,
  `ledger.jsonl` and any other local state. Do not modify the original repo, now or later.
  Write `warrant/VENDORED.md`: source path, source commit (`git -C ~/Documents/warrant rev-parse
  HEAD`), date copied, and a "Changes since copy" list (empty for now).
- Run `cd warrant && deno task test` — all 43 tests must pass in the copied location before
  anything else is built.
- `scripts/dev.sh`: generate a random `WARRANT_TOKEN` if `.env` has none, start Warrant
  (`WARRANT_STORE=memory` by default, `WARRANT_LEDGER=state/ledger.jsonl` when `--persist`),
  poll `GET /health` until up (fail after 10s), then run the given Drift command; `trap` to stop
  Warrant on exit, including Ctrl-C.
- `requirements.txt`: `duckdb`, `httpx`, `pyyaml`, `faker`, `anthropic`, `streamlit`, `pytest`.
- Layout:
  ```
  drift/
    connectors/  normalize.py  detect.sql  detect.py  classify.py  explain.py
    patterns.py  govern.py  target/  app.py  cli.py
  config/change_classes.yaml
  data/source/   state/   reports/   tests/   scripts/
  ```
- `Makefile` targets: `setup`, `data`, `test` (runs **both** pytest and `deno task test` in
  `warrant/`), `warrant` (Warrant alone, foreground), `run`, `app`, `demo`, `reset`. Every target
  that needs Warrant goes through `scripts/dev.sh`.
- `.gitignore`: `.venv`, `state/`, `reports/`, `.env`, `__pycache__`.
- `.env.example`: `WARRANT_URL=http://localhost:8000`, `WARRANT_TOKEN=`, `ANTHROPIC_API_KEY=`,
  `DRIFT_MODEL=claude-sonnet-5-5`, `DRIFT_PRINCIPAL=Murtaza Dibbo`.

**Done when:** `make setup && make test` runs (zero Python tests is fine; the 43 Warrant tests
pass); `make warrant` serves `/health` on port 8000 and stops cleanly on Ctrl-C.

### Step 2 · Synthetic data generator

`scripts/generate_data.py`, seeded (`seed=42`), writes everything listed in PLAN.md §2:
both exports, the crosswalk, the two code maps, and `planted.json`.

- ~200 employees, ~12 cost centres, ~25 managers, 4 locations, salaried and hourly mix.
- Plant exactly the 18 mismatches and 4 decoys in PLAN.md §2 — no more, no accidental extras.
- The five M3 cost-centre mismatches (and two of the M4 manager mismatches) share
  `Org_Effective_Date = 2026-10-01`. Nothing else in the data uses that date for an org change.
- One M1 termination date falls after a `PAYROLL_CUTOFF = 2026-10-09` constant.
- Use realistic but obviously fake values (`Faker`), no real companies or people.

**Done when:** `make data` is reproducible (same bytes twice); a test asserts `planted.json` has 18
mismatches and 4 decoys and that every planted row exists in the exports.

### Step 3 · Warrant policy (in `warrant/` inside this repo)

In `./warrant/` (the copy — never `~/Documents/warrant`):
- Add `HR_RECORD_CORRECTION` exactly as in PLAN.md §3 to `src/policy.ts`; register it in
  `POLICIES`.
- Add tests to `test/acceptance_test.ts`:
  - routine correction → `warrant_id` issued;
  - each of compensation / employment_status / identity → `202`, `approval_id`, no `warrant_id`;
  - one source only → `422`;
  - generic purpose (`"data cleanup"`-style filler) → `422`;
  - approver equal to principal → rejected.
- Update the README test count and the "two policies ship" sentence.

- Add the policy, tests and README edits to "Changes since copy" in `warrant/VENDORED.md`.

**Done when:** `make test` passes, including all 43 existing Warrant tests. One commit in the drift
repo.

---

## Day 2 — detection

### Step 4 · Connectors and normalisation

- `connectors/base.py`: `WorkerSource` and `PayrollSource` protocols. `CsvWorkdaySource`,
  `CsvAdpSource` implement them. Docstring says where a Workday RaaS report / ADP API reader
  would plug in.
- `normalize.py`: crosswalk join (unmatched rows kept, flagged for M5/M6), cost-centre and
  location maps, hourly → annual (`rate × 2080`, tolerance ±$1), email lower/trim, names compared
  on legal name only.

**Done when:** unit tests cover each normalisation rule, including all 4 decoys normalising to
equal.

### Step 5 · Detection

- `detect.sql`: DuckDB over the normalised tables, one CTE per mismatch type M1–M7, unioned into a
  `mismatches` result: `fingerprint, type, employee_ref, field, workday_value, adp_value,
  workday_source, adp_source, org_effective_date, comp_effective_date`.
- `fingerprint = sha256(type|employee_ref|field|workday_value|adp_value)`.
- `employee_ref` is the Workday `Employee_ID` (or `ADP:<Associate_ID>` for M6). Never a name.
- `*_source` strings look like `workday_workers.csv#Employee_ID=W10042`.
- `classify.py` + `config/change_classes.yaml`: field/type → `change_class`. Unknown field →
  `identity` (the safest class), never `routine`.

**Done when:**
- a test scores detection against `planted.json`: **18/18 found, 0 decoys flagged, 0 extras**;
- a test asserts no compensation, status, missing-record or name mismatch is classed `routine`.

### Step 6 · **GATE A** — does the detection look right?

Print the mismatch table (`python -m drift.cli detect`). Ask the human:
1. Do the 18 rows read like real HR problems?
2. Is anything classed wrongly?
3. Are the column names believable for Workday/ADP exports?

Stop here until answered.

---

## Day 3 — explanation and governance

### Step 7 · Explanations and patterns

- `explain.py`: for each mismatch, one call to the Claude API (`DRIFT_MODEL`) with **only**
  `type, field, employee_ref, both values (pay values replaced by "differs by N%"), effective
  dates, PAYROLL_CUTOFF`. Returns JSON `{explanation, likely_causes: [..max 3], suggested_owner}`.
  Cache by fingerprint in `state/explanations.json`. No API key → deterministic template per type.
  The prompt says the output is advisory and must not recommend bypassing review.
- `patterns.py`: group mismatches by shared `org_effective_date`, `comp_effective_date`, cost
  centre and "after cutoff". Any group of ≥3 becomes a pattern with a one-line diagnosis, e.g.
  *"7 org mismatches share the 2026-10-01 reorg date — the reorg likely did not reach ADP.
  Suggested owner: IT Application Engineering (integration)."*

**Done when:** tests prove the LLM payload contains no names, emails or pay figures; the reorg
pattern is found; template fallback works offline.

### Step 8 · Governance and apply

- `target/adp_target.py`: copies `adp_payroll.csv` → `state/adp_current.csv` on first run;
  `apply(mismatch)` edits the copy only. Source files are opened read-only.
- `govern.py`, per mismatch not already handled (check fingerprint in `state/drift.db`):
  - build the warrant request; `purpose` is **computed per record** and names the employee ref,
    field and reason — pay figures never appear in it;
  - `422` → status `blocked`, store violations, write nothing;
  - `202` → status `held`, store `approval_id` and `requested_of`, write nothing;
  - `200` → apply, then `/warrant/commit` with a result summary; on exception `/warrant/abort`;
  - connection error → stop the whole run, status `warrant_unreachable`, write nothing.
- `approve` flow: `cli.py approve <fingerprint> --as "<name>" --decision approved|rejected` posts
  `/approval/decide`; `cli.py sync` polls held approvals and applies + commits any that now have a
  `warrant_id`. Rejected → status `rejected`, nothing written.
- Approver per class from `config/change_classes.yaml` (e.g. compensation → "Priya Raman, Payroll
  Manager"). Fictional names only.
- `cli.py run` writes `reports/run_<ts>.md`: counts, patterns, held items, ledger `/verify` result.

**Done when** (integration tests against a real Warrant process started from `./warrant/` by a
pytest session fixture with `WARRANT_STORE=memory`, a random token and a free port, and stopped
afterwards):
- routine mismatches applied, each with a committed warrant;
- every sensitive mismatch held, `adp_current.csv` unchanged for those rows;
- approve one → applied after `sync`; reject one → never applied;
- re-running `run` creates no new warrants;
- Warrant stopped → zero writes, run reports `warrant_unreachable`;
- `/verify` returns intact after the full flow.

### Step 9 · **GATE B** — end-to-end check

Run `make demo` (Step 11 builds the polished version; a rough one is fine here). Ask the human to
watch the output and confirm the five demo beats in PLAN.md §1 all happen. Stop until answered.

---

## Day 4 — the page

### Step 10 · Review page

`app.py` (Streamlit), one page, written for a non-technical HR reader:
- top row: employees compared · mismatches · auto-fixed · waiting for approval · format
  differences ignored;
- **Patterns** box first — root causes before rows;
- table: employee ref, what differs (plain words), class, status, approver;
- expand a row: explanation, likely causes, sources, warrant id / approval id;
- held rows: "Decide as" name field + Approve / Reject (calls the same code as the CLI);
- footer: "Audit log: verified ✓ (N events)" from `/verify`, and an export link;
- banner: "Synthetic data. No real Workday or ADP system is connected."

**Done when:** the human can do the whole demo from the page without the terminal.

### Step 11 · Demo script

`make demo` (via `scripts/dev.sh`): resets state, starts Warrant from `./warrant/` (memory store,
random token), generates data, runs,
approves one held termination as the named approver, rejects nothing, syncs, prints `/verify`,
then opens the Streamlit page. Narrated output, like `warrant/demo/walkthrough.ts`.

### Step 12 · (Stretch) n8n workflow

Only if Steps 1–11 are done by end of Day 4.
- Add a tiny HTTP wrapper (`drift/api.py`, FastAPI or stdlib) exposing `GET /mismatches`,
  `POST /apply/{fingerprint}` (requires a `warrant_id` header, refuses without one).
- `n8n/drift-workflow.json`: Schedule → GET mismatches → loop → HTTP POST /warrant → IF status
  422 stop / 202 notify (a Set node standing in for Slack) / 200 → POST apply → POST commit.
- Run with `npx n8n`; screenshot the canvas into `docs/`.

**Done when:** importing the JSON into a fresh n8n and running it produces the same outcome as
`cli.py run`.

---

## Day 5 — docs and rehearsal

### Step 13 · Documentation

- `README.md`: the problem in one paragraph, the demo GIF/screenshot, how to run, the governance
  decisions (PLAN.md §4), **What this is not** (synthetic data, unauthenticated approver, ledger
  truncation, single process), and **What I'd validate with HR Ops first** (PLAN.md §5).
- `docs/architecture.md`: the diagram from PLAN.md §3.
- `HANDOVER.md` final state.

### Step 14 · Rehearsal support

- Record a 3-minute screen capture as a backup in case a live demo isn't possible.
- Final check: fresh clone → `make setup && make demo` works with no manual steps beyond the API
  key (and works without it, using templates). Only prerequisites: Python 3.11+ and Deno. No
  process is left running after the demo exits.

---

## Cut line (if time runs short, drop in this order)

1. Step 12 n8n — say "the adapter pattern for n8n is documented in Warrant"
2. LLM explanations — keep the templates
3. Streamlit page — demo from the CLI and the markdown report
4. Never cut: detection accuracy tests, the held-for-approval path, fail-closed, `/verify`.
