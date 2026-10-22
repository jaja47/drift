# HANDOVER — Drift

> **Rule:** update this file at the end of every step.

## Where the build stands

| | Step | Day | State |
|---|---|---|---|
| ✅ | 1 · Scaffold | 1 | done 2026-10-21 |
| ⬜ | 2 · Synthetic data generator | 1 | |
| ⬜ | 3 · Warrant policy | 1 | |
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

## Deviations from PLAN.md / BUILD.md

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

## Verification debt

- **Ctrl-C was simulated, not typed.** A script sent SIGINT to the process group, which is what a
  terminal does. Nobody has yet pressed Ctrl-C in a real terminal.
- **Tested on macOS only** (bash 3.2, BSD tools). Not run on Linux.
- **`make data`, `make run`, `make app` and `make demo` exist but are not built yet.** `data`
  fails because `scripts/generate_data.py` doesn't exist yet. `run` and `demo` reach the `cli.py`
  stub, which exits 2.

## Open questions (for later steps — nothing decided yet)

- **Step 2:** `requirements.txt` is unpinned, as BUILD.md lists it (installed: Faker 40.43.0,
  duckdb 2.0.0). Faker's output for a given seed can change between Faker versions. "Same bytes
  twice" will hold on one machine, but a fresh clone could produce different names. Consider
  pinning Faker.
- **Step 8:** `make run` and `make app` each start their own Warrant, with an in-memory store by
  default. An approval held during `run` would not exist in the Warrant that `app` starts. Choose
  one: those targets use `--persist`, or the whole flow runs within one Warrant lifetime, as
  `make demo` does.

## Next step

Step 2 · Synthetic data generator.
