"""The three-minute demo (PLAN.md §1), narrated. Rough version for GATE B; Step 11 polishes it.

It holds no logic of its own. Every step calls the same functions as the CLI and the page —
govern.run / show / approve / sync, explain — and only prints what they return. It runs inside
one Warrant lifetime (`make demo`, in-memory store), so it needs fresh state.
"""

from __future__ import annotations

from collections import Counter

from drift import classify, govern
from drift.constants import PAYROLL_CUTOFF
from drift.explain import explain
from drift.govern import Settings, WarrantClient
from drift.report import STATUS_WORDS, ignored_line, shown_lines, verify_line

WIDTH = 96


def beat(n: int, title: str) -> None:
    head = f"── {n} · {title} "
    print(f"\n{head}{'─' * max(0, WIDTH - len(head))}\n")


def say(text: str = "") -> None:
    print(f"  {text}" if text else "")


def indent(lines: list[str]) -> None:
    for line in lines:
        print(f"  {line}")


def adp_status(row: dict[str, str] | None) -> str:
    if row is None:
        return "no ADP row"
    return (f"Position_Status {row['Position_Status'] or 'blank'}, Termination_Date "
            f"{row['Termination_Date'] or 'blank'}")


def demo(settings: Settings, client: WarrantClient) -> int:
    if settings.db.exists():
        print(f"drift: {settings.db} already exists. The demo needs fresh state, because it runs "
              f"inside one Warrant lifetime — use `make demo`, which resets state first.")
        return 2
    approvers = classify.load_approvers()

    print("Drift — finds where Workday and ADP disagree, explains why, and fixes only what it is "
          "allowed to.\nSynthetic data. No real Workday or ADP system is connected.")
    print(f"Principal (who authorised this run): {settings.principal} · Warrant: {client.url}")

    # 1 ─────────────────────────────────────────────────────────────────────────────────────
    beat(1, "Compare")
    r = govern.run(settings, client)
    if r.outcome != "completed":
        say(f"FAIL CLOSED — {r.detail}. Nothing more was written.")
        return 1
    pairs = Counter(p.match for p in r.detection.pairs)
    say(f"{len(r.detection.pairs)} people across Workday and ADP: {pairs['both']} matched through "
        f"the ID crosswalk, {pairs['workday_only']} only in Workday, {pairs['adp_only']} only in "
        f"ADP.")
    say(f"{len(r.records)} real mismatches.")
    say(ignored_line(r.detection.ignored))
    for f in r.detection.ignored:
        say(f"   {f.employee_ref}  {f.why}")
    statuses = Counter(rec.status for rec in r.records)
    say()
    say("What governance did with the 18: " + ", ".join(
        f"{n} {STATUS_WORDS[s]}" for s, n in statuses.most_common()) + ".")

    # 2 ─────────────────────────────────────────────────────────────────────────────────────
    beat(2, "Routine fixes went through on their own — each one has a warrant")
    applied = [rec for rec in r.records if rec.status == "applied"]
    say(f"{len(applied)} routine fixes (cost centre, manager) were applied automatically, each "
        f"under its own warrant:")
    for rec in applied:
        say(f"   {rec.employee_ref:<8} {classify.label(rec.type):<20} {rec.warrant_id}")
    say()
    say(f"Opening one — {applied[0].employee_ref}:")
    indent(shown_lines(govern.show(settings, client, applied[0].warrant_id or ""), approvers))

    # 3 ─────────────────────────────────────────────────────────────────────────────────────
    beat(3, "A terminated employee still active in payroll — Drift did not touch it")
    # The clearest case: terminated well before the payroll cutoff, so not a timing question.
    mismatches = {m.fingerprint: m for m in r.detection.mismatches}
    left = {p.employee_ref: p.wd_termination_date for p in r.detection.pairs}
    held_m1 = sorted((x for x in r.records if x.type == "M1" and x.status == "held"),
                     key=lambda x: left[x.employee_ref] or PAYROLL_CUTOFF)
    rec = next((x for x in held_m1 if (left[x.employee_ref] or PAYROLL_CUTOFF) <= PAYROLL_CUTOFF),
               held_m1[0])
    m = mismatches[rec.fingerprint]
    target = govern.target_for(settings, govern.read_inputs(settings.source_dir))
    e = explain([m]).by_fingerprint[m.fingerprint]
    say(f"{rec.employee_ref}: {e.explanation}")
    say(f"Likely: {'; '.join(e.likely_causes)}.  (Explanation is advisory — {e.source}.)")
    say()
    indent(shown_lines(govern.show(settings, client, rec.employee_ref), approvers))
    say(f"ADP right now: {adp_status(target.row(m))} — untouched.")

    say()
    say(f"{settings.principal}, who authorised the run, tries to approve it:")
    refused = govern.approve(settings, client, rec.employee_ref, settings.principal, "approved")
    for v in refused.violations:
        say(f"   refused — {v.get('message')}")
    if refused.ok:
        say("   UNEXPECTED: the principal's approval was accepted.")
        return 1

    approver = classify.approver(rec.change_class, approvers)
    assert approver is not None
    say()
    say(f"{approver} approves it:")
    decided = govern.approve(settings, client, rec.employee_ref, approver.name, "approved",
                             note="Confirmed with HR: termination is final.")
    say(f"   {decided.message}")
    synced = govern.sync(settings, client)
    after = next((x for x in synced.changed if x.fingerprint == rec.fingerprint), None)
    if not decided.ok or after is None or after.status != "applied":
        say(f"   UNEXPECTED: the approved correction was not applied ({synced.detail}).")
        return 1
    say("   sync → applied:")
    indent(shown_lines(govern.show(settings, client, rec.employee_ref), approvers))
    say(f"ADP now: {adp_status(target.row(m))}.")
    others = [x for x in r.records if x.status == "held" and x.fingerprint != rec.fingerprint]
    say(f"The other {len(others)} held corrections are still waiting; nothing was written for "
        f"them.")

    # 4 ─────────────────────────────────────────────────────────────────────────────────────
    beat(4, "The pattern")
    if not r.patterns:
        say("No pattern found.")
    for p in r.patterns:
        say(p.diagnosis)
        say(f"Suggested owner: {p.suggested_owner}.")
    if r.patterns:
        say()
        say("That's one integration ticket for IT, not one manual fix per row.")

    # 5 ─────────────────────────────────────────────────────────────────────────────────────
    beat(5, "And the record can be checked")
    verify = client.verify()
    say(f"GET /verify: {verify_line(verify)}")
    say("Each warrant issued or held, each decision and each committed result above is an "
        "event in that")
    say("hash-chained ledger. A refused request leaves no event: it never became an action.")
    say(f"Run report: {r.report}")
    return 0 if verify.get("verified") else 1
