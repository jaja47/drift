# CLAUDE.md — rules for working on Drift

Read `PLAN.md` (why and what), then `BUILD.md` (the steps), then `HANDOVER.md` (what has actually
happened). Work one BUILD step at a time, in order.

## Working agreement

- **One step, then stop and report.** After each step: run the tests, commit, update
  `HANDOVER.md`, and tell the human what was done and what was verified.
- **Gates are real.** At a GATE step, ask the questions listed and wait. Do not continue on
  assumed answers.
- **Say what you did not verify.** If something was only unit-tested against mocks, or not run at
  all, write that in `HANDOVER.md` under "Verification debt". Never claim something works that
  was not run.
- **Deviations are recorded.** If you depart from PLAN.md or BUILD.md, note what and why in
  `HANDOVER.md` before moving on. Ask first if the change touches governance behaviour.
- **Update `HANDOVER.md` at the end of every step.**

## Hard rules (governance — never relax these)

1. **Fail closed.** No code path writes to `state/adp_current.csv` without a `warrant_id` that came
   from Warrant in this run or from a decided approval. Warrant unreachable → write nothing.
2. **A `202` is not a success.** Held means nothing is written. There is no "pending" apply.
3. **The LLM never decides.** `change_class`, the approver and whether a fix is applied come from
   code and `config/change_classes.yaml`. LLM output is display text only.
4. **Unknown → strictest.** An unrecognised field or type is classed `identity`, never `routine`.
5. **Data minimisation.** The LLM receives pseudonymous refs and differing fields only — no names,
   emails or pay figures. Warrant `purpose` strings never contain pay figures.
6. **Source exports are read-only.** Only the copy in `state/` is ever modified.
7. **Purposes are computed per record.** Never a hardcoded purpose string.
8. **Synthetic data only.** Fictional people, fictional approvers. No real company or person data
   anywhere in the repo.

## Code conventions

- Python 3.11+, type hints, standard library first. Dependencies only from `requirements.txt`;
  ask before adding one.
- SQL lives in `detect.sql`, not in Python strings.
- Tests in `tests/`, run with `make test`. Detection is always scored against
  `data/source/planted.json`; the detector itself must never read that file.
- **Warrant lives in `./warrant/` and runs as a separate process.** Drift talks to it over HTTP
  only — never import, shell out to, or read Warrant's code or ledger file from Python. The only
  intended Warrant changes are the `hr-record-correction` policy, its tests and README counts;
  anything more, ask first. Log every change in `warrant/VENDORED.md`. Its full test suite must
  keep passing (`make test` runs it).
- Never touch the original `~/Documents/warrant` repo.
- Keep the CLI and the Streamlit page calling the same functions — no logic in `app.py`.

## Running Warrant locally

Normally you don't start it by hand — every Make target that needs it goes through
`scripts/dev.sh`, which starts Warrant, waits for `/health`, runs the command and stops Warrant
on exit. To run it alone:

```bash
make warrant      # Warrant only, foreground, http://localhost:8000, token from .env
```
