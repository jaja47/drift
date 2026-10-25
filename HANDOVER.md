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
- **Step 4:** the ±$1 rule exists in Python (`pay_matches`), and Step 5's `detect.sql` will need
  the same comparison. Step 5 should pass `PAY_TOLERANCE_CENTS` into the SQL as a parameter rather
  than writing 100 again; the 18/18 + 0-decoy test is what proves the two agree. The mutation
  checks were a one-off script.
- **Step 4:** a `Pair` carries names and emails because M7 and D1 need them. Step 7 must build the
  LLM payload from an explicit allow-list, never from a whole `Pair`.
- **Step 7: no live Claude API call has been made.** This machine has no credentials (no
  `ANTHROPIC_API_KEY` in the shell or `.env`, and no `ant` CLI). The LLM path is tested only
  against a fake client. Not yet confirmed against the real API: the request shape (beta
  `messages.create` with `fallbacks="default"` and `output_config` effort + JSON schema on
  `claude-sonnet-5-5`), and the quality of the explanations.
- **Step 7:** `python -m drift.cli …` run directly doesn't read `.env`; only `scripts/dev.sh` (the
  Make targets) does. Run directly, it uses an API key only if the shell already exports one.
- **Two slow test runs (>120 s and >300 s of wall time)** happened while pytest itself reported
  3–4 s. Reruns took 4–5 s and the slowness couldn't be reproduced, so the cause is unknown. It
  looks environmental, not code.

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

Step 8 · Governance and apply (`target/adp_target.py`, `govern.py`, `cli.py run / approve / sync`,
approvers in `change_classes.yaml`, integration tests against a real Warrant process). See the
Step 8 open questions above.
