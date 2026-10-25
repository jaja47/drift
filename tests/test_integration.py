"""Step 8: governance against a real Warrant process (tests/conftest.py starts it).

The full flow — run, approve, reject, sync, rerun, verify — runs once, in `flow`, and the tests
below check what each stage did. The failure paths each get their own state and, where they stop
Warrant, their own Warrant.
"""

from __future__ import annotations

import csv
import json
import re
import time
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

import httpx
import pytest

from drift import classify, cli, govern, report
from drift.connectors.csv_source import ADP_EXPORT, DEFAULT_DIR
from drift.govern import (
    RunResult,
    Settings,
    SyncResult,
    WarrantClient,
    WarrantUnreachable,
    apply_with_warrant,
)
from drift.demo import demo
from drift.state import Record, State
from drift.target.adp_target import CREATED_ID_PREFIX

PRINCIPAL = "Dana Okafor"  # fictional, as in Warrant's own tests
APPROVERS = classify.load_approvers()
SOURCE_ROWS_PATH = DEFAULT_DIR / ADP_EXPORT


def adp_rows(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {r["Associate_ID"]: r for r in csv.DictReader(f)}


SOURCE_ROWS = adp_rows(SOURCE_ROWS_PATH)
CROSSWALK = {r["Employee_ID"]: r["Associate_ID"] for r in csv.DictReader(
    (DEFAULT_DIR / "id_crosswalk.csv").open(newline="", encoding="utf-8"))}


def associate(ref: str) -> str:
    return ref.removeprefix("ADP:") if ref.startswith("ADP:") else CROSSWALK.get(ref, "")


def settings(tmp: Path) -> Settings:
    return Settings(principal=PRINCIPAL, state_dir=tmp / "state", reports_dir=tmp / "reports")


def by_ref(records: list[Record]) -> dict[tuple[str, str], Record]:
    return {(r.type, r.employee_ref): r for r in records}


# ── the full flow, once ──────────────────────────────────────────────────────────────────────


@dataclass
class Flow:
    s: Settings
    first: RunResult
    rows_after_first: dict
    approvals_after_first: dict  # Warrant's view of each held request, before anyone decides
    refused_principal: govern.DecisionResult
    refused_other: govern.DecisionResult
    approved: govern.DecisionResult
    rows_after_approve: dict
    rejected: govern.DecisionResult
    synced: SyncResult
    rows_after_sync: dict
    events_before_rerun: int
    second: RunResult
    events_after_rerun: int
    verify: dict


@pytest.fixture(scope="module")
def flow(warrant, tmp_path_factory) -> Flow:
    s = settings(tmp_path_factory.mktemp("flow"))
    client = WarrantClient(warrant.url, warrant.token)
    owen = APPROVERS["employment_status"].name
    priya = APPROVERS["compensation"].name
    try:
        first = govern.run(s, client)
        rows_after_first = adp_rows(s.target)
        approvals_after_first = {r.approval_id: warrant.get(f"/approval/{r.approval_id}").json()
                                 for r in first.records if r.approval_id}
        refused_principal = govern.approve(s, client, "W10026", PRINCIPAL, "approved")
        refused_other = govern.approve(s, client, "W10026", priya, "approved")
        approved = govern.approve(s, client, "W10026", owen, "approved")
        rows_after_approve = adp_rows(s.target)
        rejected = govern.approve(s, client, "W10076", priya, "rejected",
                                  note="Total Rewards to confirm the figure first")
        synced = govern.sync(s, client)
        rows_after_sync = adp_rows(s.target)
        events_before_rerun = len(warrant.events())
        second = govern.run(s, client)
        events_after_rerun = len(warrant.events())
        verify = client.verify()
    finally:
        client.close()
    return Flow(s, first, rows_after_first, approvals_after_first, refused_principal, refused_other, approved,
                rows_after_approve, rejected, synced, rows_after_sync, events_before_rerun, second,
                events_after_rerun, verify)


def test_routine_mismatches_are_applied_each_with_a_committed_warrant(flow: Flow,
                                                                      warrant) -> None:
    applied = [r for r in flow.first.records if r.status == "applied"]
    assert sorted(r.employee_ref for r in applied) == sorted(
        r.employee_ref for r in flow.first.records if r.change_class == "routine")
    assert len(applied) == 8
    for r in applied:
        w = warrant.get(f"/warrant/{r.warrant_id}").json()
        assert w["state"] == "committed"
        assert w["record"]["object"] == r.employee_ref
        assert w["record"]["change_class"] == "routine"
        assert w["outcome"]["row"] == f"Associate_ID={associate(r.employee_ref)}"
        assert w["approvals"] == []  # routine: no human in the loop
    for r in applied:
        row = flow.rows_after_first[associate(r.employee_ref)]
        assert row != SOURCE_ROWS[associate(r.employee_ref)]


def test_every_sensitive_mismatch_is_held_and_its_row_left_alone(flow: Flow) -> None:
    held = [r for r in flow.first.records if r.status == "held"]
    assert len(held) == 10
    assert all(r.change_class != "routine" for r in held)
    for r in held:
        assert r.warrant_id is None and r.approval_id
        a = flow.approvals_after_first[r.approval_id]
        assert a["state"] == "pending_approval"
        assert "warrant_id" not in a  # a 202 is not a success: nothing to act with
        assert r.requested_of == APPROVERS[r.change_class].name
        if r.type != "M5":
            assert flow.rows_after_first[associate(r.employee_ref)] == SOURCE_ROWS[
                associate(r.employee_ref)]
    assert not any(k.startswith(CREATED_ID_PREFIX) for k in flow.rows_after_first)
    changed = {k for k, v in flow.rows_after_first.items() if SOURCE_ROWS.get(k) != v}
    assert len(changed) == 8  # the routine fixes, and nothing else


def test_neither_the_principal_nor_another_approver_can_decide(flow: Flow) -> None:
    for refused in (flow.refused_principal, flow.refused_other):
        assert not refused.ok
        assert any("Owen Castellanos" in v["message"] for v in refused.violations)
    assert any("second person" in v["message"] for v in flow.refused_principal.violations)


def test_an_approval_writes_nothing_until_sync_then_applies_and_commits(flow: Flow,
                                                                        warrant) -> None:
    w10026 = associate("W10026")
    assert flow.approved.ok and flow.approved.record.status == "approved"
    assert flow.rows_after_approve[w10026] == SOURCE_ROWS[w10026]  # approval alone writes nothing
    after = by_ref(flow.synced.changed)[("M1", "W10026")]
    assert after.status == "applied" and after.decided_by == "Owen Castellanos"
    row = flow.rows_after_sync[w10026]
    assert (row["Position_Status"], row["Termination_Date"]) == ("T", "2026-10-13")
    w = warrant.get(f"/warrant/{after.warrant_id}").json()
    assert w["state"] == "committed" and w["record"]["object"] == "W10026"
    assert [a["approver"] for a in w["approvals"]] == ["Owen Castellanos"]


def test_a_rejected_correction_is_never_applied(flow: Flow, warrant) -> None:
    assert flow.rejected.ok and flow.rejected.record.status == "rejected"
    w10076 = associate("W10076")
    assert flow.rows_after_sync[w10076] == SOURCE_ROWS[w10076]
    assert by_ref(flow.second.records)[("M2", "W10076")].status == "rejected"
    a = warrant.get(f"/approval/{flow.rejected.record.approval_id}").json()
    assert a["state"] == "refused" and "warrant_id" not in a


def test_sync_leaves_the_undecided_ones_held(flow: Flow) -> None:
    assert [r.employee_ref for r in flow.synced.changed] == ["W10026"]
    assert len(flow.synced.waiting) == 8
    changed = {k for k, v in flow.rows_after_sync.items() if SOURCE_ROWS.get(k) != v}
    assert len(changed) == 9  # 8 routine + the approved termination


def test_rerunning_creates_no_new_warrants(flow: Flow) -> None:
    assert flow.events_after_rerun == flow.events_before_rerun
    assert flow.second.already == frozenset(m.fingerprint for m in flow.first.detection.mismatches)
    statuses = {(r.type, r.employee_ref): r.status for r in flow.second.records}
    assert list(statuses.values()).count("applied") == 9
    assert list(statuses.values()).count("held") == 8
    assert flow.second.outcome == "completed"


def test_the_ledger_verifies_intact_after_the_full_flow(flow: Flow) -> None:
    assert flow.verify["verified"] is True
    assert flow.second.verify["verified"] is True


def test_the_ledger_carries_per_record_purposes_and_no_personal_data(flow: Flow,
                                                                     warrant) -> None:
    events = warrant.events()
    asked = {e["subject_id"]: e["payload"]["record"]["purpose"] for e in events
             if e["kind"] in ("warrant.issued", "warrant.requested")
             and (e["payload"].get("context") or {}).get("run") == flow.first.run_id}
    assert len(asked) == 18 and len(set(asked.values())) == 18  # one purpose per record
    blob = json.dumps(events)
    deciders = {t for n in [PRINCIPAL, *(a.name for a in APPROVERS.values())] for t in n.split()}
    names = {t for p in flow.first.detection.pairs for n in (p.wd_name, p.adp_name) if n
             for t in n.split() if len(t) > 2} - deciders  # employees' names, not the deciders'
    assert "northpine.example" not in blob
    assert not names & set(re.findall(r"[^\W\d_]+", blob))
    pay = {v for m in flow.first.detection.mismatches if m.field == "pay"
           for v in (m.workday_value, m.adp_value)}
    for value in pay:
        assert value.removesuffix("/yr").split(".")[0] not in blob, value
    for m in flow.first.detection.mismatches:
        assert m.fingerprint not in blob


def test_the_report_says_what_happened_without_personal_data(flow: Flow) -> None:
    text = flow.first.report.read_text(encoding="utf-8")
    assert flow.first.report.name == f"{flow.first.run_id}.md"
    assert re.fullmatch(r"run_\d{8}T\d{6}Z", flow.first.run_id)
    assert "8 applied, 10 held for approval" in text
    assert "4 format differences correctly ignored" in text
    assert "7 org changes share the effective date 2026-10-01" in text
    assert "Owen Castellanos (Payroll Operations Lead)" in text
    assert "intact" in text
    for r in flow.first.records:
        assert r.employee_ref in text
    assert "@" not in text.replace("drift-reconciler@v", "")
    assert not re.search(r"(?<![\d.])\d+\.\d{2}(?!\d)", text)


# ── one correction, as Warrant holds it ──────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def client(warrant):
    c = WarrantClient(warrant.url, warrant.token)
    yield c
    c.close()


def test_show_opens_an_applied_warrant_with_its_purpose_and_sources(flow: Flow, client) -> None:
    shown = govern.show(flow.s, client, "W10015")
    record = shown.warrant["record"]
    assert shown.warrant["state"] == "committed" and shown.approval is None
    assert "W10015" in record["purpose"] and "CC-420" in record["purpose"]
    assert record["sources"] == ["workday_workers.csv#Employee_ID=W10015",
                                 f"adp_payroll.csv#Associate_ID={associate('W10015')}"]
    assert govern.show(flow.s, client, shown.record.warrant_id).warrant == shown.warrant
    lines = "\n".join(report.shown_lines(shown, APPROVERS))
    assert "Home_Department: 410 → 420" in lines and "none needed — routine" in lines


def test_show_of_a_held_correction_has_no_warrant_to_open(flow: Flow, client) -> None:
    shown = govern.show(flow.s, client, "W10112")
    assert shown.warrant is None and shown.approval["state"] == "pending_approval"
    assert govern.show(flow.s, client, shown.record.approval_id).record == shown.record
    lines = "\n".join(report.shown_lines(shown, APPROVERS))
    assert "No warrant exists until Owen Castellanos decides" in lines


def test_show_follows_an_approval_to_its_warrant_and_a_rejection_to_none(flow: Flow,
                                                                          client) -> None:
    approved = govern.show(flow.s, client, "W10026")
    assert approved.warrant["state"] == "committed"
    assert [a["approver"] for a in approved.warrant["approvals"]] == ["Owen Castellanos"]
    assert "the person it was requested of" in "\n".join(
        report.shown_lines(approved, APPROVERS))
    rejected = govern.show(flow.s, client, "W10076")
    assert rejected.warrant is None and rejected.approval["state"] == "refused"
    with pytest.raises(LookupError):
        govern.show(flow.s, client, "wrt_neverissued000000")


def test_the_source_export_is_never_modified(flow: Flow) -> None:
    assert adp_rows(SOURCE_ROWS_PATH) == SOURCE_ROWS
    assert flow.s.target.resolve().parent != SOURCE_ROWS_PATH.resolve().parent


def test_sync_acts_on_decisions_made_directly_in_warrant(warrant, tmp_path: Path) -> None:
    """Warrant is the source of truth: a decision made by another client (a page, a workflow)
    is picked up by sync exactly as one made through `approve`."""
    s = settings(tmp_path)
    client = WarrantClient(warrant.url, warrant.token)
    held = by_ref(govern.run(s, client).records)
    auth = {"Authorization": f"Bearer {warrant.token}"}
    for ref, who, decision in [(("M1", "W10112"), "Owen Castellanos", "approved"),
                               (("M2", "W10103"), "Priya Raman", "rejected")]:
        r = httpx.post(warrant.url + "/approval/decide", headers=auth, json={
            "approval_id": held[ref].approval_id, "approver": who, "decision": decision})
        assert r.status_code == 200
    # Before sync, Drift's record has no warrant id; show follows the approval to it.
    early = govern.show(s, client, "W10112")
    assert early.record.warrant_id is None and early.warrant["state"] == "issued"
    synced = by_ref(govern.sync(s, client).changed)
    assert (synced[("M1", "W10112")].status, synced[("M1", "W10112")].decided_by) == (
        "applied", "Owen Castellanos")
    assert (synced[("M2", "W10103")].status, synced[("M2", "W10103")].decided_by) == (
        "rejected", "Priya Raman")
    rows = adp_rows(s.target)
    assert rows[associate("W10112")]["Position_Status"] == "T"
    assert rows[associate("W10103")] == SOURCE_ROWS[associate("W10103")]


# ── fail closed ──────────────────────────────────────────────────────────────────────────────


def test_with_warrant_stopped_nothing_is_written(warrant_factory, tmp_path: Path) -> None:
    w = warrant_factory()
    w.stop()
    s = settings(tmp_path)
    r = govern.run(s, WarrantClient(w.url, w.token, timeout=2))
    assert r.outcome == "warrant_unreachable"
    assert "unreachable" in (r.detail or "")
    assert {x.status for x in r.records} == {"warrant_unreachable"}
    assert not s.target.exists()  # zero writes: the copy is never even made
    assert r.verify is None
    report = r.report.read_text(encoding="utf-8")
    assert "warrant_unreachable" in report and "Fail closed" in report

    # Nothing was settled, so once Warrant is back the next run asks again.
    w2 = warrant_factory()
    again = govern.run(s, WarrantClient(w2.url, w2.token))
    assert again.outcome == "completed" and again.already == frozenset()
    assert [x.status for x in again.records].count("applied") == 8


class StopWarrant(httpx.HTTPTransport):
    """Forwards to the real Warrant, and stops it just before the nth matching request."""

    def __init__(self, w, method: str, path: str, nth: int) -> None:
        super().__init__()
        self.w, self.method, self.path, self.nth, self.seen = w, method, path, nth, 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == self.method and request.url.path.startswith(self.path):
            self.seen += 1
            if self.seen == self.nth:
                self.w.stop()
        return super().handle_request(request)


def test_warrant_stopping_before_a_write_stops_every_later_write(warrant_factory,
                                                                 tmp_path: Path) -> None:
    w = warrant_factory()
    s = settings(tmp_path)
    # The second routine fix has its warrant, but Warrant is gone before it can be confirmed.
    client = WarrantClient(w.url, w.token, timeout=2,
                           transport=StopWarrant(w, "GET", "/warrant/", 2))
    r = govern.run(s, client)
    assert r.outcome == "warrant_unreachable"
    statuses = by_ref(r.records)
    assert statuses[("M3", "W10015")].status == "applied"
    assert statuses[("M3", "W10040")].status == "warrant_unreachable"
    later = r.records[r.records.index(statuses[("M3", "W10040")]):]
    assert {x.status for x in later} == {"warrant_unreachable"}
    changed = {k for k, v in adp_rows(s.target).items() if SOURCE_ROWS.get(k) != v}
    assert changed == {associate("W10015")}


class DropRequest(httpx.HTTPTransport):
    """Forwards to the real Warrant, but drops the warrant request for one employee, as a network
    blip would. Warrant itself stays up."""

    def __init__(self, employee_ref: str) -> None:
        super().__init__()
        self.employee_ref = employee_ref

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/warrant" \
                and json.loads(request.content).get("object") == self.employee_ref:
            raise httpx.ConnectError("dropped", request=request)
        return super().handle_request(request)


def test_one_dropped_request_stops_the_run_even_though_warrant_is_still_up(
        warrant, tmp_path: Path) -> None:
    s = settings(tmp_path)
    r = govern.run(s, WarrantClient(warrant.url, warrant.token, transport=DropRequest("W10040")))
    assert r.outcome == "warrant_unreachable"
    statuses = by_ref(r.records)
    assert statuses[("M3", "W10015")].status == "applied"
    later = r.records[r.records.index(statuses[("M3", "W10040")]):]
    assert {x.status for x in later} == {"warrant_unreachable"}  # not asked, though it is up
    changed = {k for k, v in adp_rows(s.target).items() if SOURCE_ROWS.get(k) != v}
    assert changed == {associate("W10015")}


def test_a_write_whose_commit_is_lost_is_committed_by_the_next_sync(warrant_factory,
                                                                    tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"  # a persistent Warrant, so the warrant survives a restart
    w = warrant_factory(WARRANT_STORE="jsonl", WARRANT_LEDGER=str(ledger))
    s = settings(tmp_path)
    client = WarrantClient(w.url, w.token, timeout=2,
                           transport=StopWarrant(w, "POST", "/warrant/commit", 2))
    r = govern.run(s, client)
    stuck = by_ref(r.records)[("M3", "W10040")]
    assert r.outcome == "warrant_unreachable"
    assert stuck.status == "uncommitted" and stuck.warrant_id
    assert adp_rows(s.target)[associate("W10040")]["Home_Department"] == "420"  # it was written

    w2 = warrant_factory(WARRANT_STORE="jsonl", WARRANT_LEDGER=str(ledger))
    client2 = WarrantClient(w2.url, w2.token)
    synced = govern.sync(s, client2)
    assert by_ref(synced.changed)[("M3", "W10040")].status == "applied"
    assert w2.get(f"/warrant/{stuck.warrant_id}").json()["state"] == "committed"
    assert client2.verify()["verified"] is True


def test_a_wrong_token_fails_closed(warrant, tmp_path: Path) -> None:
    s = settings(tmp_path)
    r = govern.run(s, WarrantClient(warrant.url, "not-the-token"))
    assert r.outcome == "warrant_unreachable" and "401" in (r.detail or "")
    assert not s.target.exists()


def test_a_refused_request_is_blocked_and_writes_nothing(warrant, tmp_path: Path,
                                                         monkeypatch) -> None:
    s = settings(tmp_path)
    before = len(warrant.events())
    monkeypatch.setattr(govern, "purpose", lambda m: f"Routine data cleanup for {m.employee_ref}")
    r = govern.run(s, WarrantClient(warrant.url, warrant.token))
    assert {x.status for x in r.records} == {"blocked"}
    assert all(any(v["field"] == "purpose" for v in x.violations) for x in r.records)
    assert len(warrant.events()) == before  # a 422 writes nothing to the ledger either
    assert not s.target.exists()
    assert "Blocked by Warrant" in r.report.read_text(encoding="utf-8")

    monkeypatch.undo()  # a blocked mismatch is asked again on the next run
    again = govern.run(s, WarrantClient(warrant.url, warrant.token))
    assert [x.status for x in again.records].count("applied") == 8


# ── a warrant id Drift did not get for this change writes nothing ────────────────────────────


def one(s: Settings, type_: str, ref: str):
    inputs = govern.read_inputs(s.source_dir)
    m = next(x for x in inputs.detection.mismatches if (x.type, x.employee_ref) == (type_, ref))
    rec = Record(m.fingerprint, m.type, m.employee_ref, m.field,
                 classify.change_class(m.type, m.field), govern.action_for(m), "held",
                 "run_test", "run_test")
    return m, rec, govern.target_for(s, inputs)


def test_a_forged_warrant_id_writes_nothing(warrant, tmp_path: Path) -> None:
    s = settings(tmp_path)
    m, rec, target = one(s, "M3", "W10015")
    with State(s.db) as state:
        after = apply_with_warrant(m, rec, "wrt_forged000000000000",
                                   client=WarrantClient(warrant.url, warrant.token),
                                   target=target, state=state)
    assert after.status == "failed" and "never issued" in after.detail
    assert not s.target.exists()


def test_a_warrant_for_a_different_change_writes_nothing(warrant, tmp_path: Path) -> None:
    s = settings(tmp_path)
    client = WarrantClient(warrant.url, warrant.token)
    m15, _, _ = one(s, "M3", "W10015")
    status, answer = client.request(govern.warrant_request(m15, principal=PRINCIPAL,
                                                           run_id="run_test"))
    assert status == 200
    m40, rec40, target = one(s, "M3", "W10040")
    with State(s.db) as state:
        after = apply_with_warrant(m40, rec40, answer["warrant_id"], client=client,
                                   target=target, state=state)
    assert after.status == "failed" and "different change (object)" in after.detail
    assert not s.target.exists()
    client.abort(answer["warrant_id"], "test: issued only to be misused")


def test_an_approved_change_that_fails_to_write_is_aborted(warrant, tmp_path: Path) -> None:
    s = settings(tmp_path)
    client = WarrantClient(warrant.url, warrant.token)
    m, rec, target = one(s, "M3", "W10015")
    target.reference = replace(target.reference, cost_centers={})  # nothing can be mapped
    status, answer = client.request(govern.warrant_request(m, principal=PRINCIPAL,
                                                           run_id="run_test"))
    with State(s.db) as state:
        after = apply_with_warrant(m, rec, answer["warrant_id"], client=client, target=target,
                                   state=state)
    assert after.status == "failed" and "nothing written" in after.detail
    w = warrant.get(f"/warrant/{answer['warrant_id']}").json()
    assert w["state"] == "aborted" and "cost centre" in w["outcome"]
    assert not s.target.exists()


# ── held requests that can no longer be decided ──────────────────────────────────────────────


def test_an_expired_approval_writes_nothing_and_is_asked_again(warrant_factory,
                                                               tmp_path: Path) -> None:
    w = warrant_factory(WARRANT_APPROVAL_TTL="1")
    s = settings(tmp_path)
    client = WarrantClient(w.url, w.token)
    first = govern.run(s, client)
    old = {r.approval_id for r in first.records if r.status == "held"}
    time.sleep(1.5)
    synced = govern.sync(s, client)
    assert {r.status for r in synced.changed} == {"expired"} and len(synced.changed) == 10
    late = govern.approve(s, client, "W10026", "Owen Castellanos", "approved")
    assert not late.ok and "expired" in late.message
    again = govern.run(s, client)
    held = [r for r in again.records if r.status == "held"]
    assert len(held) == 10 and not {r.approval_id for r in held} & old
    changed = {k for k, v in adp_rows(s.target).items() if SOURCE_ROWS.get(k) != v}
    assert len(changed) == 8


def test_a_warrant_restarted_without_its_ledger_writes_nothing(warrant_factory,
                                                               tmp_path: Path) -> None:
    w1 = warrant_factory()
    s = settings(tmp_path)
    govern.run(s, WarrantClient(w1.url, w1.token))
    w1.stop()
    w2 = warrant_factory()  # a fresh in-memory store: it has never heard of those approvals
    client = WarrantClient(w2.url, w2.token)
    synced = govern.sync(s, client)
    assert {r.status for r in synced.changed} == {"expired"}
    assert all("no record of this approval" in r.detail for r in synced.changed)
    changed = {k for k, v in adp_rows(s.target).items() if SOURCE_ROWS.get(k) != v}
    assert len(changed) == 8


def test_approve_and_sync_fail_closed_when_warrant_is_down(warrant_factory,
                                                           tmp_path: Path) -> None:
    w = warrant_factory()
    s = settings(tmp_path)
    govern.run(s, WarrantClient(w.url, w.token))
    w.stop()
    client = WarrantClient(w.url, w.token, timeout=2)
    with pytest.raises(WarrantUnreachable):
        govern.approve(s, client, "W10026", "Owen Castellanos", "approved")
    synced = govern.sync(s, client)
    assert synced.outcome == "warrant_unreachable" and synced.changed == []
    with State(s.db) as state:
        assert [r.status for r in state.all()].count("held") == 10


# ── the CLI is the same code ─────────────────────────────────────────────────────────────────


def test_the_cli_runs_approves_and_syncs(warrant, tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("WARRANT_URL", warrant.url)
    monkeypatch.setenv("WARRANT_TOKEN", warrant.token)
    monkeypatch.setenv("DRIFT_PRINCIPAL", PRINCIPAL)
    dirs = ["--state", str(tmp_path / "state")]
    assert cli.main(["run", *dirs, "--reports", str(tmp_path / "reports")]) == 0
    out = capsys.readouterr().out
    assert "8  applied" in out and "10  held for approval" in out and "Report:" in out
    assert "4 format differences correctly ignored" in out
    assert cli.main(["approve", "W10026", "--as", PRINCIPAL, "--decision", "approved",
                     *dirs]) == 1
    assert "second person" in capsys.readouterr().err
    assert cli.main(["approve", "W10026", "--as", "Owen Castellanos", "--decision",
                     "approved", *dirs]) == 0
    assert cli.main(["sync", *dirs]) == 0
    assert "1 moved on" in capsys.readouterr().out
    assert cli.main(["show", "W10015", *dirs]) == 0
    out = capsys.readouterr().out
    assert "Purpose" in out and "committed" in out and "none needed — routine" in out
    assert cli.main(["show", "W99999", *dirs]) == 1


def test_the_cli_refuses_to_run_without_a_principal(warrant, tmp_path: Path, monkeypatch,
                                                    capsys) -> None:
    monkeypatch.delenv("DRIFT_PRINCIPAL", raising=False)
    assert cli.main(["run", "--state", str(tmp_path / "state")]) == 2
    assert "DRIFT_PRINCIPAL" in capsys.readouterr().err
    assert not (tmp_path / "state").exists()


def test_the_cli_refuses_a_principal_who_is_an_approver(warrant, tmp_path: Path, monkeypatch,
                                                        capsys) -> None:
    monkeypatch.setenv("WARRANT_URL", warrant.url)
    monkeypatch.setenv("WARRANT_TOKEN", warrant.token)
    monkeypatch.setenv("DRIFT_PRINCIPAL", "Priya Raman")
    before = len(warrant.events())
    assert cli.main(["run", "--state", str(tmp_path / "state")]) == 2
    assert "second person" in capsys.readouterr().err
    assert len(warrant.events()) == before


# ── the demo is the same code, narrated ──────────────────────────────────────────────────────


def test_the_demo_shows_all_five_beats(warrant, tmp_path: Path, capsys) -> None:
    s = settings(tmp_path)
    assert demo(s, WarrantClient(warrant.url, warrant.token)) == 0
    out = capsys.readouterr().out
    for title in ("1 · Compare", "2 · Routine fixes went through on their own",
                  "3 · A terminated employee still active in payroll", "4 · The pattern",
                  "5 · And the record can be checked"):
        assert title in out
    assert "18 real mismatches" in out and "4 format differences correctly ignored" in out
    assert "Purpose       Change the ADP home department for W10015" in out
    assert "No warrant exists until Owen Castellanos decides" in out
    assert "refused — cannot decide an action you authorised" in out
    assert "ADP now: Position_Status T, Termination_Date 2026-09-18" in out
    assert "7 org changes share the effective date 2026-10-01" in out
    assert "GET /verify: intact" in out
    with State(s.db) as state:
        statuses = Counter(r.status for r in state.all())
    assert statuses == {"applied": 9, "held": 9}


def test_the_demo_refuses_stale_state(warrant, tmp_path: Path, capsys) -> None:
    s = settings(tmp_path)
    s.state_dir.mkdir(parents=True)
    s.db.touch()
    before = len(warrant.events())
    assert demo(s, WarrantClient(warrant.url, warrant.token)) == 2
    assert "fresh state" in capsys.readouterr().out
    assert len(warrant.events()) == before
