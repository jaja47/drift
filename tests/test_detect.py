"""Step 5: detection scored against planted.json, plus each detect.sql rule on hand-built pairs."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import fields, replace
from datetime import date

import pytest

from drift.cli import detection_from
from drift.connectors.csv_source import DEFAULT_DIR
from drift.detect import Detection, Mismatch, detect, load, queries
from drift.normalize import PAIR_COLUMNS, PAY_TOLERANCE_CENTS, Pair

PLANTED = json.loads((DEFAULT_DIR / "planted.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def detection() -> Detection:
    return detection_from(DEFAULT_DIR)


# ── scored against the ground truth ──────────────────────────────────────────────────────────


def test_detection_finds_18_of_18_flags_no_decoy_and_nothing_extra(detection: Detection) -> None:
    found = {(m.type, m.employee_ref, m.field) for m in detection.mismatches}
    planted = {(e["type"], e["employee_ref"], e["field"]) for e in PLANTED["mismatches"]}
    decoys = {e["employee_ref"] for e in PLANTED["decoys"]}

    assert planted - found == set(), "missed"
    assert found - planted == set(), "extra"
    assert {m.employee_ref for m in detection.mismatches} & decoys == set(), "decoy flagged"
    assert len(detection.mismatches) == 18


def test_fingerprint_is_the_documented_hash_and_stable(detection: Detection) -> None:
    for m in detection.mismatches:
        parts = [m.type, m.employee_ref, m.field, m.workday_value or "", m.adp_value or ""]
        assert m.fingerprint == hashlib.sha256("|".join(parts).encode()).hexdigest()
    assert len({m.fingerprint for m in detection.mismatches}) == 18
    again = detection_from(DEFAULT_DIR)
    assert [m.fingerprint for m in again.mismatches] == [
        m.fingerprint for m in detection.mismatches]


def test_employee_ref_is_an_id_never_a_name(detection: Detection) -> None:
    names = {p.wd_name for p in detection.pairs} | {p.adp_name for p in detection.pairs}
    for m in detection.mismatches:
        assert re.fullmatch(r"W\d{5}|ADP:G[A-Z0-9]{9}", m.employee_ref), m.employee_ref
        assert m.employee_ref not in names


def test_sources_point_back_to_both_rows(detection: Detection) -> None:
    for m in detection.mismatches:
        if m.type == "M6":
            assert m.workday_source is None
        else:
            assert m.workday_source == f"workday_workers.csv#Employee_ID={m.employee_ref}"
        if m.type == "M5":
            assert m.adp_source is None
        else:
            assert re.fullmatch(r"adp_payroll\.csv#Associate_ID=G[A-Z0-9]{9}", m.adp_source)


def test_effective_dates_are_set_only_where_they_explain_the_mismatch(
        detection: Detection) -> None:
    for m in detection.mismatches:
        assert (m.org_effective_date is not None) == (m.type in ("M3", "M4")), m
        assert (m.comp_effective_date is not None) == (m.type == "M2"), m
    reorg = date.fromisoformat(PLANTED["reorg_effective_date"])
    on_reorg = [m.type for m in detection.mismatches if m.org_effective_date == reorg]
    assert sorted(on_reorg) == ["M3"] * 5 + ["M4"] * 2


def test_values_read_plainly(detection: Detection) -> None:
    by = {(m.type, m.employee_ref): m for m in detection.mismatches}
    (m3,) = [e for e in PLANTED["mismatches"] if e["id"] == "M3-1"]
    m = by[("M3", m3["employee_ref"])]
    assert (m.workday_value, m.adp_value) == ("CC-420 Fulfillment Network Engineering", "410")
    pay = [m for m in detection.mismatches if m.type == "M2"]
    assert all(re.fullmatch(r"\d+\.\d\d/yr", v) for m in pay for v in (m.workday_value,
                                                                       m.adp_value))


# ── the table detect.sql reads ───────────────────────────────────────────────────────────────


def test_sql_schema_mirrors_the_pair_dataclass() -> None:
    con = load([])
    described = con.execute("DESCRIBE pairs").fetchall()
    assert [row[0] for row in described] == list(PAIR_COLUMNS)
    for f, row in zip(fields(Pair), described):
        annotation = str(f.type)  # e.g. "date | None"; `match` is the Match literal, a string
        sql_type = ("DATE" if "date" in annotation else
                    "BIGINT" if "int" in annotation else "VARCHAR")
        assert row[1] == sql_type, (f.name, row[1])


def test_detect_sql_has_its_named_sections() -> None:
    assert {"schema", "mismatches"} <= queries().keys()


# ── each rule, on hand-built pairs ───────────────────────────────────────────────────────────

SAME = Pair(
    employee_ref="W1", match="both", workday_source="wd#W1", adp_source="adp#G1",
    associate_id="G1", hire_date=date(2020, 1, 6), org_effective_date=date(2024, 1, 1),
    comp_effective_date=date(2025, 4, 1),
    wd_status="active", adp_status="active",
    wd_termination_date=None, adp_termination_date=None,
    wd_name="Ada Lovelace", adp_name="Ada Lovelace",
    wd_name_key="ada lovelace", adp_name_key="ada lovelace",
    wd_email="ada@x.example", adp_email="ada@x.example",
    wd_cost_center="CC-410 Analytics", wd_department="410", adp_department="410",
    wd_location="TOR", adp_location="TOR", wd_manager="W2", adp_manager="W2",
    wd_rate_type="Salary", adp_rate_type="Salary",
    wd_annual_pay_cents=9_000_000, adp_annual_pay_cents=9_000_000,
)
ADP_SIDE = [f for f in PAIR_COLUMNS if f.startswith("adp_") and f != "adp_source"]
WORKDAY_ONLY = replace(SAME, match="workday_only", adp_source=None, associate_id=None,
                       **dict.fromkeys(ADP_SIDE))
ADP_ONLY = replace(SAME, employee_ref="ADP:G1", match="adp_only", workday_source=None,
                   hire_date=None, org_effective_date=None, comp_effective_date=None,
                   **dict.fromkeys(f for f in PAIR_COLUMNS if f.startswith("wd_")))


def found(*pairs: Pair) -> list[tuple[str, str]]:
    return [(m.type, m.field) for m in detect(list(pairs))]


@pytest.mark.parametrize("pair, expected", [
    (SAME, []),
    # M1
    (replace(SAME, wd_status="terminated", wd_termination_date=date(2026, 9, 18)),
     [("M1", "worker_status")]),
    (replace(SAME, wd_status="terminated", adp_status="leave"), [("M1", "worker_status")]),
    (replace(SAME, wd_status="terminated", adp_status="unrecognised:D"), [("M1", "worker_status")]),
    (replace(SAME, wd_status="terminated", adp_status="terminated"), []),
    # M2 — the SQL must apply the same ±$1 as normalize.pay_matches
    (replace(SAME, adp_annual_pay_cents=9_000_000 + PAY_TOLERANCE_CENTS), []),
    (replace(SAME, adp_annual_pay_cents=9_000_000 + PAY_TOLERANCE_CENTS + 1), [("M2", "pay")]),
    (replace(SAME, adp_annual_pay_cents=9_000_000 - PAY_TOLERANCE_CENTS - 1), [("M2", "pay")]),
    (replace(SAME, adp_annual_pay_cents=None), [("M2", "pay")]),
    (replace(SAME, wd_annual_pay_cents=None, adp_annual_pay_cents=None), []),
    # M3 — including a cost centre the map does not know
    (replace(SAME, adp_department="420"), [("M3", "cost_center")]),
    (replace(SAME, wd_department="unmapped:CC-999 Nowhere"), [("M3", "cost_center")]),
    # M4
    (replace(SAME, adp_manager="W3"), [("M4", "manager")]),
    (replace(SAME, adp_manager="ADP:G9"), [("M4", "manager")]),
    (replace(SAME, adp_manager=None), [("M4", "manager")]),
    # M5 — a terminated worker with no payroll record is not a gap
    (WORKDAY_ONLY, [("M5", "record")]),
    (replace(WORKDAY_ONLY, wd_status="terminated"), []),
    # M6 — any ADP record nobody in Workday owns, whatever its status
    (ADP_ONLY, [("M6", "record")]),
    (replace(ADP_ONLY, adp_status="terminated"), [("M6", "record")]),
    # M7
    (replace(SAME, adp_name="Ada Byron", adp_name_key="ada byron"), [("M7", "legal_name")]),
    # one person, two disagreements → two rows
    (replace(SAME, adp_department="420", adp_manager="W3"),
     [("M3", "cost_center"), ("M4", "manager")]),
])
def test_rule(pair: Pair, expected: list[tuple[str, str]]) -> None:
    assert found(pair) == expected


def test_fields_detection_does_not_compare_never_produce_a_row() -> None:
    """Location, email and rate type are normalised but are not mismatch types in PLAN.md."""
    odd = replace(SAME, adp_location="CGY", adp_email="other@x.example", adp_rate_type="Hourly")
    assert found(odd) == []


def test_mismatch_columns_match_build_md() -> None:
    assert [f.name for f in fields(Mismatch)] == [
        "fingerprint", "type", "employee_ref", "field", "workday_value", "adp_value",
        "workday_source", "adp_source", "org_effective_date", "comp_effective_date"]
