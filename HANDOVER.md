# HANDOVER — Drift

> **Rule:** update this file at the end of every step.

## Where the build stands

| | Step | Day | State |
|---|---|---|---|
| ✅ | 1 · Scaffold | 1 | done 2026-10-21 |
| ✅ | 2 · Synthetic data generator | 1 | done 2026-10-24 |
| ✅ | 3 · Warrant policy | 1 | done 2026-10-24 |
| ⬜ | 4 · Connectors + normalisation | 2 | |
| ⬜ | 5 · Detection | 2 | |
| ⬜ | 6 · **GATE A** | 2 | |
| ⬜ | 7 · Explanations + patterns | 3 | |
| ⬜ | 8 · Governance + apply | 3 | |
| ⬜ | 9 · **GATE B** | 3 | |
| ⬜ | 10 · Review page | 4 | |
| ⬜ | 11 · Demo script | 4 | |
| ⬜ | 12 · n8n (stretch) | 4 | |
| ⬜ | 13 · Documentation | 5 | |
| ⬜ | 14 · Rehearsal support | 5 | |

## Step 1 · Scaffold — what was done and verified

- `git init` (branch `main`). `.venv` on Python 3.12.8. Deno 2.9.6.
- Warrant copied from `~/Documents/warrant` @ `f595c14` (source tree clean) into `./warrant/`,
  excluding `.git/`, `.DS_Store`, `*.jsonl`, `.env`. `diff -r` against the source showed only the
  excluded items. `warrant/VENDORED.md` written; "Changes since copy" is empty. The source repo
  was checked after every step that could touch it: still clean.
- Layout from BUILD.md created. Modules for later steps are one-line docstring stubs that name
  their step. `cli.py` prints "not built yet" and exits 2.
- `scripts/dev.sh`, `Makefile`, `requirements.txt`, `.gitignore`, `.env.example`.

**Verified by running:**
- `make setup && make test` → pytest 3 passed; Warrant `deno task test` **43 passed, 0 failed**.
- `make warrant` → `/health` answers on :8000; `/verify` returns 401 without the token and
  `verified: true` with the token from `.env`.
- Ctrl-C on `make warrant` (SIGINT to the whole process group, both during startup and once
  running) → Warrant stopped, port 8000 closed, no `deno` process left, exit 130.
- `dev.sh <cmd>`: the child gets `WARRANT_URL`, `WARRANT_TOKEN`, `DRIFT_PRINCIPAL` (with its
  space intact); a failing command's exit code (7) propagates and Warrant is still stopped.
- First run created `.env` (mode 600) with a 64-hex token; a second run left it unchanged.
- `--persist` writes `state/ledger.jsonl` (nothing in `warrant/`), and a restarted Warrant reads it
  and verifies the chain intact.
- With something already on :8000, `dev.sh` refuses to start (exit 1).

## Step 2 · Synthetic data generator — what was done and verified

- `scripts/generate_data.py` (seed 42, Faker pinned to 40.43.0) writes all six files in PLAN.md §2
  to `data/source/`. The fictional company is Northpine and emails are `@northpine.example` (a
  reserved domain). It has 200 Workday workers, 199 ADP rows (198 matched + 1 ghost), 12 cost
  centres, 25 managers, 4 locations (2 CA, 2 US), 155 salaried / 45 hourly, and 9 terminated /
  4 on leave.
- **The story.** On 2026-10-01 Supply Chain Analytics (CC-410) was reorganised:
  - five analysts' cost moved to CC-420 and their manager stayed the same (in Workday, cost centre
    and supervisory org are separate);
  - two analysts moved to the other team lead;
  - none of the seven changes reached ADP.

  Every other planted mismatch is an isolated defect: M1 with one termination (2026-10-13) after
  the cutoff, an M2 Workday raise effective 1 Oct, a transposed salary, an hourly rate $1.25 off,
  one manager change from August, two October new hires, a ghost in DC Ops, and a surname change.
- `PAYROLL_CUTOFF = 2026-10-09` lives in `drift/constants.py`, shared by the generator and Drift.
- `tests/test_data.py` (11 tests).

**Verified by running:**
- `make data` twice → identical bytes (sha1 of every file compared). Also identical under
  `PYTHONHASHSEED` = 0, 1, 12345 and random.
- `make test` → pytest **14 passed**; Warrant **43 passed**.
- The tests assert:
  - **Counts and classes:** 18 mismatches and 4 decoys, with the per-type counts and classes from
    PLAN.md §2 and one plant per person.
  - **Planted rows exist:** every planted row is in the exports with the quoted values. M5 rows are
    absent from ADP and the crosswalk; M6 is absent from Workday and the crosswalk.
  - **No unplanted differences:** an independent comparison of all 198 matched pairs (status,
    termination date, pay ±$1, rate type, cost centre, location, manager, legal name, email) finds
    exactly the planted mismatches.
  - **Decoys:** each decoy differs in its raw values, and no other row does.
  - **Reorg date:** `2026-10-01` appears as an org date only on the 7 reorg rows.
  - **Cutoff:** exactly one M1 falls after the cutoff.
  - **No accidental patterns:** outside the reorg, no cost centre, org date or comp date is shared
    by ≥3 mismatches, and fewer than 3 mismatches fall after the cutoff.
  - **Committed data is current:** `data/source/` matches a fresh generation.
- **The tests can fail:** six hand-made errors were written into a scratch copy (a location, a
  salary, an email, a case-only email change on a clean row, a preferred name, a stray reorg date)
  and each was caught by the expected test.

## Step 3 · Warrant policy — what was done and verified

- In `./warrant/` (the copy; `~/Documents/warrant` is untouched, still clean at `f595c14`):
  - `HR_RECORD_CORRECTION` is in `src/policy.ts`, registered in `POLICIES`;
  - 13 tests are in `test/acceptance_test.ts`;
  - the README counts and the "policies ship" sentence are updated;
  - every change is logged in `warrant/VENDORED.md`.
- **Two approved deviations from PLAN.md §3**, both asked before being made:
  - **Its own filler list, `HR_FILLER_PHRASES`.** It contains the shared phrases plus
    record-correction filler: "data cleanup", "clean up", "housekeeping", "data hygiene", "tidy
    up", "routine maintenance", "keep records/systems in sync". The shared list is outreach-only,
    so a long "routine data cleanup…" purpose would otherwise have been issued a warrant.
  - **A backstop:** `action: create` and `action: deactivate` are always held, whatever the
    `change_class`.

**Verified by running:**
- `make test` → pytest 14 passed; Warrant **56 passed** (43 existing + 13 new).
- Warrant's own CI sequence in `warrant/` → `deno fmt --check`, `deno lint`,
  `deno check src/ test/ demo/`, `deno task test` and `deno task demo` all exit 0.
- The new tests cover:
  - a routine correction gets a `warrant_id`, and commit works;
  - compensation / employment_status / identity → 202, `approval_id`, no `warrant_id`, and only a
    `warrant.requested` event;
  - create / deactivate labelled routine → held;
  - 422 with no event written for: one source, a cleanup purpose, a housekeeping purpose, a
    too-short purpose, an unknown class, and a held class with no `approval_from`;
  - the principal (also as `"  dana okafor "`) cannot approve, and no issue event is written.
- **The tests can fail.** In a scratch copy, each rule was undone in turn: the shared filler list
  instead of the HR one, `minItems: 1`, the backstop removed, identity not held. Each change made
  exactly the tests that guard that rule fail.
- **Against a real Warrant process** (started by `dev.sh`):
  - `/policies` lists the five hold rules;
  - routine → 200;
  - compensation → 202 `change_class:compensation`;
  - deactivate labelled routine → 202 `action:deactivate`;
  - cleanup purpose → 422;
  - `/verify` intact.

## Deviations from PLAN.md / BUILD.md

**Step 1**
- **`dev.sh` runs `deno run …` directly, not `deno task dev`.** The flags are the same as the `dev`
  task. Running deno directly makes the PID that `dev.sh` tracks the server itself, so the
  `kill` on exit reaches it.
- **`dev.sh` refuses to start if something already answers at `WARRANT_URL`.** Without this check,
  Drift could talk to a different Warrant, which would have a different token and ledger.
- **`dev.sh` creates `.env` from `.env.example` when there is no `.env`.** It then adds the token.
  Variables already set in the shell take precedence over `.env`, and empty values stay unset, so
  `ANTHROPIC_API_KEY=` means "no key".
- **Added `tests/test_scaffold.py`** (package imports; vendored Warrant has no `.git`, `.env` or
  `*.jsonl`; `VENDORED.md` present). Without it pytest exits 5 ("no tests collected") and
  `make test` fails.
- **`.gitignore` also covers `.pytest_cache/` and `.DS_Store`.**
- **`make setup` also runs `deno install` in `warrant/`** to pre-fetch `@std/assert`. This did not
  change `deno.lock`.
- **`make demo` runs `reset` first**, as Step 11 describes.

**Step 2**
- **`requirements.txt` pins `faker==40.43.0`** (the human asked for this). Everything else is
  still unpinned.
- **New module `drift/constants.py`** holds `PAYROLL_CUTOFF`. It isn't in BUILD.md's layout. The
  detector may use it; it is a business date, not ground truth.
- **`make data` runs `python -m scripts.generate_data`**, so that the generator can import
  `drift.constants`. This needed an empty `scripts/__init__.py`. A new `pytest.ini` sets
  `testpaths = tests` and `pythonpath = .`.
- **The generated `data/source/` files are committed.** A test fails if they go stale.
- **planted.json format (Step 5 must match it or map to it).** Each entry has:
  - `type`: `M1`–`M7` or `D1`–`D3`;
  - `employee_ref`: a Workday ID, or `ADP:<Associate_ID>` for M6;
  - `associate_id`;
  - `field`: one of `worker_status`, `pay`, `cost_center`, `manager`, `record` (M5/M6),
    `legal_name`, `work_email`, `preferred_name`;
  - `change_class` (mismatches only);
  - the raw `workday` / `adp` column values;
  - a `note`.
- **How the data is represented** (anything else would be an unplanted difference):
  - a salaried row has annual pay only, and an hourly row has a rate only, in both systems;
  - dates are ISO in both systems;
  - `Preferred_Name` equals `Legal_First_Name` for everyone except D3;
  - emails are built from legal names, and M7's email keeps the old surname;
  - ADP has no currency column.
- **Added for realism:** 6 workers terminated in both systems and 4 on leave in both. They are
  consistent, and Step 5 must not flag them.
- **Reading of "two [M4] are also part of the reorg":** the 5 M3 and 2 M4 reorg mismatches are
  7 different people, so each planted person has exactly one difference.

**Step 3** (governance-touching; approved by the human before being made)
- **`hr-record-correction` uses `HR_FILLER_PHRASES`, not the shared `FILLER_PHRASES`.** It adds the
  cleanup and housekeeping phrases listed above. The other two policies are unchanged.
- **The policy also holds `action: create` and `action: deactivate`.** These rules are listed
  after the class rules, so `because` still names the class when one matches. The `rationale`
  sentence now mentions adding or removing a record.
- **One existing Warrant assertion changed:** the policy-list test now expects 3 policies instead
  of 2, because adding a policy changes that count.
- `warrant/VENDORED.md` was reformatted with `deno fmt`; the Step 1 table had failed
  `deno fmt --check`.

## Verification debt

- **Ctrl-C was simulated, not typed.** A script sent SIGINT to the process group, which is what a
  terminal does. Nobody has yet pressed Ctrl-C in a real terminal.
- **Tested on macOS only** (bash 3.2, BSD tools). Not run on Linux.
- **`make run`, `make app` and `make demo` exist but are not built yet.** `run` and `demo` reach
  the `cli.py` stub, which exits 2. (`make data` works as of Step 2.)
- **Step 2:** "no accidental extras" is proven against the test's own comparison, not the real
  detector, which Steps 4–5 still have to build to hit 18/18. The mutation checks were a one-off
  script and are not in the suite.
- **Step 2:** D1-2's ADP email ends in a trailing space inside the CSV. If Step 4's CSV reader
  trims whitespace silently, that decoy stops testing anything (it would still pass). Step 4
  should trim deliberately, in `normalize.py`.
- **Step 3:** the new Warrant tests call the request handler in-process, as the existing ones do.
  Against the real process, only the manual run above was done. Step 8's integration tests are the
  real end-to-end check.

## Open questions (for later steps — nothing decided yet)

- **Step 8:** `make run` and `make app` each start their own Warrant, with an in-memory store by
  default. An approval held during `run` would not exist in the Warrant that `app` starts. Choose
  one: those targets use `--persist`, or the whole flow runs within one Warrant lifetime, as
  `make demo` does.
- **Step 8, filler phrases:** a purpose containing any phrase from `HR_FILLER_PHRASES` is refused
  with 422. That covers the shared ones too ("follow up", "check in", "awareness",
  "engagement"…). Drift can't read Warrant's code, so the computed purposes simply have to be
  specific, and the Step 8 integration tests will catch a 422.
- **Step 8, approver = principal:** Warrant accepts a request whose `approval_from` is the
  principal, but nobody can then approve it, and it expires after 24 h. That fails closed, but it
  leaves the request stuck. Drift should refuse, when it loads its config, any approver equal to
  `DRIFT_PRINCIPAL`, rather than relying on Warrant to catch it.

## Next step

Step 4 · Connectors and normalisation.
