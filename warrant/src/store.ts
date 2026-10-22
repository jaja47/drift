/**
 * Storage.
 *
 * The ledger is an append-only log, so the default store is an append-only
 * file. There is no update path and no delete path — not by convention, but
 * because the interface does not expose one.
 *
 * Reading the tip and appending must not interleave, or two concurrent writers
 * can both build on the same predecessor and fork the chain. Every store
 * serialises writes through a promise chain to prevent that.
 */

import { GENESIS, hashEvent, type LedgerEvent } from "./chain.ts";

export interface Store {
  append(e: { subject_id: string; kind: string; payload: unknown }): Promise<LedgerEvent>;
  all(): Promise<LedgerEvent[]>;
  bySubject(subject_id: string): Promise<LedgerEvent[]>;
}

function newEventId(): string {
  return "evt_" + crypto.randomUUID().replace(/-/g, "").slice(0, 16);
}

/** Serialises writes. Every append waits for the previous one to land. */
abstract class SerialisedStore implements Store {
  #queue: Promise<unknown> = Promise.resolve();

  protected abstract read(): Promise<LedgerEvent[]>;
  protected abstract write(e: LedgerEvent): Promise<void>;

  append(input: { subject_id: string; kind: string; payload: unknown }): Promise<LedgerEvent> {
    const run = this.#queue.then(async () => {
      const existing = await this.read();
      const prev = existing.at(-1);
      const draft = {
        event_id: newEventId(),
        subject_id: input.subject_id,
        kind: input.kind,
        ts: new Date().toISOString(),
        payload: input.payload,
        prev_hash: prev?.hash ?? GENESIS,
      };
      const event: LedgerEvent = {
        seq: (prev?.seq ?? 0) + 1,
        ...draft,
        hash: await hashEvent(draft),
      };
      await this.write(event);
      return event;
    });
    // Keep the queue alive even if this append fails.
    this.#queue = run.catch(() => {});
    return run;
  }

  all(): Promise<LedgerEvent[]> {
    return this.#queue.then(() => this.read());
  }

  async bySubject(subject_id: string): Promise<LedgerEvent[]> {
    return (await this.all()).filter((e) => e.subject_id === subject_id);
  }
}

/** In-memory. Used by the test suite; nothing survives a restart. */
export class MemoryStore extends SerialisedStore {
  #events: LedgerEvent[] = [];
  protected read(): Promise<LedgerEvent[]> {
    return Promise.resolve([...this.#events]);
  }
  protected write(e: LedgerEvent): Promise<void> {
    this.#events.push(e);
    return Promise.resolve();
  }
}

/**
 * Append-only JSONL file. The default.
 *
 * Honest limitation: this is a single-node store. It is correct for one process
 * writing one file, which is what a single service instance does. Horizontal
 * scale needs a store with a real atomic append — see adapters/ for notes.
 */
export class JsonlStore extends SerialisedStore {
  #path: string;
  #cache: LedgerEvent[] | null = null;

  constructor(path = "./ledger.jsonl") {
    super();
    this.#path = path;
  }

  protected async read(): Promise<LedgerEvent[]> {
    if (this.#cache) return this.#cache;
    try {
      const text = await Deno.readTextFile(this.#path);
      this.#cache = text.split("\n").filter(Boolean).map((l) => JSON.parse(l));
    } catch (err) {
      if (!(err instanceof Deno.errors.NotFound)) throw err;
      this.#cache = [];
    }
    return this.#cache!;
  }

  protected async write(e: LedgerEvent): Promise<void> {
    await Deno.writeTextFile(this.#path, JSON.stringify(e) + "\n", { append: true });
    this.#cache?.push(e);
  }
}
