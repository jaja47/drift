"""Step 5: change_class comes from code and config, sensitive changes are never routine, and
anything unknown is classed identity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from drift.classify import (
    CLASSES,
    CONFIG,
    NEVER_ROUTINE,
    ConfigError,
    change_class,
    load_rules,
)
from drift.cli import detection_from
from drift.connectors.csv_source import DEFAULT_DIR

PLANTED = json.loads((DEFAULT_DIR / "planted.json").read_text(encoding="utf-8"))
SENSITIVE_TYPES = {"M1": "status", "M2": "compensation", "M5": "missing record",
                   "M6": "missing record", "M7": "name"}


def test_every_planted_mismatch_gets_its_expected_class() -> None:
    for e in PLANTED["mismatches"]:
        assert change_class(e["type"], e["field"]) == e["change_class"], e["id"]


def test_no_compensation_status_missing_record_or_name_mismatch_is_routine() -> None:
    detected = detection_from(DEFAULT_DIR).mismatches
    sensitive = [m for m in detected if m.type in SENSITIVE_TYPES or m.field in NEVER_ROUTINE]
    assert len(sensitive) == 10  # 3 M1 + 3 M2 + 2 M5 + 1 M6 + 1 M7
    for m in sensitive:
        assert change_class(m.type, m.field) != "routine", (m.type, m.employee_ref)


def test_only_cost_centre_and_manager_are_routine_in_the_shipped_config() -> None:
    rules = load_rules()
    assert {r.field for r in rules.values() if r.change_class == "routine"} == {
        "cost_center", "manager"}
    for type_ in SENSITIVE_TYPES:
        assert rules[type_].change_class != "routine"


@pytest.mark.parametrize("type_, field", [
    ("M9", "cost_center"),  # unknown type, even with a routine-looking field
    ("M3", "pay"),  # known routine type carrying a different field
    ("M4", "location"),
    ("", ""),
])
def test_anything_unrecognised_is_classed_identity(type_: str, field: str) -> None:
    assert change_class(type_, field) == "identity"


def write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "change_classes.yaml"
    path.write_text(body, encoding="utf-8")
    return path


@pytest.mark.parametrize("body, message", [
    ("types:\n  M2: {field: pay, class: routine, label: x}\n", "pay routine"),
    ("types:\n  M1: {field: worker_status, class: routine, label: x}\n", "worker_status routine"),
    ("types:\n  M5: {field: record, class: routine, label: x}\n", "record routine"),
    ("types:\n  M7: {field: legal_name, class: routine, label: x}\n", "legal_name routine"),
    ("types:\n  M3: {field: cost_center, class: Routine, label: x}\n", "unknown class"),
    ("types:\n  M3: {field: cost_center, label: x}\n", "needs field, class and label"),
    ("{}\n", "no `types` table"),
])
def test_a_config_that_would_weaken_a_rule_is_refused(tmp_path: Path, body: str,
                                                      message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        load_rules(write_config(tmp_path, body))


def test_the_shipped_config_loads_and_uses_only_known_classes() -> None:
    rules = load_rules(CONFIG)
    assert set(rules) == {"M1", "M2", "M3", "M4", "M5", "M6", "M7"}
    assert {r.change_class for r in rules.values()} <= set(CLASSES)
