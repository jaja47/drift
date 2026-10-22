# Drift — Project Plan

**An agent that finds where Workday and ADP disagree, explains why, and fixes only what it is allowed to.**

Status: planning · Repo: `~/Documents/drift` · Includes: `warrant/` (copied from `~/Documents/warrant`)
Purpose: a working demo for the SPS Commerce *AI & Automations Builder (HR)* interview with Tasha.
Deadline: 5 days. Read `BUILD.md` for the steps and `CLAUDE.md` for the rules.

---

## 1. What we're building

HR runs on several systems that are supposed to agree and slowly stop agreeing. Workday is the
system of record for people; ADP pays them. When a termination, a pay change or a reorg doesn't
make it across, nobody owns the gap — it sits between two platforms until payroll is wrong.

Drift is a reconciler for that gap:

1. **Read** a Workday-style worker export and an ADP-style payroll export (synthetic CSVs).
2. **Match** each person across both systems through an ID crosswalk, and normalise the formats
   that differ legitimately (cost-centre codes, hourly vs annual pay, email case).
3. **Detect** the real mismatches — and, just as important, *not* flag the differences that are
   only formatting.
4. **Explain** each mismatch in plain English with a likely root cause, and roll mismatches up into
   patterns ("all five cost-centre mismatches carry the 1 Oct reorg effective date — the reorg did
   not sync"). The rollup is the point: fix the pipe, not just the rows.
5. **Govern** every correction through Warrant:
   - routine fixes (cost centre, manager) get a warrant and are applied automatically;
   - sensitive fixes (pay, employment status, identity, creating or removing a payroll record) are
     **held** for a named human, who cannot be the person who authorised the run;
   - every action, refusal, hold, approval and rejection lands in Warrant's hash-chained ledger.
6. **Show** it on one page a non-technical HR leader can read in under a minute.

### The three-minute demo this must support

1. "Here are 200 employees in Workday and ADP. Drift compared them." → summary: N compared,
   18 mismatches, 4 format differences correctly ignored.
2. "Routine fixes went through on their own — each one has a warrant." → open one, show the
   per-record purpose and sources.
3. "This one is a terminated employee still active in payroll. Drift did not touch it." → held,
   waiting on a named payroll approver. Approve it as that person; it applies.
4. "Here's the pattern it found" → the reorg rollup. "That's an integration ticket for IT, not
   five manual fixes."
5. "And the record can be checked" → `/verify` on the ledger: intact.

### Non-goals

- No real Workday or ADP access. Inputs are synthetic exports; the source sits behind a connector
  interface so a Workday RaaS report or the ADP API can replace the CSV reader later. In SPS's
  setup IT would own those connections.
- No writes to anything real. "Applying" a fix writes to a local simulated ADP table.
- No authentication or multi-user system. Approver identity is a typed name — a known limit,
  stated in the docs (Warrant's README already documents it).
- No full HR data model. Only the fields needed for the mismatch types below.

---

## 2. The data

Deterministic generator (`seed=42`), about 200 employees, one Canadian/US mixed company.

**Workday-style export** (`data/source/workday_workers.csv`)
`Employee_ID, Legal_First_Name, Legal_Last_Name, Preferred_Name, Work_Email, Worker_Status
(Active|Terminated|Leave), Hire_Date, Termination_Date, Supervisory_Organization,
Manager_Employee_ID, Cost_Center (e.g. "CC-410 Supply Chain Analytics"), Location, Pay_Rate_Type
(Salary|Hourly), Annual_Base_Pay, Hourly_Rate, Currency, Comp_Effective_Date, Org_Effective_Date`

**ADP-style export** (`data/source/adp_payroll.csv`)
`Associate_ID, File_Number, Position_ID, First_Name, Last_Name, Work_Email, Position_Status
(A|T|L), Termination_Date, Home_Department (e.g. "410"), Reports_To_Associate_ID, Location_Code,
Rate_Type (Salary|Hourly), Annual_Salary, Rate_Amount, Pay_Frequency (Biweekly|Semimonthly)`

**Supporting files**
- `id_crosswalk.csv` — Workday `Employee_ID` ↔ ADP `Associate_ID`. The two systems do not share
  an ID; that is itself a real-world headache and part of the story.
- `cost_center_map.csv` — Workday cost centre ↔ ADP home department code.
- `location_map.csv` — Workday location name ↔ ADP location code.

**Ground truth** (`data/source/planted.json`) — every planted mismatch *and* every planted decoy,
with its expected type and class. Tests score detection against it. Never read by the detector.

### Planted mismatches (18) and decoys (4)

| # | Type | Count | Class | Notes |
|---|---|---|---|---|
| M1 | Terminated in Workday, active in ADP | 3 | `employment_status` | one terminated after the last payroll cutoff |
| M2 | Pay differs | 3 | `compensation` | one is a Workday comp change effective 1 Oct not carried over |
| M3 | Cost centre differs | 5 | `routine` | **all five** have `Org_Effective_Date = 2026-10-01` (the reorg) |
| M4 | Manager differs | 3 | `routine` | two are also part of the reorg |
| M5 | In Workday, missing from ADP | 2 | `employment_status` | new hires not set up in payroll |
| M6 | In ADP, missing from Workday | 1 | `identity` | possible ghost record — never auto-fixed |
| M7 | Legal name differs | 1 | `identity` | |
| D1 | Email case / whitespace only | 2 | — | must NOT be flagged |
| D2 | Hourly rate × 2080 = annual salary | 1 | — | must NOT be flagged |
| D3 | Preferred name ≠ legal first name | 1 | — | must NOT be flagged |

---

## 3. Architecture

```
data/source/*.csv
      │
 connectors/        CsvWorkdaySource, CsvAdpSource  (interface: list_workers / list_payroll)
      │
 normalize.py       crosswalk join, code maps, pay conversion, string normalisation
      │
 detect.sql         DuckDB queries, one CTE per mismatch type → mismatches table
      │
 classify.py        field → change_class from a static table (config/change_classes.yaml)
      │                — deterministic code, never the LLM
 explain.py         plain-English explanation + likely cause per mismatch (Claude API,
      │                pseudonymised input, cached; template fallback with no API key)
 patterns.py        rollups: shared effective date, department, cutoff — the root-cause view
      │
 govern.py          per mismatch: POST /warrant (policy hr-record-correction)
      │                 422 → blocked, nothing written
      │                 202 → held, approval_id stored, nothing written
      │                 200 → apply via AdpTarget, then /commit (or /abort on error)
      │             poll held approvals → once a warrant_id exists, apply + commit
 target/            AdpTarget writes to state/adp_current.csv (a copy — never the source)
      │
 state/drift.db     SQLite: runs, mismatches, warrant_id / approval_id, status
      │
 app.py             Streamlit review page
 reports/           run_<timestamp>.md
```

### One repo, two processes

```
drift/                      one repo, one README, one `make demo`
├── drift/                  the Python app (everything above)
├── warrant/                Warrant, copied in unchanged + the HR policy (Deno)
│   └── VENDORED.md         which commit it was copied from, and what changed since
├── scripts/dev.sh          starts Warrant, waits for /health, starts Drift, stops both on exit
├── Makefile
└── ...
```

Warrant lives inside the repo but still runs as **its own process** on port 8000. Drift never
imports Warrant code — it talks to it over HTTP only, exactly as `warrant/adapters/README.md`
describes for "code you own". That keeps the property that matters: the thing taking the action
is not the thing keeping the record.

Interview framing: *"In production Warrant would be a separate service owned by IT or the AI
Engineering team, shared by every HR automation. I bundled it so the demo runs with one command."*

### The Warrant policy (added to `warrant/src/policy.ts` in this repo)

```ts
export const HR_RECORD_CORRECTION: Policy = {
  id: "hr-record-correction",
  version: "1.0.0",
  title: "Correction of an employee record in a downstream HR system",
  rationale: "A correction to employee data must name who authorised it, which records it was " +
    "derived from, and why — and pay, status and identity changes need a second person.",
  fields: {
    actor: { type: "versioned_id" },                 // "drift-reconciler@v0.1.0"
    principal: { type: "named_human" },              // who authorised this reconciliation run
    system: { type: "enum", values: ["adp", "workday", "learning"] },
    action: { type: "enum", values: ["update", "create", "deactivate"] },
    object: { type: "string", minLength: 3, hint: "pseudonymous employee id, never a name" },
    field: { type: "string", minLength: 2 },
    change_class: {
      type: "enum",
      values: ["routine", "compensation", "employment_status", "identity"],
    },
    purpose: { type: "prose", minLength: 24, forbidPhrases: FILLER_PHRASES },
    sources: { type: "array", itemType: "string", minItems: 2 },  // both systems' rows
  },
  requiresApproval: [
    { when: { field: "change_class", equals: "compensation" }, because: "change_class:compensation" },
    { when: { field: "change_class", equals: "employment_status" }, because: "change_class:employment_status" },
    { when: { field: "change_class", equals: "identity" }, because: "change_class:identity" },
  ],
};
```

`sources` needs **two** entries because a reconciliation that cites only one system has not
reconciled anything.

---

## 4. Governance decisions worth explaining in the interview

1. **The classifier is not the LLM.** Which changes need a human is decided by a static
   field → class table that a person reviews, with a test proving no pay or status field can be
   classed `routine`. The LLM only writes explanations; it cannot change what happens.
2. **Data minimisation.** The LLM sees pseudonymous IDs and the differing fields only — no names,
   no emails. Pay amounts never go into a Warrant `purpose` (the ledger is exportable); the
   purpose describes the change without the figure.
3. **Fail closed.** If Warrant is unreachable, Drift writes nothing and says so.
4. **Inputs are read-only.** Fixes go to a copy. Source exports are never modified.
5. **Idempotent runs.** A mismatch has a stable fingerprint; re-running does not request a second
   warrant for something already held or applied.
6. **Honest limits.** Approver identity is not authenticated; the ledger detects edits but not
   truncation (see Warrant README). Both are named in the README, not hidden.

---

## 5. What I'd validate with HR Ops first (goes in the README)

- Which system is actually the source of truth per field? (Assumed: Workday for people and org,
  ADP for payroll mechanics.)
- Who should approve each change class at SPS — a payroll manager, an HRBP, Total Rewards?
- What is the payroll cutoff, and how often does the Workday → ADP integration run?
- Which mismatches are already known and tolerated?
- Where does IT want integration defects reported — the pattern rollup should land there.
