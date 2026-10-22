/**
 * A narrated walkthrough. No server, no database, no setup:
 *
 *   deno task demo
 *
 * Shows the three things worth seeing — a refusal that produces no warrant and
 * no event, a governed action end to end, and a chain that catches an edit made
 * after the fact.
 */

import { createHandler } from "../src/server.ts";
import { MemoryStore } from "../src/store.ts";
import { verifyChain } from "../src/chain.ts";

const TOKEN = "demo-token";
const store = new MemoryStore();
const handler = createHandler({ store, token: TOKEN });

const call = async (method: string, path: string, body?: unknown) => {
  const res = await handler(
    new Request("http://demo" + path, {
      method,
      headers: { authorization: `Bearer ${TOKEN}`, "content-type": "application/json" },
      ...(body ? { body: JSON.stringify(body) } : {}),
    }),
  );
  return { status: res.status, body: JSON.parse(await res.text()) };
};

const dim = (s: string) => `\x1b[2m${s}\x1b[0m`;
const red = (s: string) => `\x1b[31m${s}\x1b[0m`;
const green = (s: string) => `\x1b[32m${s}\x1b[0m`;
const bold = (s: string) => `\x1b[1m${s}\x1b[0m`;

function step(n: number, title: string) {
  console.log(`\n${bold(`${n}. ${title}`)}\n${dim("─".repeat(64))}`);
}

const BASE = {
  policy_id: "contact-data-touch",
  actor: "enrichment-runner@v1.0.0",
  principal: "Dana Okafor",
  action: "enrich",
  object: "acct_00123",
  sources: ["https://example.com/filings/2026-annual-report"],
  send: false,
};

// ─────────────────────────────────────────────────────────────────────────────

console.log(bold("\nWarrant — walkthrough"));
console.log(dim("A fail-closed audit interlock for agent actions.\n"));

step(1, "An action with a purpose nobody can falsify is refused");
{
  const r = await call("POST", "/warrant", {
    ...BASE,
    purpose: "Increase engagement with this account",
  });
  console.log(`   ${red(`HTTP ${r.status}`)}  ${r.body.error}`);
  for (const v of r.body.violations) console.log(`   ${dim("·")} ${v.field}: ${v.message}`);
  console.log(
    `\n   ${dim("No warrant_id was returned, so a caller has nothing to pass downstream.")}`,
  );
  console.log(
    `   ${dim(`Events written: ${(await store.all()).length}. The action never happened.`)}`,
  );
}

step(2, "A specific purpose, from a named human, with sources — issued");
let warrantId = "";
{
  const r = await call("POST", "/warrant", {
    ...BASE,
    purpose: "Confirm a board-level risk committee exists before proposing an executive briefing",
  });
  warrantId = r.body.warrant_id;
  console.log(`   ${green(`HTTP ${r.status}`)}  ${r.body.warrant_id}`);
  console.log(`   ${dim(`policy ${r.body.policy} · hash ${r.body.hash.slice(0, 16)}…`)}`);
  console.log(`\n   ${dim("The record exists BEFORE the action runs. That is the whole point:")}`);
  console.log(`   ${dim("a write-ahead reserve, not a log line written afterwards.")}`);
}

step(3, "The action runs, then commits its result");
{
  const r = await call("POST", "/warrant/commit", {
    warrant_id: warrantId,
    result: { risk_committee: true, employees: 48000 },
    sources: ["https://example.com/filings/2026-annual-report#governance"],
  });
  console.log(`   ${green(`HTTP ${r.status}`)}  state: ${r.body.state}`);

  const replay = await call("POST", "/warrant/commit", { warrant_id: warrantId, result: {} });
  console.log(`   ${red(`HTTP ${replay.status}`)}  ${replay.body.message}`);
  console.log(`\n   ${dim("State is a fold over an append-only log, so a replay cannot quietly")}`);
  console.log(`   ${dim("overwrite a sealed record.")}`);
}

step(4, "Anything that sends is HELD until a human decides");
{
  const SEND = {
    ...BASE,
    action: "message",
    send: true,
    purpose: "Send the governance summary the audit lead asked to forward internally",
  };

  const anon = await call("POST", "/warrant", SEND);
  console.log(`   ${red(`HTTP ${anon.status}`)}  send:true naming nobody to ask`);
  for (const v of anon.body.violations) console.log(`   ${dim("·")} ${v.field}: ${v.message}`);

  const held = await call("POST", "/warrant", { ...SEND, approval_from: "Priya Raman" });
  console.log(
    `\n   ${green(`HTTP ${held.status}`)}  ${held.body.state} — ${held.body.approval_id}`,
  );
  console.log(
    `   ${dim(`requested of ${held.body.requested_of}, lapses ${held.body.expires_at}`)}`,
  );
  console.log(
    `   ${bold("warrant_id:")} ${held.body.warrant_id === undefined ? red("none issued") : "?"}`,
  );
  console.log(`\n   ${dim("The caller has nothing to pass downstream. Not a flag it could")}`);
  console.log(`   ${dim("ignore — there is no id to ignore. The send cannot happen.")}`);

  const self = await call("POST", "/approval/decide", {
    approval_id: held.body.approval_id,
    approver: "Dana Okafor",
    decision: "approved",
  });
  console.log(`\n   ${red(`HTTP ${self.status}`)}  Dana authorised it, so Dana cannot approve it`);
  for (const v of self.body.violations) console.log(`   ${dim("·")} ${v.field}: ${v.message}`);

  const wrong = await call("POST", "/approval/decide", {
    approval_id: held.body.approval_id,
    approver: "Sam Whitfield",
    decision: "approved",
  });
  console.log(`   ${red(`HTTP ${wrong.status}`)}  it was requested of Priya, not Sam`);

  const ok = await call("POST", "/approval/decide", {
    approval_id: held.body.approval_id,
    approver: "Priya Raman",
    decision: "approved",
  });
  console.log(`\n   ${green(`HTTP ${ok.status}`)}  Priya approved — ${ok.body.warrant_id}`);
  console.log(`   ${dim("Only now does a warrant exist, and only now can the send run.")}`);
}

step(5, "The log is tamper-evident");
{
  const clean = await verifyChain(await store.all());
  console.log(
    `   ${green("verified")}  ${
      clean.verified ? `${clean.events} events, chain intact` : "broken"
    }`,
  );

  // Edit a committed record the way someone would if they wanted a nicer story.
  const events = structuredClone(await store.all());
  const target = events.find((e) => e.kind === "warrant.issued")!;
  (target.payload as Record<string, { purpose: string }>).record.purpose =
    "Something that reads better in the audit review";

  const broken = await verifyChain(events);
  console.log(
    `   ${red("altered")}   ${
      broken.verified ? "NOT DETECTED" : `detected at event ${broken.broken_at}`
    }`,
  );
  if (!broken.verified) console.log(`   ${dim(`             ${broken.reason}`)}`);
}

step(6, "Policies are data, not code");
{
  const r = await call("GET", "/policies");
  for (const p of r.body.policies) {
    console.log(`   ${bold(p.id)}@${p.version}`);
    console.log(`   ${dim(p.rationale)}`);
    console.log(`   ${dim(`requires: ${p.required.join(", ")}`)}`);
    for (const c of p.conditional) {
      console.log(`   ${dim(`when ${c.when} → also requires ${c.then_require.join(", ")}`)}`);
    }
    for (const a of p.requires_approval) {
      console.log(`   ${dim(`when ${a.when} → ${a.then}`)}`);
    }
    console.log();
  }
  console.log(`   ${dim("Adding a governance rule means adding an object to src/policy.ts.")}`);
  console.log(`   ${dim("The server does not change.")}`);
}

console.log(`\n${dim("─".repeat(64))}`);
console.log(`${dim("Full event log:")} deno task dev, then GET /export\n`);
