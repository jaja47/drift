"""Step 7: the reorg rollup is found, and nothing else in the data forms a pattern."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date

import pytest

from drift.cli import detection_from
from drift.connectors.csv_source import DEFAULT_DIR
from drift.detect import Detection, Mismatch
from drift.patterns import PATTERN_MIN, find_patterns

PLANTED = json.loads((DEFAULT_DIR / "planted.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def detection() -> Detection:
    return detection_from(DEFAULT_DIR)


def test_the_reorg_is_found_once_and_is_the_only_pattern(detection: Detection) -> None:
    (pattern,) = find_patterns(detection.mismatches, detection.pairs)
    reorg = PLANTED["reorg_effective_date"]
    assert (pattern.kind, pattern.key, pattern.size) == ("org_date", reorg, 7)
    assert pattern.diagnosis.startswith(f"7 org changes share the effective date {reorg} "
                                        f"(5 cost centre, 2 manager)")
    assert pattern.suggested_owner == "IT Application Engineering (integration)"

    planted = {(e["type"], e["employee_ref"]) for e in PLANTED["mismatches"]
               if e["workday"] and e["workday"].get("Org_Effective_Date") == reorg}
    by_print = {m.fingerprint: (m.type, m.employee_ref) for m in detection.mismatches}
    assert {by_print[f] for f in pattern.fingerprints} == planted


def test_a_group_already_explained_is_not_reported_again(detection: Detection) -> None:
    """The five M3 rows also share CC-420; that is the reorg again, not a second pattern."""
    kinds = [p.kind for p in find_patterns(detection.mismatches, detection.pairs)]
    assert "cost_center" not in kinds


def m(type_: str, ref: str, n: int, **dates: date) -> Mismatch:
    return Mismatch(f"fp{type_}{ref}{n}", type_, ref, {"M2": "pay", "M3": "cost_center"}.get(
        type_, "manager"), "a", "b", None, None, dates.get("org"), dates.get("comp"))


def test_a_shared_comp_date_forms_a_pattern() -> None:
    rows = [m("M2", f"W{i}", i, comp=date(2026, 4, 1)) for i in range(3)]
    (p,) = find_patterns(rows, [])
    assert (p.kind, p.size) == ("comp_date", 3)
    assert "merit cycle" in p.diagnosis


def test_mismatches_dated_after_the_cutoff_form_a_pattern() -> None:
    rows = [m("M3", f"W{i}", i, org=date(2026, 10, 12 + i)) for i in range(3)]
    patterns = find_patterns(rows, [])
    assert [p.kind for p in patterns] == ["after_cutoff"]
    assert "after the 2026-10-09 payroll cutoff" in patterns[0].diagnosis


def test_fewer_than_the_minimum_is_not_a_pattern() -> None:
    rows = [m("M4", f"W{i}", i, org=date(2026, 5, 1)) for i in range(PATTERN_MIN - 1)]
    assert find_patterns(rows, []) == []


def test_cost_centre_groups_use_the_pairs(detection: Detection) -> None:
    pair = next(p for p in detection.pairs if p.match == "both")
    pairs = [replace(pair, employee_ref=f"W{i}") for i in range(3)]
    rows = [m("M4", f"W{i}", i) for i in range(3)]  # no dates, so only the cost centre is shared
    (p,) = find_patterns(rows, pairs)
    assert (p.kind, p.key) == ("cost_center", pair.wd_cost_center)
