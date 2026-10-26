# Drift — architecture

PLAN.md §3 drew the plan. This is what was built.

## Two processes, one repo

```
┌──────────────────────────── Drift (Python) ────────────────────────────┐        ┌──── Warrant (Deno) ────┐
│                                                                        │        │                        │
│  data/source/*.csv ─► connectors/ ─► normalize.py ─► detect.sql        │        │  policy:               │
│   (read-only)          (CSV today;     (crosswalk,     (DuckDB, M1–M8) │        │  hr-record-correction  │
│                         Workday report, codes, pay,         │          │        │                        │
│                         ADP API later)  email, names)       ▼          │  HTTP  │  POST /warrant         │
│                                         classify.py ◄─ config/         │ ─────► │   200 issued           │
│                                         (class, approver) change_      │        │   202 held             │
│                                                           classes.yaml │ ◄───── │   422 refused          │
│                                         explain.py  (advisory text)    │        │  POST /approval/decide │
│                                         patterns.py (root causes)      │        │  POST /warrant/commit  │
│                                                     │                  │        │  GET  /verify, /export │
│                                         govern.py ◄─┘ ─────────────────┼───────►│                        │
│                                            │                           │        │  ledger: append-only,  │
│                         apply_with_warrant │ (the only caller)         │        │  hash-chained          │
│                                            ▼                           │        └────────────────────────┘
│                                         target/adp_target.py           │
│                                            │                           │
│                                            ▼                           │
│                                   state/adp_current.csv (a copy)       │
│                                                                        │
│  state/drift.db ◄── state.py        report.py ─► reports/run_<ts>.md   │
│  review.py ─► app.py (Streamlit)    cli.py · demo.py                   │
└────────────────────────────────────────────────────────────────────────┘
```

- **Drift never imports, starts or reads Warrant.** It calls Warrant's HTTP API and nothing else.
  `scripts/dev.sh` starts Warrant around each Make target, and the tests start their own on a
  free port. A rule test checks that nothing under `drift/` uses `subprocess`, `deno`, Warrant's
  source or its ledger file.
- **Both listen on this machine only:** Warrant on `127.0.0.1` (`WARRANT_HOSTNAME`), the review
  page on `localhost`. Every Warrant route except `/health` and `/policies` also needs the bearer
  token.
- **The CLI, the page and the demo call the same functions:** `govern.run / approve / sync /
  show`. `app.py` is layout only; `review.py` assembles what it shows.

## One mismatch through governance

```
classify (code + config) ─► POST /warrant
   │
   ├─ 422 refused ─► blocked: violations stored, nothing written ............ asked again next run
   ├─ no answer ───► warrant_unreachable: the run stops, nothing more written  asked again next run
   ├─ 200 issued ──► GET /warrant/:id — issued, this policy, this exact change?
   │                    no  ─► failed: nothing written
   │                    yes ─► write the ADP copy ─► POST /warrant/commit ─► applied
   │                             write fails ─► POST /warrant/abort ─► failed
   │                             commit lost ─► uncommitted (sync retries the commit)
   └─ 202 held ────► held: approval id stored, no warrant id, nothing written
                        approve (POST /approval/decide, as the named person)
                          approved ─► approved ─► sync ─► the same path as 200 ─► applied
                          rejected ─► rejected: never written
                          no decision in 24 h ─► expired: asked again next run
```

A **rerun** asks for no new warrant for anything held, approved, applied, rejected, failed or
uncommitted. It asks again for blocked, expired and warrant_unreachable, since those wrote nothing.

## The warrant request

Built per record from code and config, never from LLM output. These are two real requests from
the demo data, the first routine and the second held:

```json
{
  "policy_id": "hr-record-correction",
  "actor": "drift-reconciler@v0.1.0",
  "principal": "Murtaza Dibbo",
  "system": "adp",
  "action": "update",
  "object": "W10015",
  "field": "cost_center",
  "change_class": "routine",
  "purpose": "Change the ADP home department for W10015 from 410 to match their Workday cost centre, CC-420 Fulfillment Network Engineering, effective 2026-10-01",
  "sources": ["workday_workers.csv#Employee_ID=W10015", "adp_payroll.csv#Associate_ID=G3ZLBK6CJE"],
  "context": {"run": "run_20261025T235454Z", "mismatch_type": "M3"}
}
```

```json
{
  "policy_id": "hr-record-correction",
  "actor": "drift-reconciler@v0.1.0",
  "principal": "Murtaza Dibbo",
  "system": "adp",
  "action": "update",
  "object": "W10112",
  "field": "worker_status",
  "change_class": "employment_status",
  "purpose": "Terminate W10112 in ADP as of 2026-09-18 to match Workday, where they are terminated; ADP still shows them as active, so they could be paid after leaving",
  "sources": ["workday_workers.csv#Employee_ID=W10112", "adp_payroll.csv#Associate_ID=GPTEGMZWN7"],
  "context": {"run": "run_20261025T235454Z", "mismatch_type": "M1"},
  "approval_from": "Owen Castellanos"
}
```

- **What the request never carries:** a pay figure, a name, an email, or the mismatch
  fingerprint. A pay mismatch's purpose says "set to the Workday figure", without the figure.
- **`action`:** `create` for a worker missing from ADP (M5), `deactivate` for an ADP record with
  no worker (M6), `update` otherwise. Warrant holds create and deactivate whatever the class.
- **What a commit records:** the row and the columns written. Old and new values are included only
  for codes and dates; pay, name and email columns say "changed (value not recorded)".

## What is written where

| Path | Written by | Contents |
|---|---|---|
| `data/source/` | `scripts/generate_data.py` only | the synthetic exports and the answer key; Drift only reads them |
| `state/adp_current.csv` | `target/adp_target.py`, under a warrant | the simulated ADP; created by the first warranted write |
| `state/drift.db` | `state.py` | where each mismatch stands: ids, class, status, warrant / approval id — no names, emails or pay |
| `state/ledger.jsonl` | Warrant (`--persist`) | Warrant's ledger for `make run / approve / sync / show / app` |
| `state/explanations.json` | `explain.py` | cached Claude explanations (only real API answers are cached) |
| `reports/run_<ts>.md` | `report.py` | one run: counts, patterns, applied, held, blocked, `/verify` — no pay, names or emails |
| `state/demo/`, `reports/demo/` | `make demo` | the demo's own state; its ledger stays in memory |

## Where real systems would plug in

- **Sources:** `drift/connectors/base.py` defines `WorkerSource`, `PayrollSource` and `Reference`.
  A Workday report and ADP's Workers API would replace the CSV readers; normalisation, detection
  and governance would not change.
- **The target:** `AdpTarget` writes a CSV copy. A real target would call ADP, behind the same
  `apply_with_warrant` gate.
- **Warrant** would be a shared service owned by IT, used by every HR automation, with approvers
  authenticated by the identity provider instead of a typed name.
