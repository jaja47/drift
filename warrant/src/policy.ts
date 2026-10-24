/**
 * Policy definitions and the generic validator.
 *
 * A policy is DATA, not code. It declares which fields a warrant must carry
 * before an action is allowed to run, and what shape each one has. Adding a new
 * governance rule means adding an object to POLICIES — not editing the server.
 */

// ─────────────────────────────────────────────────────────────────────────────
// Field specs
// ─────────────────────────────────────────────────────────────────────────────

export type FieldSpec =
  | { type: "string"; minLength?: number; maxLength?: number; pattern?: string; hint?: string }
  | { type: "enum"; values: string[] }
  | { type: "boolean" }
  | { type: "array"; itemType: "string"; minItems?: number }
  | { type: "named_human"; hint?: string }
  | { type: "versioned_id"; hint?: string }
  | { type: "prose"; minLength: number; forbidPhrases?: string[]; hint?: string };

export interface Policy {
  id: string;
  version: string;
  title: string;
  /** Why this policy exists, in one sentence. Shown by GET /policies. */
  rationale: string;
  /** Field name → spec. Every listed field is required. */
  fields: Record<string, FieldSpec>;
  /** Extra requirements that only apply when another field holds a given value. */
  conditional?: Array<{
    when: { field: string; equals: unknown };
    require: Record<string, FieldSpec>;
    because: string;
  }>;
  /**
   * Conditions under which no warrant may be issued until a human decides.
   *
   * This is not a field requirement. When a rule matches, the request is held:
   * the caller is given an approval id and NO warrant id, so there is nothing
   * to pass to the next step. See `matchedApprovalRule`.
   *
   * A matching rule also requires the caller to name who the decision is being
   * requested of, in `approval_from`.
   */
  requiresApproval?: ApprovalRule[];
}

export interface ApprovalRule {
  when: { field: string; equals: unknown };
  because: string;
}

/**
 * Who the decision is requested of. Required automatically whenever a
 * requiresApproval rule matches, so policies never declare it by hand.
 *
 * Note the name: this is the person being ASKED. The person who actually
 * decided is recorded separately, from the decision itself, and cannot be
 * supplied by the caller.
 */
export const APPROVAL_FROM: FieldSpec = {
  type: "named_human",
  hint: "must name the specific person the decision is being requested of",
};

/** The first approval rule this request trips, or null if none. */
export function matchedApprovalRule(
  policy: Policy,
  body: Record<string, unknown>,
): ApprovalRule | null {
  for (const rule of policy.requiresApproval ?? []) {
    if (body[rule.when.field] === rule.when.equals) return rule;
  }
  return null;
}

// ─────────────────────────────────────────────────────────────────────────────
// Shared vocabulary
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Phrases that make a stated purpose unfalsifiable. A purpose you could paste
 * onto any record in the table is not a purpose — it is a placeholder, and it
 * defeats the point of recording one. Kept here so policies can share the list.
 */
export const FILLER_PHRASES = [
  "engagement",
  "engage with",
  "outreach",
  "touch base",
  "touchbase",
  "follow up",
  "follow-up",
  "nurture",
  "awareness",
  "top of funnel",
  "top-of-funnel",
  "build relationship",
  "build a relationship",
  "check in",
  "check-in",
  "stay top of mind",
  "warm up",
  "warm-up",
  "connect with",
  "start a conversation",
  "get in front of",
  "drive interest",
  "generate interest",
  "explore synergies",
  "add value",
  "circle back",
];

/** Strings that are a role, a system, or a shrug — never a specific person. */
const NOT_A_PERSON = [
  "system",
  "automated",
  "automation",
  "agent",
  "bot",
  "service",
  "api",
  "n/a",
  "na",
  "tbd",
  "unknown",
  "none",
  "team",
  "the team",
  "ops",
  "marketing",
  "sales",
  "admin",
  "administrator",
  "root",
  "user",
  "operator",
  "everyone",
];

const PERSON_RE = /^([\p{L}][\p{L}'’.-]*\s+[\p{L}][\p{L}'’.-]*.*|[^\s@]+@[^\s@]+\.[^\s@]+)$/u;
const VERSIONED_RE = /^[a-z0-9][a-z0-9._-]{2,63}@v\d+\.\d+\.\d+$/i;

export function isNamedHuman(v: unknown): boolean {
  if (typeof v !== "string") return false;
  const s = v.trim();
  if (s.length < 4) return false;
  if (NOT_A_PERSON.includes(s.toLowerCase())) return false;
  return PERSON_RE.test(s);
}

// ─────────────────────────────────────────────────────────────────────────────
// The validator
// ─────────────────────────────────────────────────────────────────────────────

export interface Violation {
  field: string;
  message: string;
}

function checkField(name: string, spec: FieldSpec, value: unknown): Violation | null {
  const fail = (message: string): Violation => ({ field: name, message });

  if (value === undefined || value === null) {
    return fail(`missing — ${describe(spec)}`);
  }

  switch (spec.type) {
    case "string": {
      if (typeof value !== "string") return fail("must be a string");
      const s = value.trim();
      if (spec.minLength !== undefined && s.length < spec.minLength) {
        return fail(`must be at least ${spec.minLength} characters`);
      }
      if (spec.maxLength !== undefined && s.length > spec.maxLength) {
        return fail(`must be at most ${spec.maxLength} characters`);
      }
      if (spec.pattern && !new RegExp(spec.pattern).test(s)) {
        return fail(spec.hint ?? `must match ${spec.pattern}`);
      }
      return null;
    }

    case "enum": {
      if (!spec.values.includes(String(value))) {
        return fail(`must be one of: ${spec.values.join(" | ")}`);
      }
      return null;
    }

    case "boolean": {
      if (typeof value !== "boolean") return fail("must be an explicit true or false");
      return null;
    }

    case "array": {
      if (!Array.isArray(value)) return fail("must be an array");
      if (spec.minItems !== undefined && value.length < spec.minItems) {
        return fail(`must contain at least ${spec.minItems} item(s)`);
      }
      if (value.some((x) => typeof x !== "string" || !x.trim())) {
        return fail("every entry must be a non-empty string");
      }
      return null;
    }

    case "named_human": {
      if (!isNamedHuman(value)) {
        return fail(
          spec.hint ?? "must name a specific person (full name or email), not a role or system",
        );
      }
      return null;
    }

    case "versioned_id": {
      if (typeof value !== "string" || !VERSIONED_RE.test(value.trim())) {
        return fail(
          spec.hint ?? "must be 'identifier@vX.Y.Z' so the acting version is reconstructable",
        );
      }
      return null;
    }

    case "prose": {
      if (typeof value !== "string") return fail("must be a string");
      const s = value.trim();
      if (s.length < spec.minLength) {
        return fail(`must be a specific sentence of at least ${spec.minLength} characters`);
      }
      const hit = (spec.forbidPhrases ?? []).find((p) => s.toLowerCase().includes(p));
      if (hit) {
        return fail(`contains the filler phrase "${hit}" — state what this action is actually for`);
      }
      return null;
    }
  }
}

function describe(spec: FieldSpec): string {
  switch (spec.type) {
    case "enum":
      return `one of ${spec.values.join(" | ")}`;
    case "named_human":
      return "a named person";
    case "versioned_id":
      return "an identifier@vX.Y.Z";
    case "prose":
      return `a specific sentence (>= ${spec.minLength} chars)`;
    case "array":
      return `an array of ${spec.minItems ?? 0}+ strings`;
    default:
      return spec.type;
  }
}

export function validate(policy: Policy, body: Record<string, unknown>): Violation[] {
  const violations: Violation[] = [];

  for (const [name, spec] of Object.entries(policy.fields)) {
    const v = checkField(name, spec, body[name]);
    if (v) violations.push(v);
  }

  for (const rule of policy.conditional ?? []) {
    if (body[rule.when.field] !== rule.when.equals) continue;
    for (const [name, spec] of Object.entries(rule.require)) {
      const v = checkField(name, spec, body[name]);
      if (v) violations.push({ field: name, message: `${v.message} (${rule.because})` });
    }
  }

  // An action that needs approval must say who is being asked for it.
  const approval = matchedApprovalRule(policy, body);
  if (approval) {
    const v = checkField("approval_from", APPROVAL_FROM, body.approval_from);
    if (v) {
      violations.push({ field: "approval_from", message: `${v.message} (${approval.because})` });
    }
  }

  return violations;
}

/** Fields the warrant should carry into the record, given the policy. */
export function recordFields(policy: Policy, body: Record<string, unknown>) {
  const out: Record<string, unknown> = {};
  for (const name of Object.keys(policy.fields)) out[name] = body[name];
  for (const rule of policy.conditional ?? []) {
    if (body[rule.when.field] !== rule.when.equals) continue;
    for (const name of Object.keys(rule.require)) out[name] = body[name];
  }
  if (matchedApprovalRule(policy, body)) out.approval_from = body.approval_from;
  return out;
}

// ─────────────────────────────────────────────────────────────────────────────
// Example policies. Replace or extend — these ship as working demonstrations,
// not as a recommended ruleset for your organisation.
// ─────────────────────────────────────────────────────────────────────────────

export const CONTACT_DATA_TOUCH: Policy = {
  id: "contact-data-touch",
  version: "1.0.0",
  title: "Automated contact and account data touch",
  rationale: "Any automated action that creates, updates, enriches, scores or messages a person " +
    "or company record must be attributable to a named human and a specific purpose " +
    "before it runs.",
  fields: {
    actor: {
      type: "versioned_id",
      hint:
        "must be 'agent-id@vX.Y.Z' — an audit record that cannot name the acting version is not reconstructable",
    },
    principal: {
      type: "named_human",
      hint: "must name the human who authorised this class of action, not a system or a team",
    },
    action: { type: "enum", values: ["create", "update", "enrich", "score", "message"] },
    object: { type: "string", minLength: 3, hint: "the account or contact id being acted on" },
    purpose: {
      type: "prose",
      minLength: 24,
      forbidPhrases: FILLER_PHRASES,
      hint: "one specific sentence — what is this action for, on this record",
    },
    sources: { type: "array", itemType: "string", minItems: 1 },
    send: { type: "boolean" },
  },
  // Sending is held for a real decision. Previously this only required the
  // caller to type a name into an `approver` field, which recorded a claim of
  // approval rather than obtaining one.
  requiresApproval: [
    { when: { field: "send", equals: true }, because: "send:true" },
  ],
};

export const GENERATED_CONTENT_PUBLISH: Policy = {
  id: "generated-content-publish",
  version: "1.0.0",
  title: "Publication of model-generated content",
  rationale: "Model-generated material must carry its citations and a named reviewer before it " +
    "reaches an audience outside the team that produced it.",
  fields: {
    actor: { type: "versioned_id" },
    model: { type: "string", minLength: 3, hint: "the model identifier that produced the draft" },
    reviewer: {
      type: "named_human",
      hint: "the human who read the draft before it went out",
    },
    audience: { type: "enum", values: ["internal", "customer", "public", "regulator"] },
    purpose: { type: "prose", minLength: 24, forbidPhrases: FILLER_PHRASES },
    citations: {
      type: "array",
      itemType: "string",
      minItems: 1,
      // Every factual claim should trace to something fetched, not recalled.
    },
    publish: { type: "boolean" },
  },
  // Two different mechanisms, deliberately shown side by side.
  //
  // `conditional` requires an extra FIELD: counsel must already have signed off,
  // and the caller records who. That is still only a claim by the caller.
  //
  // `requiresApproval` requires an extra DECISION: no warrant exists until a
  // human actually decides, out of band. Regulator-bound material needs both.
  conditional: [
    {
      when: { field: "audience", equals: "regulator" },
      require: {
        counsel_sign_off: {
          type: "named_human",
          hint: "material going to a regulator needs a named sign-off beyond the reviewer",
        },
      },
      because: "audience:regulator",
    },
  ],
  requiresApproval: [
    { when: { field: "audience", equals: "regulator" }, because: "audience:regulator" },
    { when: { field: "audience", equals: "public" }, because: "audience:public" },
  ],
};

/**
 * The shared list is written for outreach. Record corrections have their own
 * way of saying nothing: a purpose like "routine data cleanup" fits every row
 * in the table, so it explains none of them.
 */
export const HR_FILLER_PHRASES = [
  ...FILLER_PHRASES,
  "data cleanup",
  "data clean-up",
  "clean up",
  "cleanup",
  "clean-up",
  "housekeeping",
  "data hygiene",
  "tidy up",
  "routine maintenance",
  "keep records in sync",
  "keep systems in sync",
];

export const HR_RECORD_CORRECTION: Policy = {
  id: "hr-record-correction",
  version: "1.0.0",
  title: "Correction of an employee record in a downstream HR system",
  rationale: "A correction to employee data must name who authorised it, which records it was " +
    "derived from, and why — and pay, status and identity changes, and adding or removing a " +
    "record, need a second person.",
  fields: {
    actor: { type: "versioned_id" }, // "drift-reconciler@v0.1.0"
    principal: { type: "named_human" }, // who authorised this reconciliation run
    system: { type: "enum", values: ["adp", "workday", "learning"] },
    action: { type: "enum", values: ["update", "create", "deactivate"] },
    object: { type: "string", minLength: 3, hint: "pseudonymous employee id, never a name" },
    field: { type: "string", minLength: 2 },
    change_class: {
      type: "enum",
      values: ["routine", "compensation", "employment_status", "identity"],
    },
    purpose: { type: "prose", minLength: 24, forbidPhrases: HR_FILLER_PHRASES },
    // A reconciliation that cites only one system has not reconciled anything.
    sources: { type: "array", itemType: "string", minItems: 2 },
  },
  requiresApproval: [
    {
      when: { field: "change_class", equals: "compensation" },
      because: "change_class:compensation",
    },
    {
      when: { field: "change_class", equals: "employment_status" },
      because: "change_class:employment_status",
    },
    { when: { field: "change_class", equals: "identity" }, because: "change_class:identity" },
    // A backstop that does not depend on the caller's classification: adding or
    // removing a payroll record is held even if it arrives labelled "routine".
    // Listed last, so a correctly classed request still reports its class.
    { when: { field: "action", equals: "create" }, because: "action:create" },
    { when: { field: "action", equals: "deactivate" }, because: "action:deactivate" },
  ],
};

export const POLICIES: Record<string, Policy> = {
  [CONTACT_DATA_TOUCH.id]: CONTACT_DATA_TOUCH,
  [GENERATED_CONTENT_PUBLISH.id]: GENERATED_CONTENT_PUBLISH,
  [HR_RECORD_CORRECTION.id]: HR_RECORD_CORRECTION,
};
