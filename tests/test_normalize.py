"""Step 4: each normalisation rule, the crosswalk join, and the four decoys normalising to equal.

The last section runs the real exports through normalisation and checks both directions: every
decoy becomes equal, and every planted mismatch is still a difference.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from types import MappingProxyType

import pytest

from drift.connectors import (
    CsvAdpSource,
    CsvWorkdaySource,
    Reference,
    SourceDataError,
    SourceRow,
    read_reference,
)
from drift.connectors.csv_source import DEFAULT_DIR
from drift.normalize import (
    ADP_STATUS,
    WORKDAY_STATUS,
    Pair,
    annual_pay_cents,
    email,
    legal_name,
    manager_from_adp,
    mapped,
    money_cents,
    name_key,
    normalize,
    parse_date,
    pay_matches,
    status,
)

# ── the rules, one at a time ─────────────────────────────────────────────────────────────────


def test_status_maps_both_vocabularies_to_one() -> None:
    for wd, adp, canonical in [("Active", "A", "active"), ("Terminated", "T", "terminated"),
                               ("Leave", "L", "leave")]:
        assert status(wd, WORKDAY_STATUS) == status(adp, ADP_STATUS) == canonical
    assert status(" T ", ADP_STATUS) == "terminated"
    assert status("", ADP_STATUS) is None


def test_an_unrecognised_status_is_kept_and_never_equals_a_known_one() -> None:
    odd = status("Retired", WORKDAY_STATUS)
    assert odd == "unrecognised:Retired"
    assert odd not in WORKDAY_STATUS.values()


def test_dates() -> None:
    assert parse_date("2026-10-01") == date(2026, 10, 1)
    assert parse_date("  ") is None
    with pytest.raises(ValueError):
        parse_date("10/01/2026")


def test_money_is_exact_cents() -> None:
    assert money_cents("72000.00") == 7_200_000
    assert money_cents("1,234.50") == 123_450
    assert money_cents("24.987") == 2_499  # half-up to the cent
    assert money_cents("") is None
    with pytest.raises(ValueError):
        money_cents("seventy")


def test_hourly_pay_is_annualised_at_2080_hours() -> None:
    assert annual_pay_cents("Salary", "72000.00", "") == 7_200_000
    assert annual_pay_cents("Hourly", "", "24.98") == 2_498 * 2080
    # ADP sometimes holds an hourly worker's pay annualised: fall back rather than lose it
    assert annual_pay_cents("Hourly", "51958.00", "") == 5_195_800
    assert annual_pay_cents("Salary", "", "") is None


def test_pay_is_equal_within_one_dollar_a_year() -> None:
    assert pay_matches(5_195_840, 5_195_800)  # $0.40 apart
    assert pay_matches(100_000, 100_100)  # exactly $1.00
    assert not pay_matches(100_000, 100_101)
    assert pay_matches(None, None)
    assert not pay_matches(None, 100_000)


def test_email_ignores_case_and_surrounding_whitespace() -> None:
    assert email(" Angela.Cohen@Northpine.example ") == "angela.cohen@northpine.example"
    assert email("a.b@x.example") != email("a.c@x.example")
    assert email("") is None


def test_names_compare_on_legal_name_only() -> None:
    assert legal_name("  Mary  Ann ", "O'Brien ") == "Mary Ann O'Brien"
    assert name_key(legal_name("MARY", "SMITH")) == name_key(legal_name("Mary", "Smith"))
    assert name_key(legal_name("Daniel", "Kennedy")) != name_key(legal_name("Daniel", "Graham"))


def test_codes_are_mapped_and_an_unmapped_value_never_matches() -> None:
    centres = {"CC-410 Supply Chain Analytics": "410"}
    assert mapped(" CC-410 Supply Chain Analytics", centres) == "410"
    assert mapped("CC-999 Nowhere", centres) == "unmapped:CC-999 Nowhere"
    assert mapped("", centres) is None


def test_adp_manager_is_translated_through_the_crosswalk() -> None:
    employee_of = {"G1": "W1"}
    assert manager_from_adp("G1", employee_of) == "W1"
    assert manager_from_adp("G9", employee_of) == "ADP:G9"  # never equals a Workday id
    assert manager_from_adp("", employee_of) is None


# ── the join ─────────────────────────────────────────────────────────────────────────────────

WD_DEFAULTS = {
    "Employee_ID": "W1", "Legal_First_Name": "Ada", "Legal_Last_Name": "Lovelace",
    "Work_Email": "ada.lovelace@x.example", "Worker_Status": "Active", "Hire_Date": "2020-01-06",
    "Termination_Date": "", "Manager_Employee_ID": "W2", "Cost_Center": "CC-410 Analytics",
    "Location": "Toronto, ON", "Pay_Rate_Type": "Salary", "Annual_Base_Pay": "90000.00",
    "Hourly_Rate": "", "Comp_Effective_Date": "2025-04-01", "Org_Effective_Date": "2024-01-01",
}
ADP_DEFAULTS = {
    "Associate_ID": "G1", "First_Name": "Ada", "Last_Name": "Lovelace",
    "Work_Email": "ada.lovelace@x.example", "Position_Status": "A", "Termination_Date": "",
    "Home_Department": "410", "Reports_To_Associate_ID": "G2", "Location_Code": "TOR",
    "Rate_Type": "Salary", "Annual_Salary": "90000.00", "Rate_Amount": "",
}
REF = Reference(crosswalk={"W1": "G1", "W2": "G2"},
                cost_centers={"CC-410 Analytics": "410"}, locations={"Toronto, ON": "TOR"})


def wd(**values: str) -> SourceRow:
    v = WD_DEFAULTS | values
    return SourceRow(f"wd#Employee_ID={v['Employee_ID']}", MappingProxyType(v))


def adp(**values: str) -> SourceRow:
    v = ADP_DEFAULTS | values
    return SourceRow(f"adp#Associate_ID={v['Associate_ID']}", MappingProxyType(v))


def test_a_matched_pair_is_equal_field_by_field() -> None:
    (p,) = normalize([wd()], [adp()], REF)
    assert (p.employee_ref, p.match, p.associate_id) == ("W1", "both", "G1")
    assert (p.workday_source, p.adp_source) == ("wd#Employee_ID=W1", "adp#Associate_ID=G1")
    for a, b in [("wd_status", "adp_status"), ("wd_name_key", "adp_name_key"),
                 ("wd_email", "adp_email"), ("wd_department", "adp_department"),
                 ("wd_location", "adp_location"), ("wd_manager", "adp_manager"),
                 ("wd_termination_date", "adp_termination_date")]:
        assert getattr(p, a) == getattr(p, b), a
    assert pay_matches(p.wd_annual_pay_cents, p.adp_annual_pay_cents)
    assert p.wd_manager == "W2" and p.wd_department == "410"


def test_unjoined_rows_are_kept_and_marked() -> None:
    pairs = normalize([wd(), wd(Employee_ID="W5")], [adp(), adp(Associate_ID="G7")], REF)
    by_ref = {p.employee_ref: p for p in pairs}
    assert by_ref["W5"].match == "workday_only" and by_ref["W5"].adp_status is None
    ghost = by_ref["ADP:G7"]
    assert ghost.match == "adp_only" and ghost.wd_status is None and ghost.associate_id == "G7"
    assert len(pairs) == 3


def test_a_crosswalk_entry_pointing_at_nothing_does_not_invent_a_match() -> None:
    ref = replace(REF, crosswalk={"W1": "G1", "W3": "G3"})
    pairs = normalize([wd(), wd(Employee_ID="W3")], [adp()], ref)
    assert {p.employee_ref: p.match for p in pairs} == {"W1": "both", "W3": "workday_only"}
    pairs = normalize([wd()], [adp(), adp(Associate_ID="G3")], ref)
    assert {p.employee_ref: p.match for p in pairs} == {"W1": "both", "ADP:G3": "adp_only"}


def test_a_duplicate_id_stops_the_run() -> None:
    with pytest.raises(SourceDataError, match="W1 appears more than once"):
        normalize([wd(), wd()], [adp()], REF)
    with pytest.raises(SourceDataError, match="G1 appears more than once"):
        normalize([wd()], [adp(), adp()], REF)


def test_a_malformed_value_names_its_row_and_column() -> None:
    with pytest.raises(SourceDataError, match=r"wd#Employee_ID=W1: Hire_Date"):
        normalize([wd(Hire_Date="06/01/2020")], [adp()], REF)
    with pytest.raises(SourceDataError, match=r"adp#Associate_ID=G1: pay"):
        normalize([wd()], [adp(Annual_Salary="ninety")], REF)


def test_preferred_name_is_not_compared() -> None:
    (p,) = normalize([wd(Preferred_Name="Addie")], [adp()], REF)
    assert p.wd_name_key == p.adp_name_key


# ── the real exports ─────────────────────────────────────────────────────────────────────────

PLANTED = json.loads((DEFAULT_DIR / "planted.json").read_text(encoding="utf-8"))

# Planted `field` → the Pair attributes that hold it on each side.
COMPARED = {
    "worker_status": ("wd_status", "adp_status"),
    "cost_center": ("wd_department", "adp_department"),
    "manager": ("wd_manager", "adp_manager"),
    "legal_name": ("wd_name_key", "adp_name_key"),
    "work_email": ("wd_email", "adp_email"),
    "preferred_name": ("wd_name_key", "adp_name_key"),
}


@pytest.fixture(scope="module")
def pairs() -> dict[str, Pair]:
    rows = normalize(CsvWorkdaySource().list_workers(), CsvAdpSource().list_payroll(),
                     read_reference())
    return {p.employee_ref: p for p in rows}


def same(p: Pair, field: str) -> bool:
    if field == "pay":
        return pay_matches(p.wd_annual_pay_cents, p.adp_annual_pay_cents)
    a, b = COMPARED[field]
    return getattr(p, a) == getattr(p, b)


def test_real_exports_join_as_expected(pairs: dict[str, Pair]) -> None:
    matches = [p.match for p in pairs.values()]
    assert (matches.count("both"), matches.count("workday_only"), matches.count("adp_only")) == (
        198, 2, 1)
    for e in PLANTED["mismatches"]:
        if e["type"] == "M5":
            assert pairs[e["employee_ref"]].match == "workday_only"
        if e["type"] == "M6":
            assert pairs[e["employee_ref"]].match == "adp_only"


@pytest.mark.parametrize("decoy", PLANTED["decoys"], ids=lambda d: d["id"])
def test_every_decoy_normalises_to_equal(pairs: dict[str, Pair], decoy: dict) -> None:
    p = pairs[decoy["employee_ref"]]
    assert p.match == "both"
    assert same(p, decoy["field"]), f"{decoy['id']} still differs after normalisation"
    assert decoy["workday"] != decoy["adp"]  # and it really did differ before


@pytest.mark.parametrize(
    "mismatch", [m for m in PLANTED["mismatches"] if m["type"] not in ("M5", "M6")],
    ids=lambda m: m["id"])
def test_no_planted_mismatch_is_normalised_away(pairs: dict[str, Pair], mismatch: dict) -> None:
    assert not same(pairs[mismatch["employee_ref"]], mismatch["field"])


def test_unmapped_values_never_appear_in_the_real_data(pairs: dict[str, Pair]) -> None:
    for p in pairs.values():
        for value in (p.wd_status, p.adp_status, p.wd_department, p.wd_location, p.adp_manager):
            assert not str(value).startswith(("unrecognised:", "unmapped:", "ADP:")), p
