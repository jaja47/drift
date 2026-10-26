# HANDOVER — Drift

> **Rule:** update this file at the end of every step.

Repo: https://github.com/jaja47/drift (public, created 2026-10-24).

## Where the build stands

| | Step | Day | State |
|---|---|---|---|
| ✅ | 1 · Scaffold | 1 | done 2026-10-21 |
| ✅ | 2 · Synthetic data generator | 1 | done 2026-10-24 |
| ✅ | 3 · Warrant policy | 1 | done 2026-10-24 |
| ✅ | 4 · Connectors + normalisation | 2 | done 2026-10-24 |
| ✅ | 5 · Detection | 2 | done 2026-10-24 |
| ✅ | 6 · **GATE A** | 2 | answered 2026-10-24 |
| ✅ | 7 · Explanations + patterns | 3 | done 2026-10-24 |
| ✅ | 8 · Governance + apply | 3 | done 2026-10-25 |
| ✅ | 9 · **GATE B** | 3 | answered 2026-10-25 |
| ✅ | 10 · Review page | 4 | done 2026-10-25 |
| ✅ | 11 · Demo script | 4 | done 2026-10-25 |
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

## Step 4 · Connectors and normalisation — what was done and verified

- `drift/connectors/base.py` is the contract:
  - the `WorkerSource` (`list_workers`) and `PayrollSource` (`list_payroll`) protocols;
  - `SourceRow` (raw values + `source_ref`);
  - `Reference` (crosswalk + cost-centre and location maps);
  - the columns Drift reads;
  - `SourceDataError`;
  - a docstring saying where a Workday RaaS report and ADP's Workers API would plug in.
- `drift/connectors/csv_source.py` holds `CsvWorkdaySource`, `CsvAdpSource` and
  `read_reference()`. Files are opened read-only and values come back exactly as written.
- `drift/normalize.py` turns the rows into one `Pair` per person (201: 198 matched, 2
  `workday_only`, 1 `adp_only`). Each rule is a small public function. `pay_matches` applies the
  ±$1 rule.

**Verified by running:**
- `make test` → pytest **63 passed**; Warrant 56 passed.
- **Unit tests per rule:**
  - status (both vocabularies, plus unrecognised values);
  - dates and exact money in cents;
  - hourly × 2080, with the annual fallback;
  - the ±$1 tolerance, including the exactly-$1 edge;
  - email case and whitespace;
  - legal name only (preferred ignored, case- and whitespace-insensitive);
  - cost-centre and location maps (unmapped is kept and marked);
  - manager through the crosswalk (unknown → `ADP:<id>`).
- **Join tests:**
  - matched, Workday-only and ADP-only rows are all kept;
  - a crosswalk entry pointing at a missing row does not invent a match;
  - a duplicate ID stops the run;
  - a malformed value names its row and column.
- **Real exports:**
  - **all 4 decoys normalise to equal**, and each really differed before normalisation;
  - **none of the 16 matched-pair planted mismatches is normalised away**;
  - M5 rows come out `workday_only` and M6 `adp_only`;
  - no marker values appear in the clean data.
- **Connectors:**
  - they meet the protocols;
  - they read the committed exports;
  - D1-2's trailing space survives reading;
  - reading leaves every source file's bytes and mtime unchanged;
  - a BOM is accepted;
  - a missing column, a wrong field count, an empty key, a duplicate mapping key, and a crosswalk
    mapping two ways are each refused.
- `tests/test_rules.py`: no file under `drift/` mentions `planted` (CLAUDE.md: the detector never
  reads planted.json).
- **The tests can fail:** ten deliberate breakages, each run in a scratch copy:
  - email not lower-cased, and not trimmed;
  - no pay tolerance;
  - 2000 hours instead of 2080;
  - the preferred name compared;
  - an unmapped code, or an unknown manager, becoming None;
  - ADP-only rows dropped;
  - a duplicate ID resolved "last wins";
  - the reader trimming values.

  Each was caught by the tests for that rule. In every run the two `test_scaffold` tests also
  failed, only because the scratch copy has no `warrant/`.

## Step 5 · Detection — what was done and verified

- `drift/detect.sql` has two named sections:
  - `schema`: the `pairs` table and an exact `annual()` money macro;
  - `mismatches`: one CTE per type M1–M7, `UNION ALL BY NAME`, then the fingerprint
    `sha256(type|employee_ref|field|workday_value|adp_value)`.

  The ±$1 tolerance is passed in as `$pay_tolerance_cents` from `normalize.PAY_TOLERANCE_CENTS`,
  so it isn't written twice.
- `drift/detect.py` loads the `Pair`s into an in-memory DuckDB (no SQL strings in Python) and
  returns `Mismatch` rows with BUILD.md's columns. `detect_sources()` runs the whole of detection;
  the CLI and the page both call it.
- `drift/classify.py` + `config/change_classes.yaml`: type + field → class and a plain label.
  Anything not listed → `identity`. The loader refuses a config that makes a pay, status, record
  or name field routine, or that uses an unknown class.
- `python -m drift.cli detect` prints the summary and the table, for GATE A. `run`, `approve`,
  `sync` and `demo` print "not built yet" and exit 2.
- **Result on the committed data:** 201 people compared, 18 mismatches (8 routine, 3
  compensation, 5 employment_status, 2 identity). The 7 reorg rows carry org date 2026-10-01.

**Verified by running:**
- `make test` → pytest **111 passed**; Warrant 56 passed.
- **Scoring against planted.json: 18/18 found, 0 decoys flagged, 0 extras.**
- **No compensation, status, missing-record or name mismatch is routine.** That's 10 rows, each
  checked. Only `cost_center` and `manager` are routine in the shipped config.
- **Unknown input is classed `identity`:** an unknown type, or a known type with another field.
  The loader refuses pay, status, record or name marked routine, an unknown class, a missing key,
  and a missing table.
- **Output format:**
  - fingerprints equal the documented hash, are unique, and are stable across two runs;
  - `employee_ref` is always `W#####` or `ADP:G…`, never a name;
  - `*_source` points back to both rows (`None` on the missing side for M5/M6);
  - effective dates are set only on M2 (comp) and M3/M4 (org).
- **The SQL schema matches the `Pair` dataclass**, in column order and types.
- **Each rule on hand-built pairs:**
  - M1 for terminated vs active, leave or unrecognised (not vs terminated);
  - M2 at exactly +$1 (no), +$1.01 and −$1.01 (yes), one side missing (yes), both missing (no);
  - M3, including an unmapped cost centre;
  - M4, including an unknown ADP manager and a missing one;
  - M5 (not for a terminated Workday-only worker);
  - M6 (even when terminated);
  - M7;
  - two disagreements on one person give two rows.
- `tests/test_rules.py` now also checks two rules: no SQL keywords in `drift/*.py`, and
  `classify.py` imports neither the explainer nor an LLM client.
- **The tests can fail:** nine deliberate breakages were each caught:
  - M7 left out of the union;
  - M1 only when ADP says active;
  - a tolerance of 0 written into the SQL;
  - M5 including terminated workers;
  - M3 comparing names with codes;
  - a fingerprint without the ADP value;
  - unknown → routine;
  - the config making M2 routine;
  - the never-routine guard dropping pay.

## Step 6 · GATE A — the human's answers

The table from `python -m drift.cli detect` was shown: 18 rows, plus both export headers.

1. **Do the 18 rows read like real HR problems?** Yes.
2. **Is anything classed wrongly?** No, all classes are correct. The after-cutoff termination
   (W10026) and after-cutoff new hire (W10200) **stay held**. Step 7's explanation may say
   they're likely timing, but a human still decides.
3. **Are the column names believable?** Yes, as they are.
4. **Differences no type covers?** Add a catch-all type, **always held**.

**Done as a result of answer 4:**
- **New type M8 · "Other difference — no specific rule covers it"** (in `detect.sql` and
  `change_classes.yaml`), classed `identity`. It reports a disagreement in:
  - status or termination date, except the M1 case;
  - location;
  - email (after normalisation);
  - rate type.
- **`classify.py` accepts a list of fields per type.** The never-routine guard checks every listed
  field, so making M8 routine is refused because the list includes `worker_status`.
- **Verified:**
  - pytest 124 passed; Warrant 56 passed;
  - the real data still gives exactly the 18 planted mismatches and no M8 rows;
  - 6 new rule cases cover location, email, rate type, active vs terminated, leave vs active, and
    differing termination dates;
  - M1 is never duplicated as M8;
  - M8 is `identity` for each of its fields;
  - a routine M8 config is refused.
- **The tests can fail:** each deliberate breakage was caught — M8 left out of the union, M8
  repeating M1, and M8 marked routine.

## Step 7 · Explanations and patterns — what was done and verified

- **`drift/explain.py`** produces display text only.
  - `payload()` builds the model's input from an allow-list: type and label, field, employee ref,
    both values, effective dates, payroll cutoff. Pay values are replaced by
    `"Workday is 4.4% higher than ADP"`; names and emails are replaced by `withheld`, plus which
    part of the name differs.
  - **The request** is one Messages API call per mismatch on `DRIFT_MODEL` (default
    `claude-sonnet-5-5`):
    - structured JSON output (`explanation`, `likely_causes`, `suggested_owner`);
    - effort `low`;
    - server-side refusal fallback (`fallbacks="default"`, beta
      `server-side-fallback-2026-07-01`);
    - a system prompt saying the output is advisory and must never suggest bypassing review.
  - **The answer is checked:** it is used only if `stop_reason` is `end_turn`, the JSON has the
    right shape, and no bypass phrase appears. Causes are cut to three. Good answers are cached by
    fingerprint in `state/explanations.json`.
  - **Otherwise the template is used:** with no credentials, a failed call, a refusal, malformed
    output, or bypass advice, a deterministic template for each type M1–M8 is used. Templates
    note when a change is dated after the cutoff.
- **`drift/patterns.py`** groups mismatches by org date, comp date, after-cutoff and cost centre.
  A group of ≥3 becomes a pattern with a one-line diagnosis and an owner. A group already
  explained by an earlier pattern is dropped.
- **`python -m drift.cli detect`** prints the patterns before the table. `--explain` adds the
  explanations and says how they were produced.
- **Result on the committed data:** exactly one pattern — "7 org changes share the effective date
  2026-10-01 (5 cost centre, 2 manager) — likely one reorg that did not reach ADP. Suggested
  owner: IT Application Engineering (integration)."

**Verified by running:**
- `make test` → pytest **151 passed**; Warrant 56 passed.
- **The LLM payload holds no names, emails or pay figures.** This was checked for all 18 real
  mismatches plus synthetic M8 email and location rows: no `@`, no amount with cents, no bare
  number of 4+ digits outside dates and IDs, and no token of the person's name from either
  system. The payload keys are an allow-list.
- **The reorg pattern is found**, it is the only pattern in the data, and it is not repeated as a
  CC-420 pattern. Comp-date, after-cutoff, cost-centre and below-minimum groups are each tested.
- **The template fallback works offline:** with no credentials, all 18 get deterministic
  templates, nothing is cached, and the tests cannot construct a real client.
- **With a fake client:**
  - there is exactly one call per mismatch, carrying exactly the payload;
  - the request is advisory and structured, uses `DRIFT_MODEL`, has fallbacks on, and sets no
    `thinking`/`temperature`;
  - answers are cached and not requested twice;
  - a refusal, truncated output, non-JSON, an empty answer, causes that aren't a list, and two
    kinds of bypass advice all give the template, uncached;
  - a connection error gives the template for that one mismatch only;
  - an auth error stops after one call.
- `tests/test_rules.py`: `classify`, `govern`, `detect`, `normalize` and `patterns` import neither
  `anthropic` nor the explainer.
- **The tests can fail:** eight deliberate breakages were each caught:
  - pay values sent;
  - names or emails sent;
  - the bypass guard removed;
  - a refusal accepted as an answer;
  - an auth error not stopping the calls;
  - the advisory line dropped from the prompt;
  - already-explained groups not suppressed;
  - the pattern minimum lowered to 2.

## Step 8 · Governance and apply — what was done and verified

- **`drift/govern.py`** is the governance loop. Everything goes through Warrant over HTTP
  (`WarrantClient`, httpx, 5 s timeout, no retries).
  - **`run`**: detect, then for each mismatch not already settled, `POST /warrant` under
    `hr-record-correction`:
    - 422 → `blocked`, violations stored;
    - 202 → `held`, approval id stored;
    - 200 → `apply_with_warrant`;
    - no usable answer → the run stops with `warrant_unreachable`, and every later mismatch is
      marked `warrant_unreachable` without being asked.
  - **The request comes from code and config:**
    - `actor` `drift-reconciler@v0.1.0` and `principal` from `DRIFT_PRINCIPAL`;
    - `action` is `create` for M5, `deactivate` for M6, `update` otherwise;
    - `change_class` from classify.py;
    - `approval_from` = the class's approver;
    - `sources` = both rows. A missing side cites the search that found nothing, e.g.
      `adp: no payroll record joined to W10199 through the ID crosswalk`.
  - **`purpose()`** is computed per record from the mismatch: employee ref, field, reason, and
    only code/date values (cost centre, manager id, status, dates). It never includes pay, names
    or emails.
  - **`apply_with_warrant`** is the only caller of the target (a rule test checks this with
    `ast`). It does four things:
    1. reads the warrant back (`GET /warrant/:id`) and writes only if it is `issued`, under this
       policy, for exactly this system, action, object, field and class;
    2. writes;
    3. commits with the result;
    4. on a write error, aborts with the reason.

    A forged id, or a real warrant issued for another record, writes nothing.
  - **`approve <ref|fingerprint> --as NAME --decision …`** posts `/approval/decide` and records
    `approved` or `rejected`. It writes nothing; Warrant's refusals are printed.
  - **`sync`** asks Warrant about every `held` / `approved` / `uncommitted` record:
    - issued → write and commit;
    - refused → `rejected`;
    - lapsed, or unknown to Warrant → `expired`;
    - `uncommitted` → retry the commit.
- **`drift/target/adp_target.py`** is `AdpTarget`. It writes Workday's value, in ADP's terms, into
  `state/adp_current.csv`, using normalize.py's maps. There is one rule per type
  (M1/M8 status, M2 the pay column ADP pays from, M3, M4, M5 a new row, M6 terminate, M7,
  M8 location/email/rate type).
  - It needs a warrant id.
  - Writes are atomic.
  - A value it cannot map is a `TargetError`, never a guess.
  - The copy is created by the first successful write.
  - It refuses to sit in the source directory.
  - The result it returns goes to the ledger: the row and the columns changed, with old/new
    values only for code and date columns.
- **`drift/state.py` + `drift/state.sql`** hold `state/drift.db`, keyed by fingerprint. It
  stores ids, class, action, status, detail and the committed result — no names, emails or pay.
  - Settled statuses (applied, held, approved, rejected, failed, uncommitted) get no new warrant
    on a rerun.
  - blocked, expired and warrant_unreachable are asked again.
- **`drift/report.py`** writes `reports/run_<ts>.md`: counts, patterns, the applied table with
  warrant ids, the held table with approver, approval id and fingerprint, then blocked,
  everything else, and `/verify`. It contains no pay, names or emails.
- **Approvers** are in `change_classes.yaml`, all fictional:
  - compensation → Priya Raman (Payroll Manager);
  - employment_status → Owen Castellanos (Payroll Operations Lead);
  - identity → Grace Adeyemi (HR Operations Manager).

  The loader refuses: a routine approver, an unknown class, a missing held class, or a name that
  isn't first + last. `check_principal` stops the run before any request if the principal is an
  approver.
- **CLI:** `run`, `approve` and `sync` replace the stubs. Exit 0 = done; 1 = refused or Warrant
  unreachable; 2 = usage/config error (nothing requested).
- **Make:**
  - `run`, `approve`, `sync` and `app` use `dev.sh --persist`.
  - New targets: `make approve REF=… AS="…" [DECISION=rejected] [NOTE=…]` and `make sync`.
- **Result on the committed data:**
  - `run`: 8 applied, each committed; 10 held. The ledger has 26 events, intact.
  - approve W10026 as Owen Castellanos, reject W10076 as Priya Raman, then `sync`: 9 rows of the
    ADP copy differ from the export (8 routine + the termination). The ledger has 31 events.
  - A rerun: 18 already handled, no new events.

**Verified by running:**
- `make test` → pytest **273 passed** (151 before); Warrant **56 passed**. `warrant/` is
  unchanged; `~/Documents/warrant` is still clean at `f595c14`.
- **Integration tests against a real Warrant process.** `tests/conftest.py` starts
  `deno run … src/server.ts` from `./warrant/` with `WARRANT_STORE=memory`, a random token and a
  free port, and stops it afterwards. Every BUILD "Done when" item is covered:
  - routine mismatches applied, each with a committed warrant (`GET /warrant/:id` → committed,
    no approvals);
  - every sensitive mismatch held: no warrant id on `GET /approval/:id`, and its ADP row
    byte-for-byte unchanged;
  - approving W10026 writes nothing until `sync`, which then writes and commits it; rejecting
    W10076 means it is never written;
  - a rerun adds 0 ledger events;
  - with Warrant stopped: outcome `warrant_unreachable`, all 18 `warrant_unreachable`, and no
    `adp_current.csv` at all. Once Warrant is back, the next run asks again;
  - `/verify` is intact after the full flow.
- **More integration tests:**
  - neither the principal nor another approver can decide;
  - Warrant stopping before a write, or between a write and its commit. The second case uses a
    persistent ledger and a restarted Warrant: the write is recorded `uncommitted`, and `sync`
    commits it;
  - one dropped request stops the run although Warrant is still up;
  - a wrong token → 401 → fail closed;
  - a filler purpose → 18 blocked, 0 ledger events, then retried successfully;
  - a forged warrant id writes nothing;
  - a warrant for another record writes nothing;
  - a write failure → warrant aborted with the reason;
  - an expired approval (TTL 1 s) → `expired`, not decidable, re-requested with new ids;
  - a Warrant restarted without its ledger → `expired`, nothing written;
  - approve and sync with Warrant down → nothing recorded;
  - decisions posted directly to Warrant are picked up by `sync`;
  - the CLI's run / approve / sync, and its refusals (no principal; principal is an approver).
- **The ledger carries no personal data:**
  - 18 distinct per-record purposes;
  - no employee name tokens, no `northpine.example`;
  - none of the planted pay figures, and no fingerprint.

  The same checks run on every request body and on the report.
- **Target unit tests:**
  - **each of the 18 fixes resolves its mismatch:** detection re-run against the copy no longer
    finds it, and nothing new appears. For M5 a crosswalk entry is added; M6 stays reported, as
    `terminated <date>`;
  - every other row is unchanged and untouched lines are byte-identical;
  - the 4 M8 fields are each fixed on edited copies of the exports;
  - unmapped values are refused with nothing written;
  - a second apply changes nothing;
  - a created record follows its location's company code and pay frequency, and is not created
    twice.
- **By hand, the real Make targets** (`make reset`, `make run`, `make approve` ×2, `make sync`,
  `make run`): five Warrant lifetimes sharing `state/ledger.jsonl`, the held requests survived
  every restart, 31 events intact, and no deno left running afterwards.
- **The tests can fail:** 20 deliberate breakages, each in a scratch copy, were all caught by the
  full suite. These were:
  - a 202 treated as a go-ahead;
  - no warrant check;
  - only the warrant's existence checked;
  - settled ignored on rerun;
  - no stop on unreachable;
  - pay in the purpose;
  - the fingerprint in the context;
  - no approver named;
  - principal = approver allowed;
  - no abort on a failed write;
  - a rejection sent down the write path;
  - no commit retry;
  - expired treated as settled;
  - the copy made eagerly;
  - the target beside the source;
  - no warrant id needed;
  - pay values in the result;
  - unmapped written as-is;
  - the wrong pay column;
  - approve writing before sync.

  The first run caught 18 of 20. Two gaps were found and fixed:
  - no test had Warrant drop one request and come back, so "keep going after a failure"
    survived;
  - no test had a decision made outside Drift.

## Step 9 · GATE B — the human's answers

`make demo` was run and its output shown (transcript below).

1. **Do all five demo beats in PLAN.md §1 happen?** Yes, all five.
2. **Are the approvers right?** Yes. Three separate fictional approvers stay as configured:
   Priya Raman (compensation), Owen Castellanos (employment status) and Grace Adeyemi
   (identity).
3. **Should refused requests be recorded in the ledger?** No. Warrant stays as it is: a refused
   request leaves no event. **The README (Step 13) must state this correctly**, rather than
   PLAN.md §1's "every … refusal … lands in Warrant's hash-chained ledger".

Nothing in the code changed as a result of the answers.


**The human's decisions before the gate (2026-10-25):**
- **Keep the read-back check.** Before every write, Drift reads the warrant back from Warrant.
  See Deviations → Step 8.
- **Fill both demo gaps, then run GATE B.**

**Built for the gate:**
- **The "format differences ignored" count (beat 1).** `normalize.format_differences()` lists
  matched people whose values differ as exported but agree once normalised:
  - email case or spacing;
  - the same pay held in another form (hourly vs annual, within $1);
  - legal-name case or spacing;
  - a Workday preferred name that differs from the legal name.

  The translations every row goes through (status letters, cost-centre and location codes,
  manager ids) are not counted. `detect_sources` puts the list on `Detection.ignored`. `detect`,
  `run`, the report and the demo print it. **On the committed data it finds exactly the 4 decoys,
  without reading planted.json.**
- **One warrant, opened (beat 2).** `govern.show()` and `python -m drift.cli show <ref|fingerprint|
  wrt_…|apr_…>` (and `make show REF=…`) print one correction as Warrant holds it: state, policy,
  actor, principal, the change, the purpose, both sources, the approval, what was written, and
  the event count. A held correction shows its approval and "No warrant exists until <approver>
  decides". `show` follows an approval to its warrant even before `sync`.
- **A rough `make demo` (Step 11 polishes it).** `drift/demo.py` narrates the five beats. It
  calls `run`, `show`, `approve` and `sync` (the same functions as the CLI) and explains one case.
  It runs in one in-memory Warrant and refuses to start if `state/drift.db` already exists.
  - It picks the clearest termination: W10112, terminated 2026-09-18, before the cutoff.
  - It shows it held.
  - The principal tries to approve and is refused.
  - Owen Castellanos approves, `sync` writes it, and the warrant is shown with the approval.

**`make demo`, as shown at the gate:**
1. 201 compared, 18 real mismatches, 4 format differences correctly ignored (listed).
2. 8 routine fixes applied with warrant ids; W10015's warrant opened (purpose, sources,
   "none needed — routine", Home_Department 410 → 420, committed).
3. W10112 held for Owen Castellanos, ADP untouched (status A). Murtaza Dibbo was refused twice
   (second person; not the person asked). Owen Castellanos approved, sync → applied, ADP now
   T / 2026-09-18. The other 9 are still held.
4. The 2026-10-01 reorg pattern → IT Application Engineering.
5. `GET /verify`: intact, 29 events.

Exit 0, and no deno left running.

**Verified by running:**
- `make test` → pytest **294 passed**; Warrant **56 passed**.
- New tests:
  - format differences, rule by rule on hand-built rows: code translations alone don't count;
    a real email, pay (>$1) or name difference doesn't count;
  - the scored test: exactly the 4 decoys, none of them flagged;
  - `show` for applied, held, approved, rejected, by warrant id, by approval id, unknown, and
    before sync for a decision made directly in Warrant;
  - the CLI `show`;
  - the demo prints all five beats and leaves 9 applied / 9 held;
  - the demo refuses stale state and writes nothing to the ledger.
- **The tests can fail:** 7 new deliberate breakages were all caught: a real email or pay
  difference counted, the preferred name not counted, code translations counted, `show` not
  following an approval, a held correction shown without "no warrant exists", the demo skipping
  sync. The first run caught 6/7. The gap — no test of `show` before `sync` for a decision made
  directly in Warrant — was filled. Step 8's 20 breakages still all get caught.

## Step 10 · Review page — what was done and verified

- **`drift/app.py`** is the Streamlit page, for a non-technical HR reader. It is layout only. From
  the top:
  - the banner "Synthetic data. No real Workday or ADP system is connected.";
  - before any run, one button: "Compare Workday and ADP";
  - after a run, the top row: employees compared, mismatches, fixed automatically, waiting for
    approval, format differences ignored (with an expander saying which and why);
  - **Root causes** — the patterns, before any row;
  - **Every mismatch**: a table of employee, what differs in plain words, class, status,
    approver. What needs a person comes first;
  - one panel per mismatch, with:
    - the explanation, likely causes and suggested owner;
    - the correction as Warrant holds it: warrant, purpose, sources, approval, what was written,
      ledger events — or, for a held one, who it waits on and that no warrant exists yet;
  - for held rows, a "Decide as" name field, a note, and Approve / Reject. The outcome shows
    inside that row, which stays open, plus a pop-up notice;
  - **footer**: "Audit log: verified ✓ (N events)" from `/verify`, an export of the ledger
    (`GET /export`, JSONL), and the run report download. It also notes how the explanations were
    produced.
- **`drift/review.py`** holds everything the page shows and does:
  - `snapshot()` assembles the view (statuses from drift.db, explanations, patterns, `/verify`).
    It is read-only and creates nothing on disk.
  - `start_run`, `decide` and `check_decisions` call `govern.run` / `approve` / `sync` — the
    CLI's code — and return an `Outcome`. **`decide` approves, then syncs**, so an approval made
    on the page is applied straight away (demo beat 3).
  - `settings_from_env()`: the page reads `DRIFT_PRINCIPAL` and, optionally, `DRIFT_STATE_DIR`,
    `DRIFT_REPORTS_DIR` and `DRIFT_SOURCE_DIR`.
- **Shared with the CLI:** `report.shown_facts()` holds one correction as (label, value) pairs;
  `show` prints them as lines and the page as a table. `WarrantClient.export()` is new.
- **`.streamlit/config.toml`:**
  - `server.address = "localhost"` — the page can approve pay changes, so it never listens beyond
    this machine;
  - no first-run email prompt;
  - no usage telemetry;
  - `toolbarMode = "minimal"` — no "Deploy" button.

**Verified by running:**
- `make test` → pytest **306 passed**; Warrant **56 passed**.
- **The page, headless** (Streamlit's `AppTest`, against a real Warrant). The main test is the
  "done when", done on the page alone:
  - compare → 201 / 18 / 8 / 10 / 4, the 4 format differences, 18 table rows with the 10
    waiting first, decide fields on held rows only;
  - W10015's warrant opened: committed, routine, purpose, sources;
  - W10112 held, "No warrant exists until Owen Castellanos decides", ADP untouched;
  - the principal is refused, in the row;
  - Owen Castellanos approves → applied in the row and in a pop-up, ADP now T / 2026-09-18,
    9 waiting, still 8 automatic, "1 more fixed after a named person approved";
  - the reorg pattern comes first;
  - "Audit log: verified ✓";
  - a rejection is never applied;
  - no stray markdown, and the time reads `YYYY-MM-DD HH:MM UTC`.

  Also tested:
  - the first screen writes nothing to disk;
  - "Check for decisions" applies a decision made through the CLI;
  - an empty name decides nothing;
  - with Warrant down, the page says so and the run fails closed with no ADP copy;
  - with no principal, the page refuses;
  - the Streamlit config;
  - plain-words descriptions and money formatting.
- **A rule test keeps `app.py` layout-only.** It imports only streamlit, `drift` and
  `drift.report`, and never calls `govern.run/approve/sync`, `State`, the target or detection.
- **In a real browser** (Chrome, driven by Claude), with the page started the way `make app` does
  (`dev.sh --persist`, clean `state/`). The whole demo was done on the page: compare, read the
  numbers and the root cause, open W10112, be refused as Murtaza Dibbo, approve as Owen
  Castellanos, see the warrant with its approval and what was written, then the audit footer
  (29 events).

  **The browser found five problems the headless tests could not.** All were fixed, re-checked
  in the browser, and are now covered by tests:
  1. blank labels rendered as a literal `****`;
  2. a decision's outcome appeared only at the top of the page, out of the decider's view, and
     the row collapsed;
  3. the last-run time was a raw ISO timestamp;
  4. Streamlit's "Deploy" button was in the toolbar;
  5. **the page listened on all network interfaces** (it printed a Network and an External URL).
     It now binds to 127.0.0.1 — checked with `lsof`.
- **The tests can fail:** 9 new deliberate breakages were all caught:
  - deciding without applying;
  - every row offering a decision;
  - the page creating state on load;
  - approved fixes counted as automatic;
  - rows not ordered;
  - `****` labels;
  - outcomes shown only at the top;
  - listening on every interface;
  - an empty name sent to Warrant.

  Step 8's 20 and Step 9's 7 are still all caught (one Step 9 pattern was updated for the
  refactored code).

## Step 11 · Demo script — what was done and verified

**The human's decisions at the start of this step (2026-10-25):**
- **Bind Warrant to localhost.** Done; see below and `warrant/VENDORED.md`.
- **Keep the values on the page** (pay figures and legal names, for the person deciding).

**Warrant listens on loopback only.** `warrant/src/server.ts` passes a `hostname` to `Deno.serve`,
from `WARRANT_HOSTNAME`, defaulting to `127.0.0.1` (before, it listened on `0.0.0.0`). The
README's configuration table has the new row, and `VENDORED.md` logs the change as approved.
Warrant's CI sequence passes: fmt, lint, check, 56 tests, demo.

**`make demo`, polished:**
- It **runs in one Warrant lifetime, with state of its own.** It:
  - clears `state/demo` and `reports/demo`;
  - regenerates the data (identical bytes);
  - starts Warrant through `dev.sh`, in memory, with a **fresh random token** for this demo;
  - runs the narration;
  - then opens the review page on the same run, until Ctrl-C.

  The state `make run` / `make app` keep in `state/` is never touched. This settles the Step 9
  question.
- **The narration** (`drift/demo.py`) follows Warrant's walkthrough:
  - bold beat titles taken from PLAN.md §1;
  - a dimmed line per beat saying what to notice;
  - green for warrants, applied fixes and an intact ledger; red for refusals;
  - colour only at a terminal (`NO_COLOR` respected).
- **`--pause`** waits for Enter before beats 2–5, but only when stdin is a terminal, so scripted
  runs never hang.
- **Ctrl-C is how the demo ends,** so exit 130 counts as a clean stop for this target only.
- The CLI's `--source/--state/--reports` default to `DRIFT_SOURCE_DIR / DRIFT_STATE_DIR /
  DRIFT_REPORTS_DIR` when set — the variables the page reads — so the demo sets its directories
  once.
- The demo's explanation cache lives in its own state directory.

**Verified by running:**
- `make test` → pytest **308 passed**; Warrant **56 passed**.
- **The real `make demo`** (the page headless, so no extra browser window opened):
  - all five beats narrated, exit 0 before the page;
  - Warrant on `127.0.0.1:8000` and the page on `127.0.0.1:8501` (`lsof`);
  - in Chrome, the page showed the demo's own run: 9 waiting, W10112 fixed after approval,
    29 events, the same run time;
  - SIGINT to the demo's whole process group (what Ctrl-C sends) stopped the page and Warrant:
    no deno or streamlit left, both ports free. After the exit-130 change a second run ended
    with exit 0;
  - **the persistent state was untouched:** `state/drift.db`, `state/ledger.jsonl` and
    `state/adp_current.csv` from an earlier `make run` had the same sha1 before and after;
  - `data/` showed no git change after regenerating.
- **New tests:**
  - **Warrant listens on loopback only.** The test binds this machine's own network address on
    Warrant's port. That succeeds only if Warrant does not hold every interface, and needs no
    traffic. A first version that tried to *connect* passed even with `0.0.0.0`, because this
    sandbox cannot reach its own LAN address. Now the test fails with `0.0.0.0` and passes with
    `127.0.0.1`.
  - the demo calls `pause` exactly before beats 2–5 and prints no colour codes off a terminal;
  - `cli demo --pause` doesn't hang without a terminal;
  - the CLI takes its directories from the `DRIFT_*` variables.
- **The tests can fail:** 4 new deliberate breakages were all caught: Warrant back on every
  interface, the demo never pausing, the CLI ignoring the variables, colour off a terminal. The
  earlier 36 are still all caught (40 in total).

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

**Step 4**
- **The CSV readers live in `connectors/csv_source.py`, not in `base.py`.** BUILD.md puts the
  protocols in `base.py` and doesn't say where the CSV classes go.
- **A `Reference` (crosswalk + two maps) is part of the contract.** BUILD.md names only the two
  source protocols, but the maps need a home too.
- **Names are also compared case-insensitively, with whitespace collapsed** (Unicode NFC).
  BUILD.md says only "legal name only". Accents are not stripped, so "José" and "Jose" still
  differ.
- **Values normalisation can't map are kept with a marker** (`unrecognised:`, `unmapped:`,
  `ADP:<id>`), so they compare unequal and get flagged. They are never dropped or guessed. This is
  the normalisation version of "unknown → strictest".
- **The run stops with `SourceDataError` on:**
  - a duplicate ID;
  - a crosswalk mapping one associate to two employees;
  - a mapping key listed twice;
  - a malformed date or amount;
  - a malformed CSV.
- **Pay uses the figure that matches the declared rate type.** If that one is blank, it falls back
  to the other. That fallback is how D2 (ADP holding an hourly rate annualised) normalises.
- **Added `tests/test_rules.py`** for CLAUDE.md rules that can be checked mechanically.

**Step 5**
- **Each rule is a little broader than its one-line description in PLAN.md:**
  - **M1** fires when Workday says terminated and ADP says anything else (active, leave or an
    unrecognised value), not only "active".
  - **M5** skips a terminated Workday worker with no payroll record, because that isn't a payroll
    gap.
  - **M6** fires whatever the ADP status.
  - **M2** also fires when only one side has a pay figure.
- **The effective-date columns are filled only where they explain the mismatch**: org date on
  M3/M4, comp date on M2, and NULL elsewhere. That way a shared date means something for Step 7's
  patterns.
- **How values are shown:**
  - pay is annualised, e.g. `154000.00/yr`;
  - cost centre shows Workday's full name against ADP's code;
  - manager shows both sides as Workday IDs;
  - status includes the termination date;
  - M5 includes the hire date.
- **Mismatch type labels live in `change_classes.yaml`**, next to the classes, so the
  human-reviewed file is the one catalogue of types.
- **`detect.sql` also holds the table schema and a money macro.** `detect.py` reads it by `-- name:`
  section and inserts rows through DuckDB's relation API, so no SQL sits in Python.
- **Approvers are not in `change_classes.yaml` yet.** That's Step 8.

**Step 6 (GATE A)**
- **Added mismatch type M8** (catch-all, `identity`, always held), as the human decided at the
  gate. PLAN.md §2 lists M1–M7. planted.json has no M8, and the data has none, so the 18/18 score
  is unchanged.
- **`change_classes.yaml`'s `field` may now be a list**, which M8 uses.

**Step 7**
- **Names and emails are withheld from the LLM too, not only pay.** BUILD.md mentions only
  replacing pay values; CLAUDE.md rule 5 requires the rest. The payload also adds a `difference`
  key (the pay percentage, or which part of a name differs) and the type's label.
- **The LLM is used only when `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` is set.** A credential
  that exists only as an `ant` CLI profile is ignored, so a fresh clone with no key gets templates.
- **Request settings for Sonnet 5.5:**
  - server-side refusal fallback `fallbacks="default"`, following the current Claude API
    guidance for this model (a refusal still ends in the template if it gets through);
  - effort `low`, because the output is short;
  - `thinking` left at the model default (adaptive);
  - `max_tokens` 2048.
- **Extra guards on the answer:**
  - output containing bypass advice is discarded for the template;
  - an auth, permission, not-found or bad-request error stops further calls for the run, since it
    would repeat;
  - only LLM answers are cached, never templates.
- **The M2 template offers "a recent pay change did not reach ADP" only when the comp date is
  within 60 days of the cutoff.** Otherwise "keyed differently (typo)" comes first.
- **Pattern details:**
  - a group already explained by an earlier pattern is dropped;
  - cost-centre groups use Workday's cost centre, or ADP's department for ADP-only records;
  - "after cutoff" uses termination and hire dates from the pairs.

**Step 8**
- **State SQL lives in `drift/state.sql`, not `detect.sql`.** CLAUDE.md says "SQL lives in
  detect.sql". The SQLite state is a different database from DuckDB detection, so it has its own
  `.sql` file, loaded by the same `-- name:` reader. The rule test (no SQL in Python) still
  passes unchanged.
- **New modules not in BUILD.md's layout:**
  - `drift/state.py` + `state.sql` (drift.db);
  - `drift/report.py` (the run report);
  - `drift/target/adp_target.py`;
  - `tests/conftest.py`.

  `csv_source.py` gained `WORKDAY_EXPORT` / `ADP_EXPORT` constants.
- **The ADP copy is made by the first warranted write, not "on first run".** So a run with
  Warrant down writes nothing at all — not even the copy. Rule 1 is then true of the file's
  existence too.
- **Stricter than BUILD.md: every write first reads the warrant back from Warrant** (governance:
  only refuses more, never less; made without asking first, then **approved by the human on
  2026-10-25**). `apply_with_warrant` writes only if `GET /warrant/:id` says
  `issued`, under `hr-record-correction`, for exactly this system, action, object, field and
  class. This holds for the immediate 200 path and for approvals found by `sync`. BUILD.md only
  says "200 → apply".
- **More statuses than BUILD.md names.** BUILD.md has blocked, held, applied, rejected and
  warrant_unreachable. Added:
  - `approved` — decided, not yet synced;
  - `expired` — lapsed, or Warrant no longer knows the approval;
  - `failed` — had a warrant but the write failed or the warrant didn't match; aborted;
  - `uncommitted` — written, but the commit was lost; `sync` retries it.

  **Rerun rule:** held, approved, applied, rejected, failed and uncommitted are never asked
  again. blocked, expired and warrant_unreachable are, since a refusal or lapse wrote nothing.
- **Any unusable answer from Warrant stops the run**, not only a connection error: a timeout,
  401, 5xx, non-JSON, or a 202 with no approval id. All of these report as `warrant_unreachable`,
  with the reason in the detail.
- **Nothing that could identify pay or a person goes to Warrant.** This is broader than BUILD.md,
  which mentions only the purpose.
  - The **request context** is `{run, mismatch_type}`. The fingerprint stays out because it is
    sha256 of the values, so a pay fingerprint can be brute-forced back to the figures.
  - The **commit result** gives old/new only for code and date columns. Pay, name and email
    columns say "changed (value not recorded)".
  - The **run report** follows the same rule.
- **Approvers are `{name, role}` per held class.** Warrant compares the typed approver with
  `approval_from` exactly (case-insensitive), so the role is kept out of the name. An unknown
  class gets the identity approver.
- **The open questions from Step 7 are resolved like this:**
  - `make run`, `approve`, `sync` and `app` use `--persist`, so the ledger lives as long as
    `state/drift.db`. `make reset` clears both. `make demo` stays on one in-memory Warrant.
  - `check_principal` refuses, before any request, a principal who is an approver.
  - No computed purpose hits a filler phrase: the integration run gets no 422.
- **`approve` accepts an employee ref or a fingerprint prefix (≥6 characters).** For an employee
  with several mismatches, it takes the one that is held.
- **What a fix writes, where BUILD.md is silent:**
  - **pay** (M2): only the column ADP pays from, by ADP's `Rate_Type`. It is refused if Workday
    has no figure of that kind.
  - **create** (M5): `Associate_ID` is `DRIFT-<employee ref>`, visibly Drift's until ADP assigns
    one. `File_Number` is the next number. `Position_ID`'s company code and `Pay_Frequency` follow
    other ADP records at the same location. The crosswalk is a source file, so it is not updated.
  - **deactivate** (M6): `Position_Status` T. `Termination_Date` is the date of the fix, unless
    ADP already has one.
- **`sync` aborts an approved warrant whose mismatch is no longer in the exports**, and records it
  `failed`. Nothing is written for a disagreement that has gone.
- **The run report has no LLM explanations.** `govern.py` must not import the explainer (rule
  test). The page (Step 10) shows them.
- **Only the tests start Warrant themselves** (BUILD.md asks for a pytest session fixture). A rule
  test checks that nothing under `drift/` uses `subprocess`, `deno`, `server.ts`, `ledger.jsonl`
  or a path into `warrant/`.

**Step 9 (preparing GATE B)**
- **A rough `make demo` exists now** (`drift/demo.py`, `cli demo`), because BUILD.md's GATE B runs
  one. Step 11 still owns the polished version: generating data, narration pacing, opening the
  page.
- **`Preferred_Name` joined `WORKDAY_COLUMNS`** (the connector contract), because the
  format-difference count reads it. A Workday export without it is now refused.
- **`Detection` has a third field, `ignored`**, the format differences. `detect_sources` reads
  each source once and passes the rows to both `normalize` and `format_differences`.
- **New `show` command and `make show`.** `govern.target_for` (was `_target`) is public, and
  `AdpTarget.row()` reads one ADP row, so the demo can narrate. The `not_built` CLI stub is gone;
  every command is built.
- **PLAN.md §1 overstates the ledger.** It says "every action, refusal, hold, approval and
  rejection lands in Warrant's hash-chained ledger". A *rejection* by an approver does
  (`warrant.refused`). A *refused request* — a 422, or a refused decision such as the principal
  trying to approve — writes no event, by Warrant's design (tested in Step 3). The demo says
  exactly this. **Answered at the gate:** keep Warrant as it is; the README (Step 13) states it
  correctly. PLAN.md itself is left as written.

**Step 10**
- **New module `drift/review.py`** (not in BUILD.md's layout). It holds what the page shows and
  its three actions, so `app.py` holds no logic and the page can be tested without Streamlit.
- **Approve on the page applies straight away** (approve, then sync — both the CLI's functions).
  The CLI keeps approve and sync separate, as BUILD.md describes.
- **The page shows the values that differ**, including pay and legal names. CLAUDE.md rule 5 is
  about what reaches the LLM and Warrant, and neither changed. The page is the decider's own
  view: an approver can't judge a pay correction without seeing the figures. The run report
  still leaves values out.
- **`.streamlit/config.toml` was added:** localhost only, no prompt, no telemetry, minimal
  toolbar.
- **The page reads optional `DRIFT_STATE_DIR` / `DRIFT_REPORTS_DIR` / `DRIFT_SOURCE_DIR`**,
  because a Streamlit script takes no command-line options. The tests use them; `make app` does
  not need them.
- **`report.shown_lines` is now built from `report.shown_facts`.** `report._changes` became the
  public `changes`.

**Step 11**
- **Warrant listens on `WARRANT_HOSTNAME`, default `127.0.0.1`.** This changes Warrant beyond
  the agreed scope; the human approved it first. Logged in `warrant/VENDORED.md`.
- **`make demo` uses its own state (`state/demo`, `reports/demo`) and a fresh random token per
  run.** BUILD.md says "resets state". Clearing everything would also wipe the persistent
  run/app state and its ledger, so the demo resets only its own.
- **The demo pauses before each beat at a terminal**, and ends at the page with Ctrl-C. Exit 130
  is treated as a clean stop for `make demo` only.
- **The CLI's directory options read the `DRIFT_*` variables** that the page reads.

## Verification debt

- **Ctrl-C was simulated, not typed.** A script sent SIGINT to the process group, which is what a
  terminal does. Nobody has yet pressed Ctrl-C in a real terminal.
- **Tested on macOS only** (bash 3.2, BSD tools). Not run on Linux.
- **Step 10: the real-browser check was done once, by Claude driving Chrome on macOS, not by the
  human.**
  - `make app` itself was not typed. The same command was run with `--server.headless true` and
    an explicit port, so `make app`'s automatic browser opening is unexercised.
  - The two download buttons were seen but not clicked (that would save files). Their contents
    come from `GET /export` and the report file, both tested elsewhere.
- ~~**Step 10: Warrant listens on all network interfaces (`0.0.0.0:8000`).**~~ *Resolved in
  Step 11: loopback by default, approved by the human, tested.*
- **Step 11: the `--pause` prompt has not been used at a real terminal.** It was tested through
  a hook, and a non-terminal run was checked not to hang. `make demo`'s automatic browser opening
  was not exercised either: it was run headless to keep extra windows off this machine. Ctrl-C
  was again sent as SIGINT to the process group by a script, not typed.
- **Step 10: the page has only ever shown template explanations.** There is no API key here
  (see Step 7).
- ~~**Step 9: `make demo` leaves `state/drift.db` behind, but its ledger was in memory.**~~
  *Resolved in Step 11: the demo keeps its own `state/demo`, and the persistent state was checked
  unchanged by sha1.*
- **Step 2:** the mutation checks (in every step since) are one-off scripts and are not in the
  suite. *(Resolved in Step 5: "no accidental extras" is now also proven by the real detector,
  with 18/18, 0 decoys and 0 extras.)*
- ~~**Step 2:** D1-2's trailing space could be trimmed away silently by the CSV reader.~~
  *Resolved in Step 4: the reader returns values untouched
  (`test_values_come_back_exactly_as_exported`), and `normalize.py` trims deliberately.*
- ~~**Step 3:** the new Warrant tests call the request handler in-process, as the existing ones do.
  Against the real process, only the manual run above was done.~~ *Resolved in Step 8: 27
  integration tests run against a real Warrant process.*
- ~~**Step 4:** the ±$1 rule would exist twice (Python and SQL).~~ *Resolved in Step 5:
  `detect.sql` takes `$pay_tolerance_cents` from `normalize.PAY_TOLERANCE_CENTS`, with boundary
  tests at +$1 (not flagged) and ±$1.01 (flagged).*
- ~~**Step 4:** a `Pair` carries names and emails, so the LLM payload must not be built from
  one.~~ *Resolved in Step 7: `explain.payload()` is an allow-list built from a `Mismatch`, and a
  test checks it carries no names, emails or pay.*
- **Step 7: no live Claude API call has been made.** This machine has no credentials (no
  `ANTHROPIC_API_KEY` in the shell or `.env`, and no `ant` CLI). The LLM path is tested only
  against a fake client. Not yet confirmed against the real API: the request shape (beta
  `messages.create` with `fallbacks="default"` and `output_config` effort + JSON schema on
  `claude-sonnet-5-5`), and the quality of the explanations.
- **Step 7:** `python -m drift.cli …` run directly doesn't read `.env`; only `scripts/dev.sh` (the
  Make targets) does. Run directly, it uses an API key only if the shell already exports one.
- **Step 8: the integration tests start Warrant with `deno run` directly, not through `dev.sh`.**
  The flags are the same. The Make targets were run by hand once (run, approve ×2, sync, run),
  not in the suite.
- **Step 8: a crash between the write and saving `uncommitted` is not handled or tested.** If the
  Python process dies at exactly that point, `drift.db` does not know the write happened. The
  next run asks for a new warrant; the target finds nothing to change and commits that. The first
  warrant is left `issued` in the ledger, with no commit or abort.
- **Step 8: the created record (M5) is an assumption.** Its `Pay_Frequency` and company code are
  inferred from other records at the same location. A real ADP would assign the id and the
  position. Not validated with anyone in payroll.
- **Step 8: `make approve NOTE="…"` breaks if the note contains a double quote** (shell quoting
  in the Makefile). The CLI itself is fine.
- ~~**Step 8: the "4 format differences ignored" count (demo beat 1) is not computed anywhere
  yet.**~~ *Resolved in Step 9: `normalize.format_differences()`, scored at exactly the 4 decoys.*
- **Two slow test runs (>120 s and >300 s of wall time)** happened while pytest itself reported
  3–4 s. Reruns took 4–5 s and the slowness couldn't be reproduced, so the cause is unknown. It
  looks environmental, not code.

## Open questions (for later steps — nothing decided yet)

- *(The Step 8 questions are answered under Deviations → Step 8, and the GATE B questions under
  Step 9.)*
- **Step 13, required by GATE B:** the README must say what the ledger records:
  - an issued, held or committed warrant, an approval and an approver's rejection are events;
  - a refused request — a policy 422, or a decision Warrant refuses — leaves no event.

  Do not copy PLAN.md §1's "every … refusal … lands in the ledger".
- *(Answered 2026-10-25: the demo uses its own state; Warrant now listens on loopback; the page
  keeps showing values to the person deciding.)*
- **Step 12 (stretch, n8n) — do it or skip it?** BUILD.md: "Only if Steps 1–11 are done by end
  of Day 4"; it is the first thing the cut line drops.

## Next step

Step 12 · n8n workflow is a **stretch**. Ask the human whether to build it or go to Step 13 ·
Documentation (README, `docs/architecture.md`, final HANDOVER). The README must carry the GATE B
wording about what the ledger records.
