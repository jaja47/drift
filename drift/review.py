"""What the review page shows and does, assembled here so that app.py only lays it out.

`snapshot` is read-only. Statuses come from Drift's state (govern), explanations from
explain.py, the audit result from Warrant. Nothing here decides anything.

The page's three actions — run, decide, check for decisions — call the same governance
functions as the CLI (`govern.run`, `govern.approve`, `govern.sync`) and return an `Outcome`
for the page to show. Deciding on the page approves and then syncs, so an approval made there is
applied straight away: the demo's "approve it as that person; it applies".
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from drift import classify, govern
from drift.connectors.csv_source import DEFAULT_DIR
from drift.detect import Mismatch
from drift.explain import Explanation, explain
from drift.govern import Settings, WarrantClient, WarrantUnreachable
from drift.normalize import FormatDifference
from drift.patterns import Pattern, find_patterns
from drift.report import changes
from drift.state import State

STATUS_WORDS = {
    "applied": "Fixed",
    "held": "Waiting for approval",
    "approved": "Approved — being applied",
    "rejected": "Rejected — left as it is",
    "blocked": "Blocked by policy",
    "expired": "Request lapsed — will be asked again",
    "failed": "Could not be applied",
    "uncommitted": "Written — audit record pending",
    "warrant_unreachable": "Not processed — audit service unreachable",
}
CLASS_WORDS = {"routine": "Routine", "compensation": "Pay",
               "employment_status": "Employment status", "identity": "Identity"}
# What needs a person first, what went wrong next, what is finished last.
ORDER = ("held", "approved", "failed", "blocked", "uncommitted", "expired",
         "warrant_unreachable", "applied", "rejected")
FIELD_NAMES = {"worker_status": "Employment status", "location": "Location",
               "work_email": "Work email", "rate_type": "Rate type"}


# ── settings for the page ────────────────────────────────────────────────────────────────────


def settings_from_env() -> Settings:
    """The page has no command line, so it reads its settings from the environment
    (scripts/dev.sh provides DRIFT_PRINCIPAL; the directories default as for the CLI)."""
    principal = " ".join(os.environ.get("DRIFT_PRINCIPAL", "").split())
    if not principal:
        raise classify.ConfigError("DRIFT_PRINCIPAL is not set — the page needs the named "
                                   "person who authorises a run. Start it with `make app`.")
    env = os.environ.get
    return Settings(principal=principal,
                    source_dir=Path(env("DRIFT_SOURCE_DIR") or DEFAULT_DIR),
                    state_dir=Path(env("DRIFT_STATE_DIR") or govern.STATE_DIR),
                    reports_dir=Path(env("DRIFT_REPORTS_DIR") or govern.REPORTS_DIR))


# ── plain words ──────────────────────────────────────────────────────────────────────────────


def when(iso: str | None) -> str:
    """'2026-10-25T22:06:01+00:00' → '2026-10-25 22:06 UTC'."""
    return datetime.fromisoformat(iso).astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC") \
        if iso else ""


def money(value: str | None) -> str:
    """'154000.00/yr' → '$154,000'; cents only when there are some."""
    if not value:
        return "nothing"
    amount = Decimal(value.removesuffix("/yr"))
    return f"${amount:,.2f}" if amount % 1 else f"${amount:,.0f}"


def what(m: Mismatch) -> str:
    """What differs, in words an HR reader uses."""
    wd, adp = m.workday_value or "nothing", m.adp_value or "nothing"
    if m.type == "M1":
        return f"Workday: {wd}. ADP: still {adp}."
    if m.type == "M2":
        return f"Annual pay: {money(m.workday_value)} in Workday, {money(m.adp_value)} in ADP."
    if m.type == "M3":
        return f"Cost centre: {wd} in Workday, department {adp} in ADP."
    if m.type == "M4":
        return f"Manager: {wd} in Workday, {adp} in ADP."
    if m.type == "M5":
        return f"Workday: {wd}. ADP: no payroll record."
    if m.type == "M6":
        return f"ADP: {adp} payroll record. Workday: no such worker."
    if m.type == "M7":
        return f"Legal name: {wd} in Workday, {adp} in ADP."
    field_name = FIELD_NAMES.get(m.field, m.field.replace("_", " ").capitalize())
    return f"{field_name}: {wd} in Workday, {adp} in ADP."


# ── the snapshot ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Row:
    fingerprint: str
    employee_ref: str
    label: str  # the mismatch type, in words
    what: str
    change_class: str
    class_words: str
    status: str
    status_words: str
    approver: str  # who decides, decided, or why nobody needs to
    explanation: Explanation
    sources: tuple[str, ...]
    can_decide: bool
    detail: str | None


@dataclass(frozen=True)
class Review:
    principal: str
    has_run: bool
    last_run: dict | None = None  # started_at, outcome, detail, report
    last_run_at: str = ""  # started_at, for people: '2026-10-25 22:06 UTC'
    compared: int = 0
    mismatches: int = 0
    fixed_automatically: int = 0
    fixed_after_approval: int = 0
    waiting: int = 0
    ignored: list[FormatDifference] = field(default_factory=list)
    patterns: list[Pattern] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)
    verify: dict | None = None
    warrant_error: str | None = None
    explanations_note: str = ""


def _approver_words(change_class: str, status: str, requested_of: str | None,
                    decided_by: str | None,
                    approvers: dict[str, classify.Approver]) -> str:
    if decided_by:
        return f"{'Rejected' if status == 'rejected' else 'Approved'} by {decided_by}"
    a = classify.approver(change_class, approvers)
    if a is None:
        return "Not needed — routine"
    if requested_of and requested_of != a.name:
        return requested_of
    return str(a)


def snapshot(settings: Settings, client: WarrantClient) -> Review:
    try:
        verify, warrant_error = client.verify(), None
    except WarrantUnreachable as e:
        verify, warrant_error = None, str(e)
    if not settings.db.exists():
        return Review(settings.principal, False, verify=verify, warrant_error=warrant_error)
    with State(settings.db) as state:
        runs = state.runs()
        records = {r.fingerprint: r for r in state.all()}
    if not runs:
        return Review(settings.principal, False, verify=verify, warrant_error=warrant_error)

    approvers = dict(classify.load_approvers())
    inputs = govern.read_inputs(settings.source_dir)
    detection = inputs.detection
    explained = explain(detection.mismatches,
                        cache_path=settings.state_dir / "explanations.json")
    rows = []
    for m in detection.mismatches:
        rec = records.get(m.fingerprint)
        change_class = classify.change_class(m.type, m.field)
        status = rec.status if rec else "warrant_unreachable"
        rows.append(Row(
            fingerprint=m.fingerprint, employee_ref=m.employee_ref, label=classify.label(m.type),
            what=what(m), change_class=change_class, class_words=CLASS_WORDS[change_class],
            status=status, status_words=STATUS_WORDS[status],
            approver=_approver_words(change_class, status, rec.requested_of if rec else None,
                                     rec.decided_by if rec else None, approvers),
            explanation=explained.by_fingerprint[m.fingerprint],
            sources=tuple(govern.sources(m)), can_decide=status == "held",
            detail=rec.detail if rec else "not processed yet — run again"))
    rows.sort(key=lambda r: ORDER.index(r.status))
    statuses = Counter(r.status for r in rows)
    after_approval = sum(1 for r in rows if r.status == "applied"
                         and records[r.fingerprint].decided_by)
    return Review(
        principal=settings.principal, has_run=True, last_run=runs[-1],
        last_run_at=when(runs[-1]["started_at"]),
        compared=len(detection.pairs), mismatches=len(rows),
        fixed_automatically=statuses["applied"] - after_approval,
        fixed_after_approval=after_approval, waiting=statuses["held"] + statuses["approved"],
        ignored=detection.ignored,
        patterns=find_patterns(detection.mismatches, detection.pairs), rows=rows, verify=verify,
        warrant_error=warrant_error, explanations_note=explained.note)


# ── the page's actions ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Outcome:
    ok: bool
    message: str
    details: tuple[str, ...] = ()


def start_run(settings: Settings, client: WarrantClient) -> Outcome:
    try:
        r = govern.run(settings, client)
    except classify.ConfigError as e:
        return Outcome(False, f"Nothing was requested or changed: {e}")
    statuses = Counter(rec.status for rec in r.records)
    if r.outcome != "completed":
        return Outcome(False, "The audit service (Warrant) gave no usable answer, so the run "
                              "stopped. Nothing more was changed.", (r.detail or "",))
    return Outcome(True, f"Compared {len(r.detection.pairs)} people: {len(r.records)} "
                         f"mismatches — {statuses['applied']} fixed, "
                         f"{statuses['held'] + statuses['approved']} waiting for approval.")


def check_decisions(settings: Settings, client: WarrantClient) -> Outcome:
    s = govern.sync(settings, client)
    if s.outcome != "completed":
        return Outcome(False, "The audit service (Warrant) is unreachable; nothing was "
                              "changed.", (s.detail or "",))
    if not s.changed:
        return Outcome(True, f"No new decisions. {len(s.waiting)} still waiting.")
    moved = Counter(STATUS_WORDS[r.status] for r in s.changed)
    return Outcome(True, "; ".join(f"{n} {words.lower()}" for words, n in moved.items())
                   + f". {len(s.waiting)} still waiting.",
                   tuple(f"{r.employee_ref}: {r.detail}" for r in s.changed))


def decide(settings: Settings, client: WarrantClient, fingerprint: str, approver: str,
           decision: str, note: str | None = None) -> Outcome:
    """A named person decides a held correction. Approved → applied straight away (approve,
    then sync); rejected → never applied. Warrant refuses anyone but the person asked."""
    if not approver.strip():
        return Outcome(False, "Type your full name to decide.")
    try:
        d = govern.approve(settings, client, fingerprint, approver.strip(), decision,
                           note or None)
    except WarrantUnreachable as e:
        return Outcome(False, "The audit service (Warrant) is unreachable; no decision was "
                              "recorded.", (str(e),))
    if not d.ok:
        return Outcome(False, d.message, tuple(v.get("message", "") for v in d.violations))
    if decision == "rejected":
        return Outcome(True, f"Rejected by {approver.strip()} — nothing will be changed.")
    s = govern.sync(settings, client)
    done = next((r for r in s.changed if r.fingerprint == fingerprint), None)
    if done is None or done.status != "applied":
        return Outcome(False, f"Approved by {approver.strip()}, but not applied yet "
                              f"({s.detail or (done.detail if done else 'still pending')}). "
                              f"Use “Check for decisions” to try again.")
    return Outcome(True, f"Approved by {approver.strip()} — applied.", tuple(changes(done.result)))
