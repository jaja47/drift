# Vendored copy of Warrant

This directory is a copy of Warrant, bundled so the Drift demo runs with one command. It still runs
as its **own process** (`scripts/dev.sh` starts it); Drift talks to it over HTTP only and never
imports, shells out to, or reads its code or ledger file.

|                     |                                                                                                 |
| ------------------- | ----------------------------------------------------------------------------------------------- |
| Source path         | `~/Documents/warrant`                                                                           |
| Source commit       | `f595c14103545b9b4ab38defb5fcd046b3b7ecb5` — "Make approval an actual gate rather than a claim" |
| Source tree at copy | clean (`git status` empty)                                                                      |
| Date copied         | 2026-10-21                                                                                      |
| Excluded from copy  | `.git/`, `.DS_Store`, `*.jsonl` (incl. `ledger.jsonl`), `.env`                                  |

After copying, `diff -r` against the source showed no differences apart from the excluded items, and
`deno task test` passed 43/43 in this location.

`.github/workflows/ci.yml` came across with the copy. GitHub only reads workflows from the
repository root, so it has no effect here.

The original repo at `~/Documents/warrant` is never modified.

## Changes since copy

Every change to anything in `warrant/` is listed here. Only the `hr-record-correction` policy, its
tests and the README counts are expected to change; anything else needs the human's approval first.

### 2026-10-24 · Drift Step 3 — the `hr-record-correction` policy

- **`src/policy.ts`**: added `HR_RECORD_CORRECTION` and registered it in `POLICIES`. It follows
  Drift's PLAN.md §3, with two differences the human approved before they were made:
  - **Its own filler list, `HR_FILLER_PHRASES`.** It contains the shared `FILLER_PHRASES` plus
    record-correction filler ("data cleanup", "clean up", "housekeeping", "data hygiene", "tidy up",
    "routine maintenance", "keep records/systems in sync"). The shared list is written for outreach,
    so a long "routine data cleanup…" purpose would have been issued a warrant. The two existing
    policies still use the shared list, which is unchanged.
  - **A backstop:** `action: create` and `action: deactivate` are always held, whatever
    `change_class` says. These rules come after the class rules, so `because` still names the class
    when one matches. The `rationale` now mentions adding or removing a record.
- **`test/acceptance_test.ts`**: 13 new tests in a `hr-record-correction` section:
  - a routine correction is issued;
  - compensation / employment_status / identity are each held with 202, an `approval_id` and no
    `warrant_id`;
  - create / deactivate labelled routine are held;
  - refusals: one source, a cleanup purpose, a housekeeping purpose, a too-short purpose, an unknown
    class, and a held class with no `approval_from`;
  - an approver who is the principal is refused.

  One existing assertion changed: `health and policy list are open` now expects **3** policies
  instead of 2, because adding a policy changes the count. Nothing else in the existing tests
  changed.
- **`README.md`**: the test count went from 43 to 56 (two places), "Two policies ship" became three
  (naming this one), and the layout line now says "three example policies".
- **`VENDORED.md`**: reformatted with `deno fmt` so that `deno fmt --check` passes.

Verified in this directory: `deno fmt --check`, `deno lint`, `deno check src/ test/ demo/`,
`deno task test` (56 passed) and `deno task demo` all pass, which is the same sequence as
`.github/workflows/ci.yml`.
