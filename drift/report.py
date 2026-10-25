"""reports/run_<ts>.md: what one run found and did, written for a person to read.

It has no pay figures, names or emails. A row is described by its employee ref, mismatch type and
field; the values stay in the exports.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from drift import __version__, classify

if TYPE_CHECKING:
    from drift.govern import RunResult, Shown
    from drift.normalize import FormatDifference
    from drift.state import Record

STATUS_ORDER = ("applied", "held", "approved", "rejected", "blocked", "expired", "failed",
                "uncommitted", "warrant_unreachable")
STATUS_WORDS = {
    "applied": "applied", "held": "held for approval", "approved": "approved, awaiting sync",
    "rejected": "rejected", "blocked": "blocked by Warrant", "expired": "expired",
    "failed": "failed", "uncommitted": "written, commit pending",
    "warrant_unreachable": "not requested (Warrant unreachable)",
}


def _cell(value: object) -> str:
    return str(value if value not in (None, "") else "—").replace("|", "\\|").replace("\n", " ")


def _table(header: list[str], rows: list[list[object]]) -> list[str]:
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header),
            *("| " + " | ".join(_cell(c) for c in row) + " |" for row in rows), ""]


def _what(r: Record) -> str:
    return f"{r.type} · {classify.label(r.type)}"


def ignored_line(ignored: list[FormatDifference]) -> str:
    """'4 format differences correctly ignored: 2 email differs only in case or spacing, …'"""
    if not ignored:
        return "No format differences to ignore."
    kinds = Counter(f.why for f in ignored)
    return (f"{len(ignored)} format difference{'s' if len(ignored) != 1 else ''} correctly "
            f"ignored: " + "; ".join(f"{n} × {why}" for why, n in kinds.most_common()) + ".")


def verify_line(verify: dict | None) -> str:
    if verify is None:
        return "not checked — Warrant was unreachable."
    if verify.get("verified"):
        return (f"intact — {verify.get('events')} events, tip "
                f"`{str(verify.get('tip', ''))[:16]}…`.")
    return (f"BROKEN at event {verify.get('broken_at')} of {verify.get('events')}: "
            f"{verify.get('reason')}")


def render(r: RunResult, approvers: Mapping[str, classify.Approver]) -> str:
    pairs = Counter(p.match for p in r.detection.pairs)
    statuses = Counter(rec.status for rec in r.records)
    by_status = {s: [rec for rec in r.records if rec.status == s] for s in STATUS_ORDER}
    roles = {a.name: a.role for a in approvers.values()}

    out = [f"# Drift run · {r.started:%Y-%m-%d %H:%M} UTC", ""]
    out += _table(["", ""], [
        ["Run", f"`{r.run_id}`"], ["Principal", r.principal],
        ["Actor", f"drift-reconciler@v{__version__}"], ["Warrant", r.warrant_url],
        ["Outcome", f"**{r.outcome}**" + (f" — {r.detail}" if r.detail else "")]])
    if r.outcome != "completed":
        out += ["> **Fail closed.** Warrant gave no usable answer, so the run stopped. Nothing was "
                "written after that point, and every unrequested mismatch is marked "
                "`warrant_unreachable`.", ""]

    out += ["## Summary", "",
            f"- **{len(r.detection.pairs)}** people compared: {pairs['both']} matched through the "
            f"ID crosswalk, {pairs['workday_only']} only in Workday, {pairs['adp_only']} only in "
            f"ADP.",
            f"- **{len(r.records)}** mismatches: " + ", ".join(
                f"{statuses[s]} {STATUS_WORDS[s]}" for s in STATUS_ORDER if statuses[s]) + ".",
            f"- {ignored_line(r.detection.ignored)}",
            f"- {len(r.already)} already handled by an earlier run (no new warrant requested).",
            ""]

    out += ["## Patterns", ""]
    out += [f"- {p.diagnosis} Suggested owner: {p.suggested_owner}." for p in r.patterns] or [
        "None — no group of 3 or more mismatches shares a cause."]
    out += [""]

    out += ["## Applied — each under a committed warrant", ""]
    out += _table(["Employee", "Mismatch", "Field", "Warrant", "Columns written"], [
        [rec.employee_ref, _what(rec), rec.field, f"`{rec.warrant_id}`",
         ", ".join((rec.result or {}).get("changed", {})) or "none needed"]
        for rec in by_status["applied"]]) if by_status["applied"] else ["None.", ""]

    waiting = by_status["held"] + by_status["approved"]
    out += ["## Held for approval — nothing written until a named person decides", ""]
    out += _table(["Employee", "Mismatch", "Class", "Waiting on", "Approval id", "Fingerprint"], [
        [rec.employee_ref, _what(rec), rec.change_class,
         f"{rec.requested_of} ({roles.get(rec.requested_of or '', 'approver')})"
         + (" — approved, awaiting sync" if rec.status == "approved" else ""),
         f"`{rec.approval_id}`", f"`{rec.fingerprint[:12]}`"]
        for rec in waiting]) if waiting else ["None.", ""]

    if by_status["blocked"]:
        out += ["## Blocked by Warrant — nothing written", ""]
        out += _table(["Employee", "Mismatch", "Violations"], [
            [rec.employee_ref, _what(rec),
             "; ".join(f"{v.get('field')}: {v.get('message')}" for v in rec.violations)]
            for rec in by_status["blocked"]])

    rest = [rec for s in ("rejected", "expired", "failed", "uncommitted", "warrant_unreachable")
            for rec in by_status[s]]
    if rest:
        out += ["## Everything else", ""]
        out += _table(["Employee", "Mismatch", "Status", "Detail"], [
            [rec.employee_ref, _what(rec), rec.status, rec.detail] for rec in rest])

    out += ["## Ledger", "", f"`GET /verify`: {verify_line(r.verify)}", ""]
    return "\n".join(out)


def write(r: RunResult, directory: Path, approvers: Mapping[str, classify.Approver]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{r.run_id}.md"
    path.write_text(render(r, approvers), encoding="utf-8")
    return path


def changes(result: dict | None) -> list[str]:
    """What a committed result wrote, one line per column."""
    changed = (result or {}).get("changed") or {}
    if not changed:
        return [(result or {}).get("note", "nothing written")]
    return [f"{column}: {v['from'] or '—'} → {v['to']}" if isinstance(v, dict)
            else f"{column}: {v}" for column, v in changed.items()]


def shown_facts(shown: Shown,
                approvers: Mapping[str, classify.Approver]) -> list[tuple[str, str]]:
    """One correction as Warrant holds it, as (label, value) pairs: what was authorised, by whom,
    why, from which sources, and what was written. A held correction has no warrant yet, so it
    shows its approval instead. The CLI and the page both print these."""
    rec, w, a = shown.record, shown.warrant, shown.approval
    roles = {x.name: x.role for x in approvers.values()}
    if w is None:
        if a is None:
            return [("Status", rec.detail or "No warrant was issued.")] if rec else []
        who = a.get("requested_of") or ""
        facts = [("Requested of", f"{who} ({roles.get(who, 'approver')})"),
                 ("Approval", f"{a.get('approval_id')} — {a.get('state')}, lapses "
                              f"{a.get('expires_at')}")]
        facts += [("Decision", f"{d.get('decision')} by {d.get('approver')}")
                  for d in a.get("decisions") or []]
        if a.get("state") == "pending_approval":
            facts.append(("", f"No warrant exists until {who} decides — there is nothing to act "
                              f"with, and nothing has been written."))
        return facts
    record = w.get("record") or {}
    approved = [x for x in w.get("approvals") or [] if x.get("decision") == "approved"]
    facts = [("Warrant", f"{w.get('warrant_id')} — {w.get('state')}"),
             ("Policy", str(w.get("policy_id"))),
             ("Actor", str(record.get("actor"))),
             ("Principal", str(record.get("principal"))),
             ("Change", f"{record.get('action')} {record.get('system')} · {record.get('object')}"
                        f" · {record.get('field')} · {record.get('change_class')}"),
             ("Purpose", str(record.get("purpose")))]
    facts += [("Sources" if i == 0 else "", src)
              for i, src in enumerate(record.get("sources") or [])]
    facts.append(("Approval", f"approved by {approved[-1].get('approver')}, the person it was "
                              f"requested of" if approved
                  else "none needed — routine" if not record.get("approval_from")
                  else f"requested of {record.get('approval_from')}"))
    if w.get("state") == "committed":
        outcome = w.get("outcome") or {}
        written = changes(outcome)
        facts.append(("Written", f"{outcome.get('target')} {outcome.get('row')}: {written[0]}"))
        facts += [("", line) for line in written[1:]]
    elif w.get("state") == "aborted":
        facts.append(("Aborted", str(w.get("outcome"))))
    facts.append(("Ledger", f"{w.get('event_count')} events, {w.get('created_at')} → "
                            f"{w.get('last_event_at')}"))
    return facts


def shown_lines(shown: Shown, approvers: Mapping[str, classify.Approver]) -> list[str]:
    """`shown_facts` for a terminal, headed by the mismatch."""
    rec = shown.record
    lines = ([f"{rec.employee_ref} · {rec.type} {classify.label(rec.type)} — {rec.status}"]
             if rec else [])
    for label, value in shown_facts(shown, approvers):
        if label or shown.warrant:  # an unlabelled line continues the one above it
            lines.append(f"  {label:<13} {value}")
        else:  # a held correction's closing note
            lines.append(f"  {value}")
    return lines
