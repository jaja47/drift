"""Governance: every correction gets a warrant from Warrant before anything is written. Fails closed.

`run` takes each mismatch not already settled in state/drift.db and asks Warrant for a warrant
under policy `hr-record-correction`:

- **422** → `blocked`. The violations are stored and nothing is written.
- **202** → `held` for the approver named in change_classes.yaml. The approval id is stored and
  nothing is written. A 202 is not a success: there is no warrant id, so nothing can be written.
- **200** → `apply_with_warrant`. The warrant is read back from Warrant and checked against this
  exact change. Then the fix is written to the ADP copy, and the warrant is committed — or aborted
  if the write failed.
- **no usable answer** (connection refused, timeout, 401, 5xx) → the run stops with
  `warrant_unreachable`, and nothing more is written.

`approve` posts a named person's decision to Warrant. `sync` asks Warrant about every held request
and applies + commits each one that now has a warrant id.

The change class, the approver and the action come from code and config (classify.py), never from
an LLM. The purpose is computed for each record. It never carries a pay figure, a name or an
email, because the ledger is exportable. Neither does any other part of the request. The
fingerprint stays out of the ledger too: it is a hash of the two values, so a pay fingerprint
could be brute-forced back to the figures.

`apply_with_warrant` is the only code that calls the target.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import quote

import httpx

from drift import __version__, classify, report
from drift.connectors import CsvAdpSource, CsvWorkdaySource, Reference, SourceRow, read_reference
from drift.connectors.csv_source import ADP_EXPORT, DEFAULT_DIR, WORKDAY_EXPORT
from drift.detect import Detection, Mismatch, detect_sources
from drift.patterns import Pattern, find_patterns
from drift.state import AWAITING, SETTLED, Record, State
from drift.target import AdpTarget

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
REPORTS_DIR = ROOT / "reports"

POLICY_ID = "hr-record-correction"
ACTOR = f"drift-reconciler@v{__version__}"
SYSTEM = "adp"
ACTIONS = {"M5": "create", "M6": "deactivate"}  # every other type corrects an existing record

FIELD_WORDS = {
    "worker_status": "employment status", "pay": "annual pay", "cost_center": "cost centre",
    "manager": "manager", "record": "payroll record", "legal_name": "legal name",
    "location": "location", "work_email": "work email", "rate_type": "rate type",
}
# Fields whose values may appear in a purpose. Anything else (pay, names, emails, and any field
# Drift does not know) is described without its values.
SHOWN_VALUES = frozenset({"worker_status", "cost_center", "manager", "location", "rate_type"})


# ── Warrant, over HTTP ───────────────────────────────────────────────────────────────────────


class WarrantUnreachable(RuntimeError):
    """No usable answer from Warrant: it is down, it timed out, or it sent an answer Drift cannot
    act on (such as 401). Drift writes nothing more."""


class WarrantClient:
    """Warrant's HTTP API. Drift never imports, starts or reads Warrant; this is the whole
    interface."""

    def __init__(self, url: str, token: str, *, timeout: float = 5.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.url = url.rstrip("/")
        self.http = httpx.Client(base_url=self.url, timeout=timeout, transport=transport,
                                 headers={"Authorization": f"Bearer {token}"})

    @classmethod
    def from_env(cls) -> WarrantClient:
        return cls(os.environ.get("WARRANT_URL") or "http://localhost:8000",
                   os.environ.get("WARRANT_TOKEN") or "")

    def close(self) -> None:
        self.http.close()

    def _call(self, method: str, path: str, expected: tuple[int, ...],
              body: dict | None = None) -> tuple[int, dict]:
        try:
            r = self.http.request(method, path, json=body)
        except httpx.TransportError as e:
            raise WarrantUnreachable(
                f"Warrant at {self.url} is unreachable ({type(e).__name__})") from None
        if r.status_code not in expected:
            try:
                error = r.json().get("error")
            except ValueError:
                error = None
            raise WarrantUnreachable(f"Warrant answered {r.status_code} "
                                     f"{error or r.reason_phrase} to {method} {path}")
        try:
            return r.status_code, r.json()
        except ValueError:
            raise WarrantUnreachable(f"Warrant's answer to {method} {path} is not JSON") from None

    def request(self, body: dict) -> tuple[int, dict]:
        """POST /warrant → 200 issued, 202 held, 422 refused."""
        return self._call("POST", "/warrant", (200, 202, 422), body)

    def warrant(self, warrant_id: str) -> dict | None:
        """The warrant's current state, or None if Warrant has never issued it."""
        status, data = self._call("GET", f"/warrant/{quote(warrant_id, safe='')}", (200, 404))
        return data if status == 200 else None

    def commit(self, warrant_id: str, result: dict, sources: list[str] | None) -> str:
        """Seal the result. Returns the warrant's state afterwards: `committed`, or the state
        that stopped the commit. Committing twice (a retried commit) is not an error."""
        status, _ = self._call("POST", "/warrant/commit", (200, 409),
                               {"warrant_id": warrant_id, "result": result, "sources": sources})
        if status == 200:
            return "committed"
        current = self.warrant(warrant_id)
        return current.get("state", "unknown") if current else "unknown"

    def abort(self, warrant_id: str, reason: str) -> None:
        self._call("POST", "/warrant/abort", (200, 409),
                   {"warrant_id": warrant_id, "reason": reason})

    def decide(self, approval_id: str, approver: str, decision: str,
               note: str | None) -> tuple[int, dict]:
        return self._call("POST", "/approval/decide", (200, 404, 409, 410, 422), {
            "approval_id": approval_id, "approver": approver, "decision": decision,
            "note": note})

    def approval(self, approval_id: str) -> tuple[int, dict]:
        return self._call("GET", f"/approval/{quote(approval_id, safe='')}", (200, 404))

    def verify(self) -> dict:
        return self._call("GET", "/verify", (200,))[1]


# ── the request, computed per record ─────────────────────────────────────────────────────────


def action_for(m: Mismatch) -> str:
    return ACTIONS.get(m.type, "update")


def _date_in(value: str | None) -> str | None:
    found = re.search(r"\d{4}-\d{2}-\d{2}", value or "")
    return found.group(0) if found else None


def purpose(m: Mismatch) -> str:
    """Why this one change is being made: the employee ref, the field and the reason. Never a pay
    figure, a name or an email."""
    ref = m.employee_ref
    effective = f", effective {m.org_effective_date}" if m.org_effective_date else ""
    if m.type == "M1":
        when = _date_in(m.workday_value) or "the date Workday records"
        return (f"Terminate {ref} in ADP as of {when} to match Workday, where they are "
                f"terminated; ADP still shows them as {m.adp_value or 'not terminated'}, so they "
                f"could be paid after leaving")
    if m.type == "M2":
        since = (f" (Workday comp change effective {m.comp_effective_date})"
                 if m.comp_effective_date else "")
        return (f"Set annual pay for {ref} in ADP to the Workday figure{since}; the two systems "
                f"disagree on what this employee is paid")
    if m.type == "M3":
        return (f"Change the ADP home department for {ref} from {m.adp_value} to match their "
                f"Workday cost centre, {m.workday_value}{effective}")
    if m.type == "M4":
        return (f"Change who {ref} reports to in ADP from {m.adp_value} to {m.workday_value}, "
                f"their manager in Workday{effective}")
    if m.type == "M5":
        hired = _date_in(m.workday_value) or "a date Workday records"
        return (f"Create an ADP payroll record for {ref}, hired {hired} and active in Workday, "
                f"who has no payroll record and so would not be paid")
    if m.type == "M6":
        return (f"Deactivate ADP payroll record {ref} ({m.adp_value or 'status unknown'} in "
                f"ADP): no Workday worker matches it, so it may be a ghost record")
    if m.type == "M7":
        return (f"Correct the legal name on the ADP record for {ref} to the legal name in "
                f"Workday; the names differ (withheld here as personal data)")
    field = FIELD_WORDS.get(m.field, m.field)
    if m.type == "M8":
        values = (f" ({m.workday_value or 'blank'} in Workday, {m.adp_value or 'blank'} in ADP)"
                  if m.field in SHOWN_VALUES else "")
        return (f"Update {field} for {ref} in ADP to match Workday{values}; no specific "
                f"reconciliation rule explains this difference")
    return (f"Update {field} for {ref} in ADP to match Workday; Drift has no rule for mismatch "
            f"type {m.type!r}, so it is held for review")


def sources(m: Mismatch) -> list[str]:
    """Both systems' rows. A record missing from one side cites the search that found nothing:
    a reconciliation that cites only one system has not reconciled anything."""
    return [m.workday_source
            or f"workday: no worker joined to {m.employee_ref} through the ID crosswalk",
            m.adp_source
            or f"adp: no payroll record joined to {m.employee_ref} through the ID crosswalk"]


def warrant_request(m: Mismatch, *, principal: str, run_id: str,
                    rules: Mapping[str, classify.TypeRule] | None = None,
                    approvers: Mapping[str, classify.Approver] | None = None) -> dict:
    change_class = classify.change_class(m.type, m.field, rules)
    body = {
        "policy_id": POLICY_ID,
        "actor": ACTOR,
        "principal": principal,
        "system": SYSTEM,
        "action": action_for(m),
        "object": m.employee_ref,
        "field": m.field,
        "change_class": change_class,
        "purpose": purpose(m),
        "sources": sources(m),
        "context": {"run": run_id, "mismatch_type": m.type},
    }
    approver = classify.approver(change_class, approvers)
    if approver is not None:
        body["approval_from"] = approver.name
    return body


# ── inputs and settings ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Settings:
    principal: str  # the named human who authorised this reconciliation
    source_dir: Path = DEFAULT_DIR
    state_dir: Path = STATE_DIR
    reports_dir: Path = REPORTS_DIR
    today: date | None = None  # the date a deactivation takes effect; defaults to today

    @property
    def db(self) -> Path:
        return self.state_dir / "drift.db"

    @property
    def target(self) -> Path:
        return self.state_dir / "adp_current.csv"


@dataclass(frozen=True)
class Inputs:
    detection: Detection
    workers: Mapping[str, SourceRow]  # Workday rows by Employee_ID, for the target
    reference: Reference
    adp_export: Path


def read_inputs(source_dir: Path) -> Inputs:
    workers = CsvWorkdaySource(source_dir / WORKDAY_EXPORT)
    payroll = CsvAdpSource(source_dir / ADP_EXPORT)
    reference = read_reference(source_dir)
    detection = detect_sources(workers, payroll, reference)
    by_id = {r.values["Employee_ID"].strip(): r for r in workers.list_workers()}
    return Inputs(detection, by_id, reference, payroll.path)


def _target(settings: Settings, inputs: Inputs) -> AdpTarget:
    return AdpTarget(inputs.adp_export, settings.target, inputs.workers, inputs.reference,
                     settings.today)


# ── the only path to the target ──────────────────────────────────────────────────────────────


def apply_with_warrant(m: Mismatch, rec: Record, warrant_id: str, *, client: WarrantClient,
                       target: AdpTarget, state: State) -> Record:
    """Write one fix, only under an issued warrant for exactly this change, then commit it — or
    abort it if the write fails. Each step is saved as it happens."""
    issued = client.warrant(warrant_id)
    wanted = {"system": SYSTEM, "action": rec.action, "object": rec.employee_ref,
              "field": rec.field, "change_class": rec.change_class}
    held = (issued or {}).get("record") or {}
    wrong = [k for k, v in wanted.items() if held.get(k) != v]
    if issued is None or issued.get("policy_id") != POLICY_ID or wrong \
            or issued.get("state") != "issued":
        why = ("Warrant never issued this warrant id" if issued is None
               else f"the warrant is {issued.get('state')}, not issued"
               if issued.get("state") != "issued"
               else f"the warrant is for a different change ({', '.join(wrong) or 'policy'})")
        return state.save(replace(rec, status="failed", detail=f"not written: {why}"))

    rec = replace(rec, warrant_id=warrant_id)
    try:
        result = target.apply(m, warrant_id=warrant_id)
    except Exception as e:  # writes are atomic, so nothing was written; the ledger says why
        reason = f"write failed, nothing written: {e}"
        try:
            client.abort(warrant_id, reason)
        except WarrantUnreachable:
            state.save(replace(rec, status="failed",
                               detail=reason + " (the abort could not be recorded)"))
            raise
        return state.save(replace(rec, status="failed", detail=reason))

    rec = state.save(replace(rec, status="uncommitted", result=result,
                             detail="written; the commit is not recorded yet"))
    outcome = client.commit(warrant_id, result, sources(m))
    if outcome != "committed":
        return state.save(replace(rec, detail=f"written, but Warrant would not commit it "
                                              f"(the warrant is {outcome})"))
    changed = ", ".join(result["changed"]) or "nothing needed changing"
    return state.save(replace(rec, status="applied", detail=f"wrote {changed}"))


# ── run ──────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RunResult:
    run_id: str
    started: datetime
    principal: str
    warrant_url: str
    outcome: str  # completed | warrant_unreachable
    detail: str | None
    detection: Detection
    patterns: list[Pattern]
    records: list[Record]  # one per detected mismatch, in detection order
    already: frozenset[str]  # fingerprints settled by an earlier run: no new warrant requested
    verify: dict | None
    report: Path | None = None


def _run_id(state: State, started: datetime) -> str:
    base = "run_" + started.strftime("%Y%m%dT%H%M%SZ")
    taken = {r["run_id"] for r in state.runs()}
    n, run_id = 1, base
    while run_id in taken:
        n += 1
        run_id = f"{base}-{n}"
    return run_id


def _request(m: Mismatch, rec: Record, *, settings: Settings, run_id: str,
             client: WarrantClient, target: AdpTarget, state: State,
             rules: Mapping[str, classify.TypeRule],
             approvers: Mapping[str, classify.Approver]) -> Record:
    status, answer = client.request(warrant_request(
        m, principal=settings.principal, run_id=run_id, rules=rules, approvers=approvers))
    if status == 422:
        return state.save(replace(rec, status="blocked", violations=answer.get("violations", []),
                                  detail="Warrant refused the request; nothing written"))
    if status == 202:
        if not answer.get("approval_id"):
            raise WarrantUnreachable("Warrant held a request without giving an approval id")
        return state.save(replace(
            rec, status="held", approval_id=answer["approval_id"],
            requested_of=answer.get("requested_of"),
            detail=f"held for {answer.get('requested_of')}; nothing written"))
    if not answer.get("warrant_id"):
        raise WarrantUnreachable("Warrant answered 200 without a warrant id")
    return apply_with_warrant(m, rec, answer["warrant_id"], client=client, target=target,
                              state=state)


def run(settings: Settings, client: WarrantClient) -> RunResult:
    """Detect, then govern every mismatch not already settled. Writes the report."""
    rules, approvers = classify.load_rules(), classify.load_approvers()
    classify.check_principal(settings.principal, approvers)
    inputs = read_inputs(settings.source_dir)
    target = _target(settings, inputs)
    patterns = find_patterns(inputs.detection.mismatches, inputs.detection.pairs)
    started = datetime.now(UTC)

    with State(settings.db) as state:
        run_id = _run_id(state, started)
        state.start_run(run_id, settings.principal)
        outcome, detail = "completed", None
        records: list[Record] = []
        already: set[str] = set()
        for m in inputs.detection.mismatches:
            prior = state.get(m.fingerprint)
            if prior and prior.status in SETTLED:
                already.add(m.fingerprint)
                records.append(state.save(replace(prior, last_run=run_id)))
                continue
            rec = Record(
                fingerprint=m.fingerprint, type=m.type, employee_ref=m.employee_ref,
                field=m.field, change_class=classify.change_class(m.type, m.field, rules),
                action=action_for(m), status="warrant_unreachable",
                first_run=prior.first_run if prior else run_id, last_run=run_id)
            if outcome != "completed":
                records.append(state.save(replace(
                    rec, detail="not requested: Warrant was unreachable earlier in this run")))
                continue
            try:
                rec = _request(m, rec, settings=settings, run_id=run_id, client=client,
                               target=target, state=state, rules=rules, approvers=approvers)
            except WarrantUnreachable as e:
                outcome, detail = "warrant_unreachable", str(e)
                saved = state.get(m.fingerprint)  # saved mid-write, if it got that far
                rec = (saved if saved and saved.last_run == run_id
                       else state.save(replace(
                           rec, detail=f"no usable answer from Warrant; nothing written ({e})")))
            records.append(rec)

        verify = None
        if outcome == "completed":
            try:
                verify = client.verify()
            except WarrantUnreachable as e:
                outcome, detail = "warrant_unreachable", str(e)

        result = RunResult(run_id, started, settings.principal, client.url, outcome, detail,
                           inputs.detection, patterns, records, frozenset(already), verify)
        path = report.write(result, settings.reports_dir, approvers)
        state.finish_run(run_id, outcome, detail, path)
    return replace(result, report=path)


# ── approve and sync ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DecisionResult:
    ok: bool
    message: str
    record: Record | None = None
    violations: tuple[dict, ...] = ()


def find(state: State, selector: str) -> Record:
    """A mismatch by employee ref or by fingerprint (a prefix of at least 6 characters)."""
    s = selector.strip()
    records = state.all()
    found = [r for r in records if r.employee_ref == s]
    if not found and len(s) >= 6:
        found = [r for r in records if r.fingerprint.startswith(s.lower())]
    held = [r for r in found if r.status == "held"]
    if len(found) > 1 and len(held) == 1:
        return held[0]
    if not found:
        raise LookupError(f"no mismatch matches {s!r} — use an employee ref or a fingerprint")
    if len(found) > 1:
        raise LookupError(f"{s!r} matches {len(found)} mismatches — use the fingerprint")
    return found[0]


def approve(settings: Settings, client: WarrantClient, selector: str, approver: str,
            decision: str, note: str | None = None) -> DecisionResult:
    """Record a named person's decision on a held correction, in Warrant. Nothing is written here:
    an approved correction is written and committed by the next `sync`."""
    if decision not in ("approved", "rejected"):
        return DecisionResult(False, "the decision must be `approved` or `rejected`")
    with State(settings.db) as state:
        try:
            rec = find(state, selector)
        except LookupError as e:
            return DecisionResult(False, str(e))
        if rec.status != "held" or not rec.approval_id:
            return DecisionResult(False, f"{rec.employee_ref} {rec.field} is {rec.status}, not "
                                         f"waiting for a decision", rec)
        status, answer = client.decide(rec.approval_id, approver, decision, note)
        if status == 422:
            return DecisionResult(False, "Warrant refused the decision", rec,
                                  tuple(answer.get("violations", [])))
        if status in (404, 410):
            rec = state.save(replace(rec, status="expired", detail=(
                "the request lapsed undecided" if status == 410
                else "Warrant has no record of this approval") + "; nothing written — the next "
                "run asks again"))
            return DecisionResult(False, rec.detail or "", rec)
        if status == 409:
            return DecisionResult(False, f"already decided in Warrant ({answer.get('message')}); "
                                         f"run sync", rec)
        if answer.get("decision") == "approved":
            rec = state.save(replace(rec, status="approved", decided_by=approver,
                                     warrant_id=answer.get("warrant_id"),
                                     detail=f"approved by {approver}; written on the next sync"))
            return DecisionResult(True, f"approved by {approver} — `sync` writes it", rec)
        rec = state.save(replace(rec, status="rejected", decided_by=approver,
                                 detail=f"rejected by {approver}; never written"))
        return DecisionResult(True, f"rejected by {approver} — nothing will be written", rec)


@dataclass(frozen=True)
class SyncResult:
    outcome: str  # completed | warrant_unreachable
    detail: str | None
    changed: list[Record]  # moved on: applied, rejected, expired, failed
    waiting: list[Record]  # still held, or still uncommitted


def _last_approver(answer: dict) -> str | None:
    decisions = answer.get("decisions") or []
    return decisions[-1].get("approver") if decisions else None


def _sync_one(rec: Record, m: Mismatch | None, *, client: WarrantClient, target: AdpTarget,
              state: State) -> Record:
    if rec.status == "uncommitted":  # written earlier; only the commit is missing
        outcome = client.commit(rec.warrant_id or "", rec.result or {},
                                sources(m) if m else None)
        if outcome != "committed":
            return state.save(replace(rec, detail=f"written, but Warrant would not commit it "
                                                  f"(the warrant is {outcome})"))
        return state.save(replace(rec, status="applied", detail="written; commit recorded late"))

    status, answer = client.approval(rec.approval_id or "")
    if status == 404:
        return state.save(replace(rec, status="expired", detail=(
            "Warrant has no record of this approval (was it restarted with an in-memory "
            "store?); nothing written — the next run asks again")))
    where = answer.get("state")
    if where == "pending_approval":
        return rec
    if where == "expired":
        return state.save(replace(rec, status="expired", detail=(
            "the request lapsed undecided; nothing written — the next run asks again")))
    if where == "refused":
        who = _last_approver(answer) or rec.decided_by
        return state.save(replace(rec, status="rejected", decided_by=who,
                                  detail=f"rejected by {who}; never written"))
    if where == "issued":
        warrant_id = answer.get("warrant_id") or ""
        rec = replace(rec, decided_by=_last_approver(answer) or rec.decided_by)
        if m is None:  # approved, but the exports no longer show this mismatch
            client.abort(warrant_id, "the mismatch is no longer in the exports; nothing written")
            return state.save(replace(rec, status="failed", warrant_id=warrant_id, detail=(
                "approved, but no longer in the exports; nothing written, warrant aborted")))
        return apply_with_warrant(m, rec, warrant_id, client=client, target=target, state=state)
    if where == "committed":
        return state.save(replace(rec, status="applied", warrant_id=answer.get("warrant_id"),
                                  detail="already committed in Warrant"))
    if where == "aborted":
        return state.save(replace(rec, status="failed", warrant_id=answer.get("warrant_id"),
                                  detail="the warrant was aborted; nothing written"))
    raise WarrantUnreachable(f"Warrant reported an unknown state {where!r}")


def sync(settings: Settings, client: WarrantClient) -> SyncResult:
    """Ask Warrant about every held request; write + commit the ones a human has approved."""
    inputs = read_inputs(settings.source_dir)
    target = _target(settings, inputs)
    current = {m.fingerprint: m for m in inputs.detection.mismatches}
    changed: list[Record] = []
    waiting: list[Record] = []
    outcome, detail = "completed", None
    with State(settings.db) as state:
        for rec in state.all():
            if rec.status not in AWAITING:
                continue
            try:
                after = _sync_one(rec, current.get(rec.fingerprint), client=client,
                                  target=target, state=state)
            except WarrantUnreachable as e:
                outcome, detail = "warrant_unreachable", str(e)
                break
            (changed if after.status != rec.status else waiting).append(after)
    return SyncResult(outcome, detail, changed, waiting)
