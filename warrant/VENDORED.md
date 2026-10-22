# Vendored copy of Warrant

This directory is a copy of Warrant, bundled so the Drift demo runs with one command. It still runs
as its **own process** (`scripts/dev.sh` starts it); Drift talks to it over HTTP only and never
imports, shells out to, or reads its code or ledger file.

| | |
|---|---|
| Source path | `~/Documents/warrant` |
| Source commit | `f595c14103545b9b4ab38defb5fcd046b3b7ecb5` — "Make approval an actual gate rather than a claim" |
| Source tree at copy | clean (`git status` empty) |
| Date copied | 2026-10-21 |
| Excluded from copy | `.git/`, `.DS_Store`, `*.jsonl` (incl. `ledger.jsonl`), `.env` |

After copying, `diff -r` against the source showed no differences apart from the excluded items, and
`deno task test` passed 43/43 in this location.

`.github/workflows/ci.yml` came across with the copy. GitHub only reads workflows from the
repository root, so it has no effect here.

The original repo at `~/Documents/warrant` is never modified.

## Changes since copy

Every change to anything in `warrant/` is listed here. Only the `hr-record-correction` policy, its
tests and the README counts are expected to change; anything else needs the human's approval first.

_None._
