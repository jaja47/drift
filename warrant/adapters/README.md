# Adapters

Three ways the interlock lands, in increasing order of how little control you have over the thing
being governed.

---

## 1. Code you own — `governed()`

The strongest form, because the body is genuinely unreachable.

```ts
async function governed<T>(
  warrantRequest: Record<string, unknown>,
  body: (warrantId: string) => Promise<T>,
): Promise<T> {
  const res = await fetch(`${LEDGER}/warrant`, {
    method: "POST",
    headers: { authorization: `Bearer ${TOKEN}`, "content-type": "application/json" },
    body: JSON.stringify(warrantRequest),
  });

  if (!res.ok) {
    const { violations } = await res.json();
    throw new PolicyError(violations); // ← the body below never runs
  }

  const issued = await res.json();

  // 202 means a human has to decide first. There is no warrant_id to be had,
  // so there is nothing to run with — treat it exactly like a refusal.
  if (res.status === 202) {
    throw new PendingApproval(issued.approval_id, issued.requested_of);
  }

  const { warrant_id } = issued;
  try {
    const result = await body(warrant_id);
    await post(`/warrant/commit`, { warrant_id, result });
    return result;
  } catch (err) {
    await post(`/warrant/abort`, { warrant_id, reason: String(err) });
    throw err;
  }
}
```

`PendingApproval` is not an error condition to smooth over — it is the interlock working. Catch it
where you can surface it to a person, or let the run fail and retry after the decision. What you
must not do is treat it as a soft warning and continue: there is no `warrant_id`, so anything below
would run ungoverned.

Used as:

```ts
const company = await governed({
  policy_id: "contact-data-touch",
  actor: "enrichment-runner@v1.0.0",
  principal: "Dana Okafor",
  action: "enrich",
  object: accountId,
  purpose: `Confirm ${name} has a board risk committee before proposing a briefing`,
  sources: [domain],
  send: false,
}, async () => provider.enrich(domain));
```

Note the purpose is **built per record**. A hardcoded purpose string is generic by construction,
which defeats the check — compute it from the row so it names the thing it is about.

---

## 2. A workflow engine — a step that throws

n8n, Temporal, Airflow, Clay Workflows, Step Functions. Anything with a code step and a failure
path.

```
TRIGGER
   │
CODE     POST /warrant
         if (!warrant_id) throw     ← nothing below this line executes
   │
ACTION   the enrichment / scoring / drafting step
   │
CODE     POST /warrant/commit   (abort on the failure branch)
```

This is nearly as strong as `governed()` — the interlock is control flow rather than configuration —
and it has one advantage: the graph is a versioned artifact, which matters because `actor` is
_identifier + version_ and an audit record that cannot name the version that acted is not
reconstructable.

Put the commit on the success path and the abort on the failure path. A warrant left in `issued`
forever is a dangling record; the log should say what happened.

---

## 3. A no-code data tool — the column interlock

The interesting case: a hosted tool whose source you cannot modify, running enrichment against your
records. Clay, Airtable automations, Retool, Zapier, Make — anything with an HTTP call and a
per-step run condition.

**The pattern:**

1. The **first** enrichment step is an HTTP call to `POST /warrant`. Extract `warrant_id` from the
   response into a column.
2. **Every subsequent step** carries a run condition: `warrant_id is not empty`.
3. The last step calls `/warrant/commit` with the result.
4. There is **no send step anywhere in the table.** Not disabled — absent.

```
col 1   Warrant ID       HTTP POST /warrant          (no run condition — this is the gate)
col 2   Company Data     enrichment provider         run if: Warrant ID is not empty
col 3   Classification   LLM step, must cite         run if: Warrant ID is not empty
col 4   Tier             formula, deterministic      run if: Warrant ID is not empty
col 5   Commit           HTTP POST /warrant/commit   run if: Warrant ID is not empty
```

**Request body**, using whatever column-reference syntax the tool uses:

```json
{
  "policy_id": "contact-data-touch",
  "actor": "table-enrichment@v1.0.0",
  "principal": "Dana Okafor",
  "action": "enrich",
  "object": "<Account ID column>",
  "purpose": "<Purpose column>",
  "sources": ["<Domain column>", "table:accounts"],
  "send": false
}
```

Note `"send": false`. If you set it to `true`, this policy holds the request: the response carries
an `approval_id` and **no `warrant_id`**, so the `Warrant ID` column stays empty and every
downstream step skips — which is the correct behaviour, but it means a table cannot send on its own.
That is deliberate. A table that sends is exactly the thing the gate exists to stop; route sends
through a workflow that can wait on `GET /approval/:id`.

Make **Purpose** a formula column rather than a literal, so it names the record:

```
"Confirm whether " + {{Company}} + " has a board risk committee, before proposing "
+ "an executive briefing under " + {{Primary Regulator}}
```

### Verify the interlock, don't assume it

Point the Purpose formula at `"Increase engagement with this account"`, run one row, and watch:

- the warrant call returns **422** with the violation named,
- `Warrant ID` stays empty,
- **every downstream step skips** — no provider credits spent, no record touched.

Screenshot that row. It is more convincing than any number of rows that succeeded: anyone can show a
table that enriched, only a governed one can show the refusal and name the reason.

### Honest limits of this shape

The interlock here is fail-closed **by configuration** — a run condition someone with table access
can switch off. That is weaker than a `throw`, and you should say so rather than implying otherwise.
Two things that help:

- Reconcile periodically. Every enriched record should have a committed warrant; `GET /export`
  against your own row set will find the ones that don't.
- Keep the credential in a workspace-level secret, not typed into the step, so the token is not
  visible to everyone who can open the table.

What the configuration cannot do is _forge_ a `warrant_id`. Turning the condition off is visible in
the reconciliation; inventing a valid warrant is not possible without the token.
