"""The three-minute demo (PLAN.md §1), narrated in the style of Warrant's own walkthrough.

    make demo      fresh data, a fresh Warrant (in-memory, random token), this narration, then
                   the review page on the same run until Ctrl-C

It holds no logic of its own. Every beat calls the same functions as the CLI and the page —
govern.run / show / approve / sync, explain — and only prints what they return. The dimmed
lines say what to notice. It runs inside one Warrant lifetime, so it needs fresh state of its
own (`make demo` uses state/demo/, never the state `make run` and `make app` keep).
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from collections.abc import Callable

from drift import classify, govern
from drift.constants import PAYROLL_CUTOFF
from drift.explain import explain
from drift.govern import Settings, WarrantClient
from drift.report import STATUS_WORDS, ignored_line, shown_lines, verify_line

WIDTH = 96
BEATS = (
    "Drift compared Workday and ADP",
    "Routine fixes went through on their own — each one has a warrant",
    "A terminated employee still active in payroll — Drift did not touch it",
    "The pattern: fix the pipe, not the rows",
    "And the record can be checked",
)


def _style(code: str) -> Callable[[str], str]:
    on = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    return (lambda s: f"\x1b[{code}m{s}\x1b[0m") if on else (lambda s: s)


bold, dim, green, red = _style("1"), _style("2"), _style("32"), _style("31")


def beat(n: int) -> None:
    head = f"{n} · {BEATS[n - 1]} "
    print(f"\n{bold(head)}{dim('─' * max(0, WIDTH - len(head)))}\n")


def say(text: str = "") -> None:
    print(f"  {text}" if text else "")


def note(text: str) -> None:
    """What the viewer should notice."""
    say(dim(text))


def indent(lines: list[str]) -> None:
    for line in lines:
        print(f"  {line}")


def adp_status(row: dict[str, str] | None) -> str:
    if row is None:
        return "no ADP row"
    return (f"Position_Status {row['Position_Status'] or 'blank'}, Termination_Date "
            f"{row['Termination_Date'] or 'blank'}")


def demo(settings: Settings, client: WarrantClient,
         pause: Callable[[str], None] | None = None) -> int:
    """The five beats. `pause(next_title)` is called before beats 2–5 (for a live run)."""
    if settings.db.exists():
        print(f"drift: {settings.db} already exists. The demo needs fresh state, because it runs "
              f"inside one Warrant lifetime — use `make demo`, which clears its own state first.")
        return 2
    approvers = classify.load_approvers()
    between = pause or (lambda title: None)

    print(bold("\nDrift — demo"))
    print(dim("Finds where Workday and ADP disagree, explains why, and fixes only what it is "
              "allowed to."))
    print(dim("Synthetic data. No real Workday or ADP system is connected."))
    print(f"Authorised by {bold(settings.principal)} · Warrant at {client.url}")

    # 1 ─────────────────────────────────────────────────────────────────────────────────────
    beat(1)
    r = govern.run(settings, client)
    if r.outcome != "completed":
        say(red(f"FAIL CLOSED — {r.detail}. Nothing more was written."))
        return 1
    pairs = Counter(p.match for p in r.detection.pairs)
    say(f"{len(r.detection.pairs)} people across Workday and ADP: {pairs['both']} matched through "
        f"the ID crosswalk, {pairs['workday_only']} only in Workday, {pairs['adp_only']} only in "
        f"ADP.")
    say(bold(f"{len(r.records)} real mismatches."))
    say(ignored_line(r.detection.ignored))
    for f in r.detection.ignored:
        say(f"   {f.employee_ref}  {f.why}")
    note("A plain comparison would have flagged those four as well. Drift normalises formats "
         "first, so only real")
    note("disagreements reach a person.")
    statuses = Counter(rec.status for rec in r.records)
    say()
    say(f"What governance did with the {len(r.records)}: " + ", ".join(
        f"{n} {STATUS_WORDS[s]}" for s, n in statuses.most_common()) + ".")

    # 2 ─────────────────────────────────────────────────────────────────────────────────────
    between(BEATS[1])
    beat(2)
    applied = [rec for rec in r.records if rec.status == "applied"]
    say(f"{len(applied)} routine fixes (cost centre, manager) were applied automatically, each "
        f"under its own warrant:")
    for rec in applied:
        say(f"   {rec.employee_ref:<8} {classify.label(rec.type):<20} {green(rec.warrant_id or '')}")
    say()
    say(f"Opening one — {applied[0].employee_ref}:")
    indent(shown_lines(govern.show(settings, client, applied[0].warrant_id or ""), approvers))
    say()
    note("The warrant was issued before the write. It names who authorised the run and cites both "
         "source rows.")
    note("No pay figure, name or email is in it: the ledger can be exported.")

    # 3 ─────────────────────────────────────────────────────────────────────────────────────
    between(BEATS[2])
    beat(3)
    # The clearest case: terminated well before the payroll cutoff, so not a timing question.
    mismatches = {m.fingerprint: m for m in r.detection.mismatches}
    left = {p.employee_ref: p.wd_termination_date for p in r.detection.pairs}
    held_m1 = sorted((x for x in r.records if x.type == "M1" and x.status == "held"),
                     key=lambda x: left[x.employee_ref] or PAYROLL_CUTOFF)
    rec = next((x for x in held_m1 if (left[x.employee_ref] or PAYROLL_CUTOFF) <= PAYROLL_CUTOFF),
               held_m1[0])
    m = mismatches[rec.fingerprint]
    target = govern.target_for(settings, govern.read_inputs(settings.source_dir))
    e = explain([m], cache_path=settings.state_dir / "explanations.json").by_fingerprint[
        m.fingerprint]
    say(f"{rec.employee_ref}: {e.explanation}")
    say(f"Likely: {'; '.join(e.likely_causes)}.")
    note(f"(The explanation is advisory — {e.source}. It cannot change what happens.)")
    say()
    indent(shown_lines(govern.show(settings, client, rec.employee_ref), approvers))
    say(f"ADP right now: {adp_status(target.row(m))} — untouched.")
    note("Warrant answered 202 and gave no warrant id, so Drift has nothing to write with.")

    say()
    say(f"{settings.principal}, who authorised the run, tries to approve it:")
    refused = govern.approve(settings, client, rec.employee_ref, settings.principal, "approved")
    for v in refused.violations:
        say(red(f"   refused — {v.get('message')}"))
    if refused.ok:
        say(red("   UNEXPECTED: the principal's approval was accepted."))
        return 1

    approver = classify.approver(rec.change_class, approvers)
    assert approver is not None
    say()
    say(f"{approver} approves it:")
    decided = govern.approve(settings, client, rec.employee_ref, approver.name, "approved",
                             note="Confirmed with HR: termination is final.")
    synced = govern.sync(settings, client)
    after = next((x for x in synced.changed if x.fingerprint == rec.fingerprint), None)
    if not decided.ok or after is None or after.status != "applied":
        say(red(f"   UNEXPECTED: the approved correction was not applied ({synced.detail})."))
        return 1
    say(green("   approved → applied:"))
    indent(shown_lines(govern.show(settings, client, rec.employee_ref), approvers))
    say(f"ADP now: {adp_status(target.row(m))}.")
    others = [x for x in r.records if x.status == "held" and x.fingerprint != rec.fingerprint]
    note(f"What was approved is exactly what was requested. The other {len(others)} held "
         f"corrections are still waiting;")
    note("nothing was written for them.")

    # 4 ─────────────────────────────────────────────────────────────────────────────────────
    between(BEATS[3])
    beat(4)
    if not r.patterns:
        say("No pattern found.")
    for p in r.patterns:
        say(bold(p.diagnosis))
        say(f"Suggested owner: {p.suggested_owner}.")
        say()
        note(f"That's one integration ticket for IT, not {p.size} manual fixes.")

    # 5 ─────────────────────────────────────────────────────────────────────────────────────
    between(BEATS[4])
    beat(5)
    verify = client.verify()
    line = f"GET /verify: {verify_line(verify)}"
    say(green(line) if verify.get("verified") else red(line))
    note("Each warrant issued or held, each decision and each committed result above is an event "
         "in that hash-")
    note("chained ledger. A refused request leaves no event: it never became an action.")
    say(f"Run report: {r.report}")
    return 0 if verify.get("verified") else 1
