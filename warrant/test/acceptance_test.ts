/**
 * Acceptance tests. No dependencies — `deno task test` and nothing to install.
 *
 * The refusal cases matter more than the happy path. A control that fails open
 * is worse than no control, because it produces evidence you cannot rely on.
 */

import { assert, assertEquals } from "@std/assert";
import { createHandler } from "../src/server.ts";
import { MemoryStore } from "../src/store.ts";
import { canonical, GENESIS, hashEvent, verifyChain } from "../src/chain.ts";
import { CONTACT_DATA_TOUCH, isNamedHuman, validate } from "../src/policy.ts";

const TOKEN = "test-token";

function client(opts: { approvalTtlSeconds?: number } = {}) {
  const store = new MemoryStore();
  const handler = createHandler({ store, token: TOKEN, ...opts });

  const call = async (method: string, path: string, body?: unknown, auth = true) => {
    const res = await handler(
      new Request("http://x" + path, {
        method,
        headers: {
          ...(auth ? { authorization: `Bearer ${TOKEN}` } : {}),
          "content-type": "application/json",
        },
        ...(body ? { body: JSON.stringify(body) } : {}),
      }),
    );
    const text = await res.text();
    return { status: res.status, body: text ? JSON.parse(text) : null };
  };

  return { store, call };
}

const VALID = {
  policy_id: "contact-data-touch",
  actor: "enrichment-runner@v1.0.0",
  principal: "Dana Okafor",
  action: "enrich",
  object: "acct_00123",
  purpose: "Confirm the company has a board-level risk committee before proposing a briefing",
  sources: ["https://example.com/filings/2026-annual-report"],
  send: false,
};

/** Trips CONTACT_DATA_TOUCH's approval gate: send:true is held for a decision. */
const SENDING = {
  ...VALID,
  action: "message",
  send: true,
  approval_from: "Priya Raman",
  purpose: "Send the governance summary the audit lead asked to forward internally",
};

// ─────────────────────────────────────────────────────────────────────────────
// Auth
// ─────────────────────────────────────────────────────────────────────────────

Deno.test("refuses requests with no token", async () => {
  const { call } = client();
  const r = await call("GET", "/events", undefined, false);
  assertEquals(r.status, 401);
});

Deno.test("fails closed when no token is configured at all", async () => {
  const handler = createHandler({ store: new MemoryStore(), token: undefined });
  const res = await handler(
    new Request("http://x/events", { headers: { authorization: "Bearer anything" } }),
  );
  assertEquals(res.status, 401);
  await res.body?.cancel();
});

Deno.test("health and policy list are open", async () => {
  const { call } = client();
  assertEquals((await call("GET", "/health", undefined, false)).status, 200);
  const p = await call("GET", "/policies", undefined, false);
  assertEquals(p.status, 200);
  assertEquals(p.body.policies.length, 3);
});

// ─────────────────────────────────────────────────────────────────────────────
// Policy refusals — a 200 on any of these means the interlock is broken
// ─────────────────────────────────────────────────────────────────────────────

const REFUSALS: Array<[string, Record<string, unknown>, string]> = [
  [
    "filler purpose",
    { ...VALID, purpose: "Increase engagement with this account over time" },
    "purpose",
  ],
  ["purpose too short", { ...VALID, purpose: "research" }, "purpose"],
  ["another filler phrase", {
    ...VALID,
    purpose: "We want to circle back and stay top of mind here",
  }, "purpose"],
  ["unversioned actor", { ...VALID, actor: "enrichment-runner" }, "actor"],
  ["principal is a system", { ...VALID, principal: "system" }, "principal"],
  ["principal is a team", { ...VALID, principal: "the team" }, "principal"],
  ["empty sources", { ...VALID, sources: [] }, "sources"],
  ["sources not an array", { ...VALID, sources: "https://example.com" }, "sources"],
  ["action not in enum", { ...VALID, action: "delete" }, "action"],
  ["send is not a boolean", { ...VALID, send: "yes" }, "send"],
  ["send:true names nobody to ask", { ...VALID, action: "message", send: true }, "approval_from"],
  ["send:true asks a role instead of a person", {
    ...VALID,
    action: "message",
    send: true,
    approval_from: "ops",
  }, "approval_from"],
];

for (const [name, payload, expectedField] of REFUSALS) {
  Deno.test(`refuses: ${name}`, async () => {
    const { call } = client();
    const r = await call("POST", "/warrant", payload);
    assertEquals(r.status, 422, `expected 422, got ${r.status}`);
    assertEquals(r.body.error, "policy_violation");
    assert(
      r.body.violations.some((v: { field: string }) => v.field === expectedField),
      `expected a violation on '${expectedField}', got ${JSON.stringify(r.body.violations)}`,
    );
    assertEquals(r.body.warrant_id, undefined, "a refused request must not return a warrant_id");
  });
}

Deno.test("refuses an unknown policy", async () => {
  const { call } = client();
  const r = await call("POST", "/warrant", { ...VALID, policy_id: "does-not-exist" });
  assertEquals(r.status, 400);
  assertEquals(r.body.error, "unknown_policy");
});

Deno.test("a refused warrant writes no event", async () => {
  const { call, store } = client();
  await call("POST", "/warrant", { ...VALID, purpose: "engagement" });
  assertEquals((await store.all()).length, 0);
});

// ─────────────────────────────────────────────────────────────────────────────
// The governed path
// ─────────────────────────────────────────────────────────────────────────────

Deno.test("a valid request is issued a warrant", async () => {
  const { call } = client();
  const r = await call("POST", "/warrant", VALID);
  assertEquals(r.status, 200);
  assert(r.body.warrant_id.startsWith("wrt_"));
  assertEquals(r.body.state, "issued");
  assertEquals(r.body.policy, "contact-data-touch@1.0.0");
});

Deno.test("commit seals the warrant and a replay is refused", async () => {
  const { call } = client();
  const { body: issued } = await call("POST", "/warrant", VALID);

  const first = await call("POST", "/warrant/commit", {
    warrant_id: issued.warrant_id,
    result: { risk_committee: true },
  });
  assertEquals(first.status, 200);
  assertEquals(first.body.state, "committed");

  const replay = await call("POST", "/warrant/commit", {
    warrant_id: issued.warrant_id,
    result: { risk_committee: false },
  });
  assertEquals(replay.status, 409);
});

Deno.test("abort is recorded, not swallowed", async () => {
  const { call } = client();
  const { body: issued } = await call("POST", "/warrant", VALID);
  const r = await call("POST", "/warrant/abort", {
    warrant_id: issued.warrant_id,
    reason: "upstream provider returned no match",
  });
  assertEquals(r.status, 200);

  const state = await call("GET", `/warrant/${issued.warrant_id}`);
  assertEquals(state.body.state, "aborted");
  assertEquals(state.body.outcome, "upstream provider returned no match");
});

Deno.test("commit and abort on an unknown warrant are refused", async () => {
  const { call } = client();
  assertEquals((await call("POST", "/warrant/commit", { warrant_id: "wrt_nope" })).status, 404);
  assertEquals((await call("POST", "/warrant/abort", { warrant_id: "wrt_nope" })).status, 404);
});

// ─────────────────────────────────────────────────────────────────────────────
// The approval gate
//
// The property under test is not "approval is recorded" — it is that an action
// needing approval CANNOT PROCEED before a human decides. The caller is given
// no warrant_id, so there is nothing to pass to the next step.
// ─────────────────────────────────────────────────────────────────────────────

Deno.test("an action needing approval is held, and gets NO warrant_id", async () => {
  const { call } = client();
  const r = await call("POST", "/warrant", SENDING);

  assertEquals(r.status, 202);
  assertEquals(r.body.state, "pending_approval");
  assert(r.body.approval_id.startsWith("apr_"));
  assertEquals(r.body.requested_of, "Priya Raman");
  assertEquals(r.body.because, "send:true");
  assertEquals(
    r.body.warrant_id,
    undefined,
    "a held request must not hand back a warrant_id — that is the whole interlock",
  );
});

Deno.test("a held request writes a request event, but never an issue", async () => {
  const { call, store } = client();
  await call("POST", "/warrant", SENDING);

  const kinds = (await store.all()).map((e) => e.kind);
  assertEquals(kinds, ["warrant.requested"]);
});

Deno.test("polling a held approval exposes no warrant_id until it is decided", async () => {
  const { call } = client();
  const { body: held } = await call("POST", "/warrant", SENDING);

  const before = await call("GET", `/approval/${held.approval_id}`);
  assertEquals(before.status, 200);
  assertEquals(before.body.state, "pending_approval");
  assertEquals(before.body.warrant_id, undefined);

  await call("POST", "/approval/decide", {
    approval_id: held.approval_id,
    approver: "Priya Raman",
    decision: "approved",
  });

  const after = await call("GET", `/approval/${held.approval_id}`);
  assertEquals(after.body.state, "issued");
  assert(after.body.warrant_id.startsWith("wrt_"));
});

Deno.test("the named approver can approve, and only then does a warrant exist", async () => {
  const { call } = client();
  const { body: held } = await call("POST", "/warrant", SENDING);

  const decided = await call("POST", "/approval/decide", {
    approval_id: held.approval_id,
    approver: "Priya Raman",
    decision: "approved",
  });
  assertEquals(decided.status, 200);
  assertEquals(decided.body.state, "issued");
  assert(decided.body.warrant_id.startsWith("wrt_"));

  // and it behaves like any other warrant from here
  const committed = await call("POST", "/warrant/commit", {
    warrant_id: decided.body.warrant_id,
    result: { delivered: true },
  });
  assertEquals(committed.status, 200);
});

Deno.test("REFUSES: you cannot approve your own action", async () => {
  const { call } = client();
  // Dana is the principal; asking Dana to approve Dana's own send.
  const { body: held } = await call("POST", "/warrant", {
    ...SENDING,
    approval_from: "Dana Okafor",
  });

  const r = await call("POST", "/approval/decide", {
    approval_id: held.approval_id,
    approver: "Dana Okafor",
    decision: "approved",
  });
  assertEquals(r.status, 422);
  assert(
    r.body.violations.some((v: { message: string }) => v.message.includes("second person")),
    `expected a self-approval violation, got ${JSON.stringify(r.body.violations)}`,
  );
  assertEquals(r.body.warrant_id, undefined);
});

Deno.test("REFUSES: a decision from someone it was not requested of", async () => {
  const { call } = client();
  const { body: held } = await call("POST", "/warrant", SENDING); // asked of Priya Raman

  const r = await call("POST", "/approval/decide", {
    approval_id: held.approval_id,
    approver: "Sam Whitfield",
    decision: "approved",
  });
  assertEquals(r.status, 422);
  assertEquals(r.body.warrant_id, undefined);
});

Deno.test("REFUSES: an unnamed approver, or a decision that is not yes or no", async () => {
  const { call } = client();

  const a = await call("POST", "/warrant", SENDING);
  const unnamed = await call("POST", "/approval/decide", {
    approval_id: a.body.approval_id,
    approver: "ops",
    decision: "approved",
  });
  assertEquals(unnamed.status, 422);

  const b = await call("POST", "/warrant", SENDING);
  const vague = await call("POST", "/approval/decide", {
    approval_id: b.body.approval_id,
    approver: "Priya Raman",
    decision: "probably",
  });
  assertEquals(vague.status, 422);
});

Deno.test("a rejection refuses the warrant, and it can never be committed", async () => {
  const { call } = client();
  const { body: held } = await call("POST", "/warrant", SENDING);

  const r = await call("POST", "/approval/decide", {
    approval_id: held.approval_id,
    approver: "Priya Raman",
    decision: "rejected",
    note: "wrong audience for this summary",
  });
  assertEquals(r.status, 200);
  assertEquals(r.body.state, "refused");
  assertEquals(r.body.warrant_id, undefined, "a refused action must not receive a warrant_id");

  const poll = await call("GET", `/approval/${held.approval_id}`);
  assertEquals(poll.body.state, "refused");
  assertEquals(poll.body.warrant_id, undefined);
});

Deno.test("a decision cannot be revisited", async () => {
  const { call } = client();
  const { body: held } = await call("POST", "/warrant", SENDING);

  const first = await call("POST", "/approval/decide", {
    approval_id: held.approval_id,
    approver: "Priya Raman",
    decision: "rejected",
  });
  assertEquals(first.status, 200);

  const second = await call("POST", "/approval/decide", {
    approval_id: held.approval_id,
    approver: "Priya Raman",
    decision: "approved",
  });
  assertEquals(second.status, 409, "a rejected request must not be re-decided into an approval");
  assertEquals(second.body.warrant_id, undefined);
});

Deno.test("an approval that has lapsed cannot be decided", async () => {
  const { call } = client({ approvalTtlSeconds: -1 }); // already expired on arrival
  const { body: held } = await call("POST", "/warrant", SENDING);

  const r = await call("POST", "/approval/decide", {
    approval_id: held.approval_id,
    approver: "Priya Raman",
    decision: "approved",
  });
  assertEquals(r.status, 410);
  assertEquals(r.body.error, "approval_expired");
  assertEquals(r.body.warrant_id, undefined);

  const poll = await call("GET", `/approval/${held.approval_id}`);
  assertEquals(poll.body.state, "expired");
});

Deno.test("expiry is derived, so a live approval still works", async () => {
  const { call } = client({ approvalTtlSeconds: 3600 });
  const { body: held } = await call("POST", "/warrant", SENDING);
  const r = await call("POST", "/approval/decide", {
    approval_id: held.approval_id,
    approver: "Priya Raman",
    decision: "approved",
  });
  assertEquals(r.status, 200);
});

Deno.test("what is approved is what was requested — the caller cannot amend it", async () => {
  const { call } = client();
  const { body: held } = await call("POST", "/warrant", SENDING);

  const decided = await call("POST", "/approval/decide", {
    approval_id: held.approval_id,
    approver: "Priya Raman",
    decision: "approved",
    // a caller trying to smuggle a different record past the approver
    record: { object: "acct_99999", purpose: "something else entirely" },
  });

  const state = await call("GET", `/warrant/${decided.body.warrant_id}`);
  assertEquals(state.body.record.object, SENDING.object);
  assertEquals(state.body.record.purpose, SENDING.purpose);
});

Deno.test("an unknown approval id is a 404", async () => {
  const { call } = client();
  assertEquals((await call("GET", "/approval/apr_nope")).status, 404);
  assertEquals(
    (await call("POST", "/approval/decide", {
      approval_id: "apr_nope",
      approver: "Priya Raman",
      decision: "approved",
    })).status,
    404,
  );
});

Deno.test("the chain stays intact across a full approval lifecycle", async () => {
  const { call } = client();
  const { body: held } = await call("POST", "/warrant", SENDING);
  const { body: decided } = await call("POST", "/approval/decide", {
    approval_id: held.approval_id,
    approver: "Priya Raman",
    decision: "approved",
  });
  await call("POST", "/warrant/commit", {
    warrant_id: decided.warrant_id,
    result: { delivered: true },
  });

  const v = await call("GET", "/verify");
  assertEquals(v.body.verified, true);
  assertEquals(v.body.events, 4); // requested, decided, issued, committed
});

// ─────────────────────────────────────────────────────────────────────────────
// The chain
// ─────────────────────────────────────────────────────────────────────────────

Deno.test("state is a fold over events, not a stored field", async () => {
  const { call } = client();
  const { body: held } = await call("POST", "/warrant", SENDING);
  const { body: decided } = await call("POST", "/approval/decide", {
    approval_id: held.approval_id,
    approver: "Priya Raman",
    decision: "approved",
  });
  await call("POST", "/warrant/commit", { warrant_id: decided.warrant_id, result: { ok: true } });

  const state = await call("GET", `/warrant/${decided.warrant_id}`);
  assertEquals(state.body.state, "committed");
  assertEquals(state.body.event_count, 4);
  assertEquals(state.body.approvals.length, 1);
  assertEquals(state.body.approvals[0].approver, "Priya Raman");
});

Deno.test("chain verifies end to end", async () => {
  const { call } = client();
  for (let i = 0; i < 5; i++) {
    const { body } = await call("POST", "/warrant", { ...VALID, object: `acct_0000${i}` });
    await call("POST", "/warrant/commit", { warrant_id: body.warrant_id, result: { i } });
  }
  const v = await call("GET", "/verify");
  assertEquals(v.status, 200);
  assertEquals(v.body.verified, true);
  assertEquals(v.body.events, 10);
});

Deno.test("REGRESSION: the chain survives interleaved issues", async () => {
  // The failure this guards against: two overlapping writers both read the same
  // tip, build on it, and fork the chain — so verification reports tampering
  // that never happened. Writes are serialised in the store to prevent it.
  const { call } = client();
  await Promise.all(
    Array.from(
      { length: 25 },
      (_, i) => call("POST", "/warrant", { ...VALID, object: `acct_${i}` }),
    ),
  );
  const v = await call("GET", "/verify");
  assertEquals(v.body.verified, true, `chain broke: ${JSON.stringify(v.body)}`);
  assertEquals(v.body.events, 25);
});

Deno.test("verification detects an altered payload", async () => {
  const events = [
    {
      seq: 1,
      event_id: "evt_a",
      subject_id: "wrt_a",
      kind: "warrant.issued",
      ts: "2026-01-01T00:00:00.000Z",
      payload: { record: { purpose: "the original" } },
      prev_hash: GENESIS,
      hash: "",
    },
  ];
  events[0].hash = await hashEvent(events[0]);
  assertEquals((await verifyChain(events)).verified, true);

  // Someone edits the record after the fact and leaves the hash alone.
  events[0].payload = { record: { purpose: "something more flattering" } };
  const broken = await verifyChain(events);
  assertEquals(broken.verified, false);
  assert(broken.verified === false && broken.broken_at === 1);
});

Deno.test("verification detects a removed event", async () => {
  const { call, store } = client();
  await call("POST", "/warrant", VALID);
  await call("POST", "/warrant", { ...VALID, object: "acct_00124" });
  await call("POST", "/warrant", { ...VALID, object: "acct_00125" });

  const all = await store.all();
  const gapped = [all[0], all[2]]; // the middle event is gone
  const result = await verifyChain(gapped);
  assertEquals(result.verified, false);
});

// ─────────────────────────────────────────────────────────────────────────────
// hr-record-correction — the policy Drift's reconciler runs under
// ─────────────────────────────────────────────────────────────────────────────

const HR_ROUTINE = {
  policy_id: "hr-record-correction",
  actor: "drift-reconciler@v0.1.0",
  principal: "Dana Okafor",
  system: "adp",
  action: "update",
  object: "W10042",
  field: "Home_Department",
  change_class: "routine",
  purpose: "Set W10042's ADP home department to 420 to match its Workday cost centre, " +
    "changed in the 2026-10-01 reorg",
  sources: [
    "workday_workers.csv#Employee_ID=W10042",
    "adp_payroll.csv#Associate_ID=G3K7Q2ZP9X",
  ],
};

Deno.test("hr: a routine correction is issued a warrant", async () => {
  const { call } = client();
  const r = await call("POST", "/warrant", HR_ROUTINE);
  assertEquals(r.status, 200);
  assertEquals(r.body.state, "issued");
  assertEquals(r.body.policy, "hr-record-correction@1.0.0");
  assert(r.body.warrant_id.startsWith("wrt_"));

  const committed = await call("POST", "/warrant/commit", {
    warrant_id: r.body.warrant_id,
    result: { applied: true },
  });
  assertEquals(committed.status, 200);
});

for (const change_class of ["compensation", "employment_status", "identity"]) {
  Deno.test(`hr: change_class:${change_class} is held — 202, approval_id, no warrant_id`, async () => {
    const { call, store } = client();
    const r = await call("POST", "/warrant", {
      ...HR_ROUTINE,
      field: "Annual_Salary",
      change_class,
      purpose: `Correct W10042's ADP ${change_class} field so it matches the Workday record`,
      approval_from: "Priya Raman",
    });
    assertEquals(r.status, 202);
    assertEquals(r.body.state, "pending_approval");
    assertEquals(r.body.because, `change_class:${change_class}`);
    assertEquals(r.body.requested_of, "Priya Raman");
    assert(r.body.approval_id.startsWith("apr_"));
    assertEquals(r.body.warrant_id, undefined, "a held correction must not get a warrant_id");
    assertEquals((await store.all()).map((e) => e.kind), ["warrant.requested"]);
  });
}

for (const action of ["create", "deactivate"]) {
  Deno.test(`hr: action:${action} is held even when it arrives labelled routine`, async () => {
    const { call } = client();
    const r = await call("POST", "/warrant", {
      ...HR_ROUTINE,
      action,
      field: "Position_Status",
      purpose: `Record the ${action} for W10042 in ADP so payroll matches its Workday status`,
      approval_from: "Priya Raman",
    });
    assertEquals(r.status, 202);
    assertEquals(r.body.because, `action:${action}`);
    assertEquals(r.body.warrant_id, undefined);
  });
}

const HR_REFUSALS: Array<[string, Record<string, unknown>, string]> = [
  ["one source only", { ...HR_ROUTINE, sources: [HR_ROUTINE.sources[0]] }, "sources"],
  ["a generic cleanup purpose", {
    ...HR_ROUTINE,
    purpose: "Routine data cleanup across HR systems",
  }, "purpose"],
  ["a housekeeping purpose", {
    ...HR_ROUTINE,
    purpose: "Quarterly housekeeping to keep records in sync",
  }, "purpose"],
  ["a purpose too short to say anything", { ...HR_ROUTINE, purpose: "data cleanup" }, "purpose"],
  ["an unknown change_class", { ...HR_ROUTINE, change_class: "minor" }, "change_class"],
  ["a held class that names nobody to ask", {
    ...HR_ROUTINE,
    change_class: "compensation",
  }, "approval_from"],
];

for (const [name, payload, expectedField] of HR_REFUSALS) {
  Deno.test(`hr: refuses ${name}`, async () => {
    const { call, store } = client();
    const r = await call("POST", "/warrant", payload);
    assertEquals(r.status, 422, `expected 422, got ${r.status}`);
    assert(
      r.body.violations.some((v: { field: string }) => v.field === expectedField),
      `expected a violation on '${expectedField}', got ${JSON.stringify(r.body.violations)}`,
    );
    assertEquals(r.body.warrant_id, undefined);
    assertEquals(await store.all(), [], "a refused correction writes no event");
  });
}

Deno.test("hr: REFUSES an approver who is the principal of the run", async () => {
  const { call, store } = client();
  const { body: held } = await call("POST", "/warrant", {
    ...HR_ROUTINE,
    field: "Position_Status",
    change_class: "employment_status",
    purpose: "Deactivate W10042 in ADP; Workday shows the worker terminated on 2026-09-18",
    approval_from: "Dana Okafor", // the principal, asked to approve their own run
  });

  for (const approver of ["Dana Okafor", "  dana okafor "]) {
    const r = await call("POST", "/approval/decide", {
      approval_id: held.approval_id,
      approver,
      decision: "approved",
    });
    assertEquals(r.status, 422);
    assert(
      r.body.violations.some((v: { message: string }) => v.message.includes("second person")),
      `expected a self-approval violation, got ${JSON.stringify(r.body.violations)}`,
    );
    assertEquals(r.body.warrant_id, undefined);
  }
  const kinds = (await store.all()).map((e) => e.kind);
  assertEquals(kinds, ["warrant.requested"], "no issue event after a refused self-approval");
});

// ─────────────────────────────────────────────────────────────────────────────
// Units
// ─────────────────────────────────────────────────────────────────────────────

Deno.test("canonical JSON is key-order independent", () => {
  assertEquals(
    canonical({ b: 1, a: { d: 2, c: [3, { f: 4, e: 5 }] } }),
    canonical({ a: { c: [3, { e: 5, f: 4 }], d: 2 }, b: 1 }),
  );
});

Deno.test("named-human heuristic", () => {
  for (const good of ["Dana Okafor", "Jean-Luc Picard", "d.okafor@example.com", "Mary O'Brien"]) {
    assert(isNamedHuman(good), `${good} should be accepted`);
  }
  for (const bad of ["system", "the team", "ops", "bot", "", "  ", "Dana", "TBD", "n/a"]) {
    assert(!isNamedHuman(bad), `${bad} should be rejected`);
  }
});

Deno.test("policies are data — a second policy validates with the same engine", () => {
  const clean = validate(CONTACT_DATA_TOUCH, VALID);
  assertEquals(clean.length, 0);

  const dirty = validate(CONTACT_DATA_TOUCH, { ...VALID, principal: "bot", sources: [] });
  assertEquals(dirty.length, 2);
});
