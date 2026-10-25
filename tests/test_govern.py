"""Step 8, without Warrant: the warrant requests Drift builds, the approvers it names, and the
state it keeps. tests/test_integration.py runs the same code against a real Warrant."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from drift import classify
from drift.classify import ConfigError, approver, check_principal, load_approvers
from drift.cli import detection_from
from drift.connectors.csv_source import DEFAULT_DIR
from drift.detect import Mismatch
from drift.govern import ACTOR, POLICY_ID, find, purpose, sources, warrant_request
from drift.state import AWAITING, SETTLED, STATUSES, Record, State

ROOT = Path(__file__).resolve().parent.parent
DETECTION = detection_from(DEFAULT_DIR)
PRINCIPAL = "Dana Okafor"
NAME_TOKENS = {p.employee_ref: {t.casefold() for n in (p.wd_name, p.adp_name) if n
                                for t in n.split()} for p in DETECTION.pairs}
PAY_VALUES = {m.employee_ref: {v.removesuffix("/yr") for v in (m.workday_value, m.adp_value) if v}
              for m in DETECTION.mismatches if m.field == "pay"}


def request(m: Mismatch) -> dict:
    return warrant_request(m, principal=PRINCIPAL, run_id="run_test")


EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)*\.[A-Za-z]{2,}")


def assert_no_personal_data(text: str, ref: str) -> None:
    assert not EMAIL.search(text) and "northpine.example" not in text, ref
    assert not re.search(r"(?<![\d.])\d+\.\d{2}(?!\d)", text), (ref, text)
    for figure in PAY_VALUES.get(ref, ()):
        assert figure.split(".")[0] not in text, (ref, figure)
    words = {w.casefold() for w in re.findall(r"[^\W\d_]+", text)}
    assert not words & NAME_TOKENS.get(ref, set()), (ref, words & NAME_TOKENS[ref])


# ── purposes ─────────────────────────────────────────────────────────────────────────────────


def test_every_purpose_is_specific_to_its_record() -> None:
    purposes = [purpose(m) for m in DETECTION.mismatches]
    assert len(set(purposes)) == len(purposes) == 18
    for m, p in zip(DETECTION.mismatches, purposes):
        assert m.employee_ref in p
        assert len(p) >= 24


def test_no_purpose_carries_a_pay_figure_name_or_email() -> None:
    for m in DETECTION.mismatches:
        assert_no_personal_data(purpose(m), m.employee_ref)


def test_purposes_name_the_change_without_hardcoded_strings() -> None:
    by = {(m.type, m.employee_ref): purpose(m) for m in DETECTION.mismatches}
    assert "2026-10-13" in by[("M1", "W10026")]
    assert "W10055" in by[("M4", "W10191")] and "W10007" in by[("M4", "W10191")]
    assert "CC-420" in by[("M3", "W10015")] and "2026-10-01" in by[("M3", "W10015")]
    assert "2026-10-05" in by[("M5", "W10199")]
    assert "2026-10-01" in by[("M2", "W10148")]


def m8(field: str, workday: str, adp: str) -> Mismatch:
    base = DETECTION.mismatches[0]
    return Mismatch("f" * 64, "M8", "W10042", field, workday, adp, base.workday_source,
                    base.adp_source, None, None)


def test_catch_all_purposes_show_codes_but_never_an_email() -> None:
    assert "TOR" in purpose(m8("location", "TOR", "CGY"))
    email = purpose(m8("work_email", "a.b@northpine.example", "c.d@northpine.example"))
    assert "@" not in email and "work email" in email


def test_an_unknown_type_is_held_as_identity_and_described_without_values() -> None:
    m = replace(m8("favourite_colour", "secret-a", "secret-b"), type="M9")
    body = request(m)
    assert body["change_class"] == "identity"
    assert body["approval_from"] == load_approvers()["identity"].name
    assert "secret" not in body["purpose"]


# ── the whole request ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("m", DETECTION.mismatches, ids=lambda m: f"{m.type}-{m.employee_ref}")
def test_the_request_comes_from_code_and_config(m: Mismatch) -> None:
    body = request(m)
    change_class = classify.change_class(m.type, m.field)
    assert body["policy_id"] == POLICY_ID
    assert body["actor"] == ACTOR == "drift-reconciler@v0.1.0"
    assert (body["principal"], body["system"], body["object"], body["field"]) == (
        PRINCIPAL, "adp", m.employee_ref, m.field)
    assert body["change_class"] == change_class
    assert body["action"] == {"M5": "create", "M6": "deactivate"}.get(m.type, "update")
    assert len(body["sources"]) == 2 and all(s.strip() for s in body["sources"])
    if change_class == "routine":
        assert "approval_from" not in body
    else:
        assert body["approval_from"] == load_approvers()[change_class].name


@pytest.mark.parametrize("m", DETECTION.mismatches, ids=lambda m: f"{m.type}-{m.employee_ref}")
def test_nothing_in_the_request_could_identify_pay_or_a_person(m: Mismatch) -> None:
    blob = json.dumps(request(m))
    assert_no_personal_data(blob, m.employee_ref)
    assert m.fingerprint not in blob  # a hash of the values — brute-forceable for pay


def test_a_record_missing_from_one_system_still_cites_both() -> None:
    for m in DETECTION.mismatches:
        if m.type in ("M5", "M6"):
            wd, adp = sources(m)
            missing = adp if m.type == "M5" else wd
            assert "no " in missing and m.employee_ref in missing


# ── approvers ────────────────────────────────────────────────────────────────────────────────


def test_every_held_class_has_a_named_approver_and_routine_has_none() -> None:
    approvers = load_approvers()
    assert set(approvers) == set(classify.HELD)
    assert approver("routine") is None
    for c in classify.HELD:
        assert len(approver(c).name.split()) >= 2 and approver(c).role
    assert approver("not-a-class") == approvers["identity"]  # unknown → strictest, still held


def test_the_shipped_principal_is_not_an_approver() -> None:
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    principal = re.search(r"^DRIFT_PRINCIPAL=(.*)$", env, re.M).group(1)
    check_principal(principal)


@pytest.mark.parametrize("principal", ["Priya Raman", "  priya   RAMAN ", "GRACE ADEYEMI"])
def test_a_principal_who_is_also_an_approver_is_refused(principal: str) -> None:
    with pytest.raises(ConfigError, match="second person"):
        check_principal(principal)


def write_config(tmp_path: Path, approvers: str) -> Path:
    path = tmp_path / "change_classes.yaml"
    path.write_text(approvers, encoding="utf-8")
    return path


GOOD = {"compensation": "{name: Priya Raman, role: Payroll Manager}",
        "employment_status": "{name: Owen Castellanos, role: Payroll Operations Lead}",
        "identity": "{name: Grace Adeyemi, role: HR Operations Manager}"}


def table(**override: str | None) -> str:
    rows = {**GOOD, **override}
    return "approvers:\n" + "".join(f"  {k}: {v}\n" for k, v in rows.items() if v is not None)


@pytest.mark.parametrize("body, message", [
    ("types: {}\n", "no `approvers` table"),
    (table(identity=None), "no approver for identity"),
    (table(routine="{name: Sam Lee, role: Ops}"), "applied automatically"),
    (table(payroll="{name: Sam Lee, role: Ops}"), "unknown class"),
    (table(compensation="{name: Priya Raman}"), "needs a name and a role"),
    (table(compensation="{name: Payroll, role: Team}"), "named person"),
    (table(compensation="Priya Raman"), "needs a name and a role"),
])
def test_an_approver_table_that_would_weaken_a_rule_is_refused(tmp_path: Path, body: str,
                                                               message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_approvers(write_config(tmp_path, body))


# ── state ────────────────────────────────────────────────────────────────────────────────────


def record(fingerprint: str = "a" * 64, **kw: object) -> Record:
    fields = dict(fingerprint=fingerprint, type="M3", employee_ref="W10015", field="cost_center",
                  change_class="routine", action="update", status="held", first_run="run_1",
                  last_run="run_1") | kw
    return Record(**fields)  # type: ignore[arg-type]


def test_state_round_trips_a_record(tmp_path: Path) -> None:
    with State(tmp_path / "drift.db") as state:
        state.save(record(violations=[{"field": "purpose", "message": "x"}],
                          result={"changed": {"Home_Department": {"from": "410", "to": "420"}}}))
        got = state.get("a" * 64)
        assert got is not None
        assert got.violations == [{"field": "purpose", "message": "x"}]
        assert got.result == {"changed": {"Home_Department": {"from": "410", "to": "420"}}}
        state.save(replace(got, status="applied", last_run="run_2"))
        again = state.get("a" * 64)
        assert (again.status, again.first_run, again.last_run) == ("applied", "run_1", "run_2")


def test_state_refuses_an_unknown_status(tmp_path: Path) -> None:
    with State(tmp_path / "drift.db") as state, pytest.raises(ValueError, match="pending"):
        state.save(record(status="pending"))


def test_settled_and_awaiting_are_real_statuses_and_a_hold_is_never_retried() -> None:
    assert SETTLED <= set(STATUSES) and AWAITING <= set(STATUSES)
    assert {"held", "approved", "applied", "rejected"} <= SETTLED
    assert {"blocked", "expired", "warrant_unreachable"}.isdisjoint(SETTLED)


def test_find_by_employee_ref_or_fingerprint_prefix(tmp_path: Path) -> None:
    with State(tmp_path / "drift.db") as state:
        state.save(record("abc123" + "0" * 58, employee_ref="W10001", status="held"))
        state.save(record("abc999" + "0" * 58, employee_ref="W10002", status="applied"))
        state.save(record("def456" + "0" * 58, employee_ref="W10002", status="held",
                          field="manager"))
        state.save(record("aaa111" + "0" * 58, employee_ref="W10003", status="applied"))
        state.save(record("bbb222" + "0" * 58, employee_ref="W10003", status="applied",
                          field="manager"))
        assert find(state, "W10001").fingerprint.startswith("abc123")
        assert find(state, "W10002").field == "manager"  # the held one, when one is held
        assert find(state, "ABC999").employee_ref == "W10002"
        with pytest.raises(LookupError, match="matches 2"):
            find(state, "W10003")
        with pytest.raises(LookupError, match="no mismatch"):
            find(state, "abc")  # shorter than a fingerprint prefix may be
        with pytest.raises(LookupError, match="no mismatch"):
            find(state, "W99999")
