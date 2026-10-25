"""Streamlit review page, for a non-technical HR reader. Layout only — no logic here.

Everything it shows comes from `review.snapshot`, and every button calls a `review` action that
calls the same governance code as the CLI (`govern.run`, `approve`, `sync`, `show`).

    make app        (starts Warrant with a persistent ledger, then this page)
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import streamlit as st

from drift import classify, govern, review
from drift.report import shown_facts

st.set_page_config(page_title="Drift · Workday ↔ ADP", layout="wide")


def flash(outcome: review.Outcome, fingerprint: str = "") -> None:
    """Show an outcome after the rerun: at the top for the page's own buttons, or inside the
    row that was decided (kept open, with a pop-up wherever the reader has scrolled to)."""
    st.session_state["flash"] = (fingerprint, outcome)


def show_flash(fingerprint: str = "") -> None:
    pending = st.session_state.get("flash")
    if pending is None or pending[0] != fingerprint:
        return
    outcome = st.session_state.pop("flash")[1]
    lines = "\n".join(f"- {d}" for d in outcome.details if d)
    (st.success if outcome.ok else st.error)(outcome.message + (f"\n\n{lines}" if lines else ""))
    if fingerprint:
        st.toast(outcome.message)


def flash_for(fingerprint: str) -> bool:
    pending = st.session_state.get("flash")
    return pending is not None and pending[0] == fingerprint


def facts_table(facts: list[tuple[str, str]]) -> str:
    cell = lambda v: str(v).replace("|", "\\|")  # noqa: E731
    label = lambda k: f"**{cell(k)}**" if k else ""  # noqa: E731
    return "| | |\n|---|---|\n" + "\n".join(f"| {label(k)} | {cell(v)} |" for k, v in facts)


def audit_footer(r: review.Review, client: govern.WarrantClient) -> None:
    st.divider()
    if r.verify is None:
        st.error(f"Audit log: the audit service (Warrant) is unreachable — nothing can be fixed "
                 f"until it is back. ({r.warrant_error})")
    elif r.verify.get("verified"):
        st.success(f"Audit log: verified ✓ ({r.verify.get('events')} events)")
    else:
        st.error(f"Audit log: BROKEN at event {r.verify.get('broken_at')} — "
                 f"{r.verify.get('reason')}")
    left, right, _ = st.columns([1, 1, 3])
    if r.verify is not None:
        try:
            left.download_button("Export the audit log (JSONL)", client.export(),
                                 file_name=f"warrant-evidence-{date.today()}.jsonl",
                                 mime="application/x-ndjson")
        except govern.WarrantUnreachable:
            left.caption("Audit log export unavailable.")
    report = (r.last_run or {}).get("report")
    if report and Path(report).exists():
        right.download_button("Download the run report", Path(report).read_bytes(),
                              file_name=Path(report).name, mime="text/markdown")
    if r.explanations_note:
        st.caption(f"Explanations: {r.explanations_note}. They are advisory and never change "
                   f"what happens.")


def row_details(row: review.Row, settings: govern.Settings, client: govern.WarrantClient,
                approvers) -> None:
    with st.expander(f"{row.employee_ref} · {row.label} — {row.status_words}",
                     expanded=flash_for(row.fingerprint)):
        show_flash(row.fingerprint)
        st.markdown(f"**What differs:** {row.what}")
        e = row.explanation
        st.markdown(f"{e.explanation}\n\n**Likely causes:** " + "; ".join(e.likely_causes)
                    + f"\n\n**Suggested owner:** {e.suggested_owner}")
        try:
            facts = shown_facts(govern.show(settings, client, row.fingerprint), approvers)
        except (govern.WarrantUnreachable, LookupError) as err:
            facts = [("Audit record", f"unavailable — {err}")]
        if not any(k == "Sources" for k, _ in facts):
            facts += [("Sources" if i == 0 else "", s) for i, s in enumerate(row.sources)]
        st.markdown(facts_table(facts))
        if row.can_decide:
            st.markdown(f"**Decide** — only {row.approver} can. The person who authorised the "
                        f"run cannot.")
            who = st.text_input("Decide as", key=f"who-{row.fingerprint}",
                                placeholder="Your full name")
            note = st.text_input("Note (optional)", key=f"note-{row.fingerprint}")
            approve, reject, _ = st.columns([1, 1, 4])
            if approve.button("Approve", key=f"approve-{row.fingerprint}", type="primary"):
                flash(review.decide(settings, client, row.fingerprint, who, "approved", note),
                      row.fingerprint)
                st.rerun()
            if reject.button("Reject", key=f"reject-{row.fingerprint}"):
                flash(review.decide(settings, client, row.fingerprint, who, "rejected", note),
                      row.fingerprint)
                st.rerun()


def page(settings: govern.Settings, client: govern.WarrantClient) -> None:
    st.title("Drift")
    st.markdown("Finds where **Workday** and **ADP** disagree, explains why, and fixes only "
                "what it is allowed to.")
    st.info("Synthetic data. No real Workday or ADP system is connected.")
    show_flash()
    r = review.snapshot(settings, client)

    if not r.has_run:
        st.markdown(f"Drift compares every employee in the Workday export with the ADP payroll "
                    f"export. Routine fixes are applied under an audit warrant; pay, status and "
                    f"identity changes wait for a named person.\n\nThis run is authorised by "
                    f"**{settings.principal}**.")
        if st.button("Compare Workday and ADP", key="run", type="primary"):
            flash(review.start_run(settings, client))
            st.rerun()
        audit_footer(r, client)
        return

    last = r.last_run or {}
    top, run_again, check = st.columns([4, 1, 1])
    top.caption(f"Authorised by **{r.principal}** · last run {r.last_run_at} · "
                f"{last.get('outcome')}")
    if run_again.button("Run again", key="rerun"):
        flash(review.start_run(settings, client))
        st.rerun()
    if check.button("Check for decisions", key="sync"):
        flash(review.check_decisions(settings, client))
        st.rerun()

    cols = st.columns(5)
    cols[0].metric("Employees compared", r.compared)
    cols[1].metric("Mismatches", r.mismatches)
    cols[2].metric("Fixed automatically", r.fixed_automatically,
                   help="Routine fixes (cost centre, manager), each under its own warrant.")
    cols[3].metric("Waiting for approval", r.waiting,
                   help="Pay, employment status and identity changes wait for a named person.")
    cols[4].metric("Format differences ignored", len(r.ignored),
                   help="Values that only look different — same data, different format.")
    # Always one caption here, so the page keeps its shape (and open rows stay open) on reruns.
    st.caption(f"{r.fixed_after_approval} more fixed after a named person approved."
               if r.fixed_after_approval else
               "Nothing else changes until a named person approves it.")
    with st.expander("Format differences ignored — and why"):
        for f in r.ignored:
            st.markdown(f"- **{f.employee_ref}** — {f.why}")

    st.subheader("Root causes")
    with st.container(border=True):
        if not r.patterns:
            st.markdown("No shared cause found — no group of 3 or more mismatches has one.")
        for p in r.patterns:
            st.markdown(f"**{p.diagnosis}**  \nSuggested owner: {p.suggested_owner}")

    st.subheader("Every mismatch")
    st.dataframe([{"Employee": row.employee_ref, "What differs": row.what,
                   "Class": row.class_words, "Status": row.status_words,
                   "Approver": row.approver} for row in r.rows],
                 hide_index=True, width="stretch")

    approvers = classify.load_approvers()
    waiting = [row for row in r.rows if row.can_decide]
    st.subheader(f"Waiting for a decision ({len(waiting)})")
    for row in waiting:
        row_details(row, settings, client, approvers)
    st.subheader("Everything else")
    for row in r.rows:
        if not row.can_decide:
            row_details(row, settings, client, approvers)

    audit_footer(r, client)


try:
    page_settings = review.settings_from_env()
except classify.ConfigError as e:
    st.error(str(e))
    st.stop()
page_client = govern.WarrantClient.from_env()
try:
    page(page_settings, page_client)
finally:
    page_client.close()
