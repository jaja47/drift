/**
 * Warrant — a fail-closed audit interlock for agent actions.
 *
 * Two-phase, write-ahead:
 *
 *   POST /warrant          issue   → warrant_id, or 4xx and NOTHING runs
 *   POST /warrant/commit   seal the result
 *   POST /warrant/abort    record the failure — still an event, still chained
 *
 * The interlock is the 4xx. A caller that cannot obtain a warrant has nothing
 * to pass to the next step, so the next step does not run. That works whether
 * the caller is a script you wrote or a SaaS tool you do not control.
 *
 * Run:   deno task dev
 * Test:  deno task test
 */

import {
  isNamedHuman,
  matchedApprovalRule,
  POLICIES,
  recordFields,
  validate,
  type Violation,
} from "./policy.ts";
import { verifyChain } from "./chain.ts";
import { JsonlStore, MemoryStore, type Store } from "./store.ts";

// ─────────────────────────────────────────────────────────────────────────────
// State folding. Current state is derived from the event log; it is never stored.
// ─────────────────────────────────────────────────────────────────────────────

type WarrantState =
  | "pending_approval"
  | "expired"
  | "refused"
  | "issued"
  | "committed"
  | "aborted";

interface Folded {
  warrant_id: string;
  state: WarrantState;
  policy_id: string | null;
  record: Record<string, unknown> | null;
  outcome: unknown;
  approvals: Array<Record<string, unknown>>;
  approval_id: string | null;
  requested_of: string | null;
  expires_at: string | null;
  event_count: number;
  created_at: string;
  last_event_at: string;
}

/** How long a pending approval stays decidable. */
const DEFAULT_APPROVAL_TTL_SECONDS = 24 * 60 * 60;

function fold(
  events: Awaited<ReturnType<Store["all"]>>,
  now: number = Date.now(),
): Folded | null {
  if (events.length === 0) return null;
  let state: WarrantState = "issued";
  let record: Record<string, unknown> | null = null;
  let policy_id: string | null = null;
  let outcome: unknown = null;
  let approval_id: string | null = null;
  let requested_of: string | null = null;
  let expires_at: string | null = null;
  const approvals: Array<Record<string, unknown>> = [];

  const str = (v: unknown) => (v === undefined || v === null ? null : String(v));

  for (const e of events) {
    const p = e.payload as Record<string, unknown>;
    switch (e.kind) {
      case "warrant.requested":
        state = "pending_approval";
        record = p.record as Record<string, unknown>;
        policy_id = str(p.policy_id);
        approval_id = str(p.approval_id);
        requested_of = str(p.requested_of);
        expires_at = str(p.expires_at);
        break;
      case "warrant.issued":
        state = "issued";
        record = p.record as Record<string, unknown>;
        policy_id = str(p.policy_id);
        break;
      case "warrant.refused":
        state = "refused";
        outcome = p.reason ?? null;
        break;
      case "warrant.committed":
        state = "committed";
        outcome = p.result ?? null;
        break;
      case "warrant.aborted":
        state = "aborted";
        outcome = p.reason ?? null;
        break;
      case "approval.decided":
        approvals.push({ kind: e.kind, ...p });
        break;
    }
  }

  // Expiry is DERIVED, never written. There is no sweeper job to fall behind
  // and no window in which a lapsed approval is still decidable — a pending
  // warrant past its deadline is expired the instant anything reads it.
  if (state === "pending_approval" && expires_at && now > Date.parse(expires_at)) {
    state = "expired";
  }

  return {
    warrant_id: events[0].subject_id,
    state,
    policy_id,
    record,
    outcome,
    approvals,
    approval_id,
    requested_of,
    expires_at,
    event_count: events.length,
    created_at: events[0].ts,
    last_event_at: events.at(-1)!.ts,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// HTTP
// ─────────────────────────────────────────────────────────────────────────────

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body, null, 2) + "\n", {
    status,
    headers: { "content-type": "application/json" },
  });

/** The HTTP handler `createHandler` returns. */
export type Handler = (req: Request) => Promise<Response>;

export interface HandlerOptions {
  store: Store;
  /** Bearer token. When unset, the service refuses every authenticated route. */
  token?: string;
  /** Seconds a pending approval stays decidable. Default 24h. */
  approvalTtlSeconds?: number;
}

export function createHandler(
  { store, token, approvalTtlSeconds = DEFAULT_APPROVAL_TTL_SECONDS }: HandlerOptions,
): Handler {
  const authorised = (req: Request): boolean => {
    if (!token) return false; // fail closed rather than open
    return req.headers.get("authorization") === `Bearer ${token}`;
  };

  /**
   * Resolve an approval id to the warrant it is holding.
   *
   * This scans the log, which is O(n) per call — acceptable while the store is
   * a single file, and the first thing an indexed store fixes.
   */
  const warrantForApproval = async (approval_id: string): Promise<string | null> => {
    if (!approval_id) return null;
    for (const e of await store.all()) {
      if (e.kind !== "warrant.requested") continue;
      if ((e.payload as Record<string, unknown>).approval_id === approval_id) return e.subject_id;
    }
    return null;
  };

  /** Case- and whitespace-insensitive name comparison. */
  const samePerson = (a: unknown, b: unknown): boolean =>
    String(a ?? "").trim().toLowerCase() === String(b ?? "").trim().toLowerCase();

  return async function handler(req: Request): Promise<Response> {
    const url = new URL(req.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";

    // ── open routes ─────────────────────────────────────────────────────────
    if (path === "/" || path === "/health") {
      return json({ ok: true, service: "warrant", version: "1.0.0" });
    }

    if (path === "/policies" && req.method === "GET") {
      return json({
        ok: true,
        policies: Object.values(POLICIES).map((p) => ({
          id: p.id,
          version: p.version,
          title: p.title,
          rationale: p.rationale,
          required: Object.keys(p.fields),
          conditional: (p.conditional ?? []).map((c) => ({
            when: `${c.when.field} == ${JSON.stringify(c.when.equals)}`,
            then_require: Object.keys(c.require),
          })),
          requires_approval: (p.requiresApproval ?? []).map((r) => ({
            when: `${r.when.field} == ${JSON.stringify(r.when.equals)}`,
            then: "held — no warrant is issued until a named human decides",
          })),
        })),
      });
    }

    // ── everything below needs a token ──────────────────────────────────────
    if (!authorised(req)) {
      return json({ ok: false, error: "unauthorized" }, 401);
    }

    let body: Record<string, unknown> = {};
    if (req.method === "POST") {
      try {
        body = await req.json();
      } catch {
        return json({ ok: false, error: "invalid_json" }, 400);
      }
    }

    // ── POST /warrant — issue ───────────────────────────────────────────────
    if (path === "/warrant" && req.method === "POST") {
      const policy = POLICIES[String(body.policy_id ?? "")];
      if (!policy) {
        return json({
          ok: false,
          error: "unknown_policy",
          message: `no policy '${body.policy_id}'`,
          available: Object.keys(POLICIES),
        }, 400);
      }

      const violations = validate(policy, body);
      if (violations.length > 0) {
        // 422 is the interlock. Callers get no warrant_id, so nothing downstream runs.
        return json({
          ok: false,
          error: "policy_violation",
          policy: `${policy.id}@${policy.version}`,
          violations,
        }, 422);
      }

      const warrant_id = "wrt_" + crypto.randomUUID().replace(/-/g, "").slice(0, 20);
      const contents = {
        policy_id: policy.id,
        policy_version: policy.version,
        record: recordFields(policy, body),
        context: body.context ?? null,
      };

      // ── held for a human decision ─────────────────────────────────────────
      const rule = matchedApprovalRule(policy, body);
      if (rule) {
        const approval_id = "apr_" + crypto.randomUUID().replace(/-/g, "").slice(0, 16);
        const expires_at = new Date(Date.now() + approvalTtlSeconds * 1000).toISOString();

        await store.append({
          subject_id: warrant_id,
          kind: "warrant.requested",
          payload: {
            ...contents,
            approval_id,
            requested_of: body.approval_from,
            because: rule.because,
            expires_at,
          },
        });

        // No warrant_id, deliberately. The caller has nothing to pass to the
        // next step, which IS the interlock. Returning an id here alongside a
        // "pending" flag would let any caller that merely checks for an id act
        // before the decision — and the record would then be written after the
        // fact, which is the one thing this service exists to prevent.
        return json({
          ok: true,
          state: "pending_approval",
          approval_id,
          requested_of: body.approval_from,
          because: rule.because,
          expires_at,
          message: `Held for a decision by ${body.approval_from}. No warrant_id exists yet — ` +
            `poll GET /approval/${approval_id}.`,
        }, 202);
      }

      // ── no approval required — issue immediately ──────────────────────────
      const event = await store.append({
        subject_id: warrant_id,
        kind: "warrant.issued",
        payload: contents,
      });

      return json({
        ok: true,
        warrant_id,
        state: "issued",
        policy: `${policy.id}@${policy.version}`,
        event_id: event.event_id,
        hash: event.hash,
        ts: event.ts,
      });
    }

    // ── POST /warrant/commit ────────────────────────────────────────────────
    if (path === "/warrant/commit" && req.method === "POST") {
      const id = String(body.warrant_id ?? "");
      const events = await store.bySubject(id);
      if (events.length === 0) return json({ ok: false, error: "unknown_warrant" }, 404);

      const state = fold(events)!;
      if (state.state !== "issued") {
        return json({
          ok: false,
          error: "invalid_transition",
          message: `cannot commit a warrant in state '${state.state}'`,
        }, 409);
      }

      const event = await store.append({
        subject_id: id,
        kind: "warrant.committed",
        payload: { result: body.result ?? null, sources: body.sources ?? null },
      });
      return json({
        ok: true,
        warrant_id: id,
        state: "committed",
        event_id: event.event_id,
        hash: event.hash,
      });
    }

    // ── POST /warrant/abort ─────────────────────────────────────────────────
    if (path === "/warrant/abort" && req.method === "POST") {
      const id = String(body.warrant_id ?? "");
      const events = await store.bySubject(id);
      if (events.length === 0) return json({ ok: false, error: "unknown_warrant" }, 404);

      const state = fold(events)!;
      if (state.state !== "issued") {
        return json({
          ok: false,
          error: "invalid_transition",
          message: `cannot abort a warrant in state '${state.state}'`,
        }, 409);
      }

      const event = await store.append({
        subject_id: id,
        kind: "warrant.aborted",
        payload: { reason: body.reason ?? "unspecified" },
      });
      return json({ ok: true, warrant_id: id, state: "aborted", event_id: event.event_id });
    }

    // ── POST /approval/decide ───────────────────────────────────────────────
    //
    // The only route that turns a held request into a usable warrant.
    if (path === "/approval/decide" && req.method === "POST") {
      const approval_id = String(body.approval_id ?? "");
      const subject = await warrantForApproval(approval_id);
      if (!subject) return json({ ok: false, error: "unknown_approval" }, 404);

      const events = await store.bySubject(subject);
      const state = fold(events)!;

      if (state.state === "expired") {
        return json({
          ok: false,
          error: "approval_expired",
          message: `this request lapsed at ${state.expires_at} and can no longer be decided`,
        }, 410);
      }
      if (state.state !== "pending_approval") {
        return json({
          ok: false,
          error: "invalid_transition",
          message: `already decided — this warrant is '${state.state}'`,
        }, 409);
      }

      const violations: Violation[] = [];
      if (!isNamedHuman(body.approver)) {
        violations.push({
          field: "approver",
          message: "must name a specific person, not a role or system",
        });
      }
      if (!["approved", "rejected"].includes(String(body.decision))) {
        violations.push({ field: "decision", message: "must be 'approved' or 'rejected'" });
      }
      // Nobody signs off on their own action.
      if (samePerson(body.approver, state.record?.principal)) {
        violations.push({
          field: "approver",
          message: "cannot decide an action you authorised — approval needs a second person",
        });
      }
      // And the decision must come from whoever it was asked of, or the
      // requester simply picks a friendlier approver after the fact.
      if (state.requested_of && !samePerson(body.approver, state.requested_of)) {
        violations.push({
          field: "approver",
          message: `this decision was requested of ${state.requested_of}, so only they can make it`,
        });
      }
      if (violations.length > 0) {
        return json({ ok: false, error: "policy_violation", violations }, 422);
      }

      await store.append({
        subject_id: subject,
        kind: "approval.decided",
        payload: {
          approval_id,
          approver: body.approver,
          decision: body.decision,
          note: body.note ?? null,
        },
      });

      if (String(body.decision) === "rejected") {
        await store.append({
          subject_id: subject,
          kind: "warrant.refused",
          payload: { approver: body.approver, reason: body.note ?? "rejected by approver" },
        });
        // Still no warrant_id. A refused action has nothing to run with.
        return json({ ok: true, state: "refused", decision: "rejected", approver: body.approver });
      }

      // Reissue exactly what was held, so what gets approved is what was
      // requested — the caller cannot amend the record between ask and answer.
      const held = events.find((e) => e.kind === "warrant.requested")!;
      const heldPayload = held.payload as Record<string, unknown>;
      const issued = await store.append({
        subject_id: subject,
        kind: "warrant.issued",
        payload: {
          policy_id: heldPayload.policy_id,
          policy_version: heldPayload.policy_version,
          record: heldPayload.record,
          context: heldPayload.context ?? null,
          approved_by: body.approver,
          approval_id,
        },
      });

      return json({
        ok: true,
        warrant_id: subject,
        state: "issued",
        decision: "approved",
        approver: body.approver,
        event_id: issued.event_id,
        hash: issued.hash,
      });
    }

    // ── GET /approval/:id — what a held caller polls ────────────────────────
    if (path.startsWith("/approval/") && req.method === "GET") {
      const approval_id = decodeURIComponent(path.slice("/approval/".length));
      const subject = await warrantForApproval(approval_id);
      if (!subject) return json({ ok: false, error: "unknown_approval" }, 404);

      const state = fold(await store.bySubject(subject))!;
      const usable = state.state === "issued" || state.state === "committed" ||
        state.state === "aborted";

      return json({
        ok: true,
        approval_id,
        state: state.state,
        requested_of: state.requested_of,
        expires_at: state.expires_at,
        decisions: state.approvals,
        // Present only once a human approved. Until then there is nothing to act with.
        ...(usable ? { warrant_id: subject } : {}),
      });
    }

    // ── GET /warrant/:id ────────────────────────────────────────────────────
    if (path.startsWith("/warrant/") && req.method === "GET") {
      const id = decodeURIComponent(path.slice("/warrant/".length));
      const events = await store.bySubject(id);
      if (events.length === 0) return json({ ok: false, error: "unknown_warrant" }, 404);
      return json({ ok: true, ...fold(events)! });
    }

    // ── GET /events ─────────────────────────────────────────────────────────
    if (path === "/events" && req.method === "GET") {
      const limit = Math.min(Number(url.searchParams.get("limit") ?? 100) || 100, 1000);
      const all = await store.all();
      return json({ ok: true, total: all.length, events: all.slice(-limit) });
    }

    // ── GET /verify ─────────────────────────────────────────────────────────
    if (path === "/verify" && req.method === "GET") {
      const result = await verifyChain(await store.all());
      return json({ ok: result.verified, ...result });
    }

    // ── GET /export — evidence package ──────────────────────────────────────
    if (path === "/export" && req.method === "GET") {
      const all = await store.all();
      const stamp = new Date().toISOString().slice(0, 10);
      return new Response(all.map((e) => JSON.stringify(e)).join("\n") + "\n", {
        headers: {
          "content-type": "application/x-ndjson",
          "content-disposition": `attachment; filename="warrant-evidence-${stamp}.jsonl"`,
        },
      });
    }

    return json({ ok: false, error: "not_found", path }, 404);
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Entry point
// ─────────────────────────────────────────────────────────────────────────────

if (import.meta.main) {
  const token = Deno.env.get("WARRANT_TOKEN");
  if (!token) {
    console.error(
      "WARRANT_TOKEN is not set. Every authenticated route will return 401.\n" +
        "  export WARRANT_TOKEN=$(openssl rand -hex 32)\n",
    );
  }
  const store = Deno.env.get("WARRANT_STORE") === "memory"
    ? new MemoryStore()
    : new JsonlStore(Deno.env.get("WARRANT_LEDGER") ?? "./ledger.jsonl");

  const approvalTtlSeconds = Number(
    Deno.env.get("WARRANT_APPROVAL_TTL") ?? DEFAULT_APPROVAL_TTL_SECONDS,
  );

  const port = Number(Deno.env.get("PORT") ?? 8000);
  Deno.serve({ port }, createHandler({ store, token, approvalTtlSeconds }));
}
