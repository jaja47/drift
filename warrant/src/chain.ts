/**
 * Hash chain.
 *
 * `prev_hash` is INSIDE the hashed content. That matters: it means an event's
 * hash is fixed the moment it is written and can never be changed by anything
 * appended afterwards.
 *
 * The naive alternative — storing a mutable row and recomputing its hash when
 * the row is updated — breaks under concurrency. Two overlapping actions freeze
 * each other's intermediate hashes, and verification then reports tampering
 * nobody committed. A tamper-evident log that cries wolf is worse than no log,
 * because it discredits itself exactly when you need it.
 */

export interface LedgerEvent {
  seq: number;
  event_id: string;
  subject_id: string;
  kind: string;
  ts: string;
  payload: unknown;
  prev_hash: string;
  hash: string;
}

export const GENESIS = "0".repeat(64);

/** Deterministic JSON: object keys sorted, so the same content always hashes alike. */
export function canonical(v: unknown): string {
  if (v === null || typeof v !== "object") return JSON.stringify(v) ?? "null";
  if (Array.isArray(v)) return "[" + v.map(canonical).join(",") + "]";
  const o = v as Record<string, unknown>;
  return "{" + Object.keys(o).sort()
    .map((k) => JSON.stringify(k) + ":" + canonical(o[k]))
    .join(",") +
    "}";
}

export async function sha256(s: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/** The hashed content of an event. `seq` is deliberately excluded — it is a
 *  storage detail, and including it would couple the chain to insert order. */
export function hashableContent(e: Omit<LedgerEvent, "seq" | "hash">) {
  return {
    event_id: e.event_id,
    subject_id: e.subject_id,
    kind: e.kind,
    ts: e.ts,
    payload: e.payload,
    prev_hash: e.prev_hash,
  };
}

export function hashEvent(e: Omit<LedgerEvent, "seq" | "hash">): Promise<string> {
  return sha256(canonical(hashableContent(e)));
}

export type VerifyResult =
  | { verified: true; events: number; tip: string }
  | { verified: false; events: number; broken_at: number; reason: string };

/** Walk the whole log from genesis, recomputing every hash. */
export async function verifyChain(events: LedgerEvent[]): Promise<VerifyResult> {
  let prev = GENESIS;
  for (const e of events) {
    if (e.prev_hash !== prev) {
      return {
        verified: false,
        events: events.length,
        broken_at: e.seq,
        reason: `prev_hash does not follow the previous event (expected ${prev.slice(0, 12)}…)`,
      };
    }
    const recomputed = await hashEvent(e);
    if (recomputed !== e.hash) {
      return {
        verified: false,
        events: events.length,
        broken_at: e.seq,
        reason:
          "content hash does not match the stored hash — this event was altered after it was written",
      };
    }
    prev = e.hash;
  }
  return { verified: true, events: events.length, tip: prev };
}
