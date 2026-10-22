# Warrant

[![CI](https://github.com/jaja47/warrant/actions/workflows/ci.yml/badge.svg)](https://github.com/jaja47/warrant/actions/workflows/ci.yml)

**A fail-closed audit interlock for agent actions.**

Automated systems increasingly take real actions against real records — enriching a customer row,
scoring a lead, drafting and sending a message. The usual answer to "what did it do, and on whose
authority?" is a log written _after_ the fact, in a store that permits updates, with a `purpose`
field reading `"engagement"`.

That is not evidence. It is a story about what happened, told by the thing that did it.

Warrant inverts the order. An action must obtain a **warrant** before it runs. The warrant names the
acting agent and its version, the human who authorised this class of action, what is being done to
which record, a purpose specific enough to be falsifiable, and the sources consulted. If the policy
is not satisfied, no warrant is issued — and a caller with no warrant has nothing to pass to the
next step, so the next step does not run.

The interlock is a `422`. That is deliberately unglamorous, and it is why it works against tools you
do not control.

```
deno task demo     # narrated walkthrough, no setup
deno task test     # 43 tests, no dependencies
deno task dev      # http://localhost:8000
```

---

## The shape

```
POST /warrant  ──────────────►  policy check
                                     │
                  ┌──────────────────┴──────────────────┐
                  │                                     │
            422, no id                            warrant_id
                  │                                     │
        the action never runs                    the action runs
        nothing is written                              │
                                        ┌───────────────┴───────────────┐
                                        │                               │
                                POST /warrant/commit           POST /warrant/abort
                                  result sealed                 failure recorded
                                        │                               │
                                        └───────────────┬───────────────┘
                                                        │
                                            append-only, hash-chained
                                                GET /verify · /export
```

Two phases, write-ahead. The record exists before the action is considered complete, and if the
record cannot be written the action does not happen.

---

## Three decisions worth explaining

### 1. `prev_hash` is inside the hashed content

The obvious way to build a tamper-evident log is a row per action whose hash is recomputed when the
row is updated. That design breaks under concurrency: two overlapping actions freeze each other's
intermediate hashes, and verification then reports tampering nobody committed.

A tamper-evident log that cries wolf is worse than no log, because it discredits itself at precisely
the moment you need it. So the log is append-only, every event's hash is fixed at write time, and
`prev_hash` is part of the hashed content — nothing appended later can change what came before.
Writes are serialised through the store so two writers cannot build on the same tip and fork the
chain.

`test/acceptance_test.ts` keeps a regression for this: 25 concurrent issues, one intact chain.

### 2. Policies are data

A policy is a plain object declaring which fields a warrant must carry and what shape each one has:

```ts
export const CONTACT_DATA_TOUCH: Policy = {
  id: "contact-data-touch",
  fields: {
    actor: { type: "versioned_id" },
    principal: { type: "named_human" },
    action: { type: "enum", values: ["create", "update", "enrich", "score", "message"] },
    purpose: { type: "prose", minLength: 24, forbidPhrases: FILLER_PHRASES },
    sources: { type: "array", itemType: "string", minItems: 1 },
    send: { type: "boolean" },
  },
  conditional: [{
    when: { field: "send", equals: true },
    require: { approver: { type: "named_human" } },
    because: "send:true",
  }],
};
```

Adding a governance rule means adding an object. The server does not change. Two policies ship as
working examples — one for contact-data actions, one for publishing model-generated content —
because a policy engine that has only ever run one policy is not a policy engine.

### 3. Two of the field types encode judgement, not syntax

`named_human` rejects `"system"`, `"the team"`, `"ops"`, `"bot"` and `"TBD"`. Accountability that
resolves to a role resolves to nobody.

`prose` enforces a minimum length **and** rejects 26 filler phrases — `"engagement"`,
`"touch base"`, `"circle back"`, `"stay top of mind"`. A purpose you could paste onto any record in
the table is not a purpose. This is the one check that consistently surprises people, and it is the
one that does the most work: it makes vagueness a _runtime error_ rather than a code-review comment.

### 4. A held action gets no id at all

The obvious way to gate on approval is to issue the warrant in a `pending` state and refuse the
commit until someone approves. That is wrong, and the reason is worth stating: a caller that checks
only _"did I get an id?"_ would act, and the record would then be written after the fact — which is
the exact failure this service exists to prevent.

So a held request returns `202` with an `approval_id` and **no `warrant_id`**. There is no flag to
overlook, because there is no id to overlook. The caller polls `GET /approval/:id`, and a
`warrant_id` appears there only once a human has decided.

Three rules make the decision mean something:

- **The approver cannot be the principal.** Nobody signs off on their own action.
- **The approver must be the person it was requested of**, or the requester picks a friendlier
  approver after the fact.
- **Approvals lapse** after `WARRANT_APPROVAL_TTL`. Expiry is _derived at read time_, not written by
  a sweeper job — so there is no window in which an overdue approval still works because a cron task
  is behind.

On approval the held contents are reissued verbatim, so what was approved is what was requested; the
caller cannot amend the record between the ask and the answer.

---

## Integrating

The interlock works with anything that can make an HTTP call and branch on the result — which
includes SaaS tools whose source you will never see. See [`adapters/`](adapters/) for three shapes:

| Shape                   | How the interlock lands                                                                  |
| ----------------------- | ---------------------------------------------------------------------------------------- |
| **Your own code**       | `governed()` wrapper — the body is unreachable if the warrant is refused                 |
| **A workflow engine**   | a step that throws on `422`; downstream steps never execute                              |
| **A no-code data tool** | first column calls `/warrant`; every later column runs only if `warrant_id` is non-empty |

The third is the interesting one, because it is the case where you have no ability to modify the
tool. The interlock still holds: the tool cannot invent a `warrant_id` it was not given.

---

## API

|        |                    |                                                                  |
| ------ | ------------------ | ---------------------------------------------------------------- |
| `GET`  | `/health`          | open                                                             |
| `GET`  | `/policies`        | open — registered policies and what each requires                |
| `POST` | `/warrant`         | issue. `422` with violations, `202` if held, else a `warrant_id` |
| `POST` | `/warrant/commit`  | seal the result. `409` on replay                                 |
| `POST` | `/warrant/abort`   | record the failure — still an event, still chained               |
| `POST` | `/approval/decide` | the only route that turns a held request into a warrant          |
| `GET`  | `/approval/:id`    | poll a held request. No `warrant_id` until approved              |
| `GET`  | `/warrant/:id`     | current state, folded from events                                |
| `GET`  | `/events`          | raw log                                                          |
| `GET`  | `/verify`          | recompute every hash from genesis                                |
| `GET`  | `/export`          | JSONL evidence package                                           |

All routes except `/health` and `/policies` require `Authorization: Bearer $WARRANT_TOKEN`. **If
`WARRANT_TOKEN` is unset the service refuses everything** rather than running open — an unconfigured
deployment is not an authorised one.

```bash
export WARRANT_TOKEN=$(openssl rand -hex 32)
deno task dev
```

| Variable               | Default                 |                                         |
| ---------------------- | ----------------------- | --------------------------------------- |
| `WARRANT_TOKEN`        | _(none — fails closed)_ | bearer token                            |
| `WARRANT_STORE`        | `jsonl`                 | `jsonl` or `memory`                     |
| `WARRANT_LEDGER`       | `./ledger.jsonl`        | log path                                |
| `PORT`                 | `8000`                  |                                         |
| `WARRANT_APPROVAL_TTL` | `86400`                 | seconds a held approval stays decidable |

---

## What this is not

Everything below was confirmed by running it, not assumed.

**Not distributed.** The default store is an append-only file, correct for one process writing one
file. Running two instances against the same file corrupts the ledger — and the dangerous part is
how it fails: each instance serves `/verify` from its own in-memory cache, so **both report
`verified: true` while the file on disk is already broken.** A fresh reader over that file reports
`verified: false`. Nothing stops you from starting the second instance. Multi-instance deployment
needs a store with a real atomic append; the `Store` interface is three methods with no update path,
so swapping it is contained.

**Truncation is not detected.** The chain is verified forward from genesis, so a prefix of a valid
chain is itself a valid chain — verification has no idea how long the log is supposed to be.
Deleting entries from the _end_ of the ledger leaves `GET /verify` reporting clean. That is the
cheapest possible way to make a record disappear, and it currently works. The fix is to persist the
expected tip and event count, or to anchor the tip somewhere external.

**Not a substitute for a signed log.** In-place edits _are_ caught — altering a stored event breaks
its hash and `/verify` names the offending sequence number. What the chain does not stop is someone
who controls the process rewriting the whole file and recomputing every hash. Defending against that
means signing events with a key the writer cannot reach.

**Approval gates the sequence, but does not authenticate the approver.** No warrant exists until a
decision is recorded, the approver must be a second person, and the request lapses — all enforced,
all tested. What is _not_ enforced is that the decision actually came from the named human: anyone
holding the bearer token can post a decision claiming to be Priya Raman. Nothing is delivered to her
either; there is no notification channel. The gate is a real state machine on top of an unverified
identity, and it is only as strong as that identity. Fix it with the entry above.

**Not an authorisation system.** Warrant records that a named human authorised a _class_ of action.
It does not verify they exist — `principal` is only a format check. `Fake Person` passes, and so
does `Sales Team`, which slips past the role blocklist. Wire it to your identity provider before
relying on it for anything that matters.

**The filler-phrase list is opinionated, English-only, and matches raw substrings.** An accounting
firm cannot describe an `engagement letter`, and `check inventory levels` trips on `check in`.
Meanwhile a synonym or a single typo walks straight through. It catches carelessness, not intent.
Edit `FILLER_PHRASES`.

---

## Layout

```
src/policy.ts      policy types, the generic validator, two example policies
src/chain.ts       canonical JSON, hashing, chain verification
src/store.ts       Store interface — append-only, no update path
src/server.ts      HTTP surface
test/              43 acceptance tests, zero dependencies
demo/walkthrough   narrated run: refusal, governed action, tamper detection
adapters/          integration notes for three deployment shapes
```

MIT.
