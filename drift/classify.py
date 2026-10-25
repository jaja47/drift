"""Mismatch type + field → change_class, and change_class → approver, from
config/change_classes.yaml.

Deterministic code reading a reviewed table. The LLM is not involved: which corrections need a
human, and which human, is decided here, and explanations (explain.py) cannot change it.

Rules that hold whatever the file says:
- anything not listed — an unknown type, or a known type with a field it does not list — is
  `identity`, the strictest class, never `routine`;
- a pay, status, record or name field can never be `routine`; a file that says so is refused;
- every held class names one approver, and no approver may be the run's principal.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parent.parent / "config" / "change_classes.yaml"

CLASSES = ("routine", "compensation", "employment_status", "identity")
HELD = ("compensation", "employment_status", "identity")  # need a named human's decision
STRICTEST = "identity"
NEVER_ROUTINE = frozenset({"pay", "worker_status", "record", "legal_name"})


class ConfigError(ValueError):
    """change_classes.yaml is malformed or would weaken a rule. Drift does not run on it."""


@dataclass(frozen=True)
class TypeRule:
    fields: frozenset[str]  # the field(s) this type may carry
    change_class: str
    label: str


def load_rules(path: Path = CONFIG) -> Mapping[str, TypeRule]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    types = data.get("types")
    if not isinstance(types, dict) or not types:
        raise ConfigError(f"{path.name}: no `types` table")
    rules: dict[str, TypeRule] = {}
    for type_, spec in types.items():
        if not isinstance(spec, dict) or not {"field", "class", "label"} <= spec.keys():
            raise ConfigError(f"{path.name}: {type_} needs field, class and label")
        listed = spec["field"] if isinstance(spec["field"], list) else [spec["field"]]
        fields = frozenset(str(f) for f in listed)
        change_class = str(spec["class"])
        if not fields:
            raise ConfigError(f"{path.name}: {type_} lists no field")
        if change_class not in CLASSES:
            raise ConfigError(f"{path.name}: {type_} has unknown class {change_class!r}")
        if change_class == "routine" and fields & NEVER_ROUTINE:
            sensitive = ", ".join(sorted(fields & NEVER_ROUTINE))
            raise ConfigError(
                f"{path.name}: {type_} would make {sensitive} routine — never allowed")
        rules[str(type_)] = TypeRule(fields, change_class, str(spec["label"]))
    return rules


@cache
def _rules() -> Mapping[str, TypeRule]:
    return load_rules()


def change_class(type_: str, field: str, rules: Mapping[str, TypeRule] | None = None) -> str:
    rule = (rules if rules is not None else _rules()).get(type_)
    if rule is None or field not in rule.fields:
        return STRICTEST
    return rule.change_class


def label(type_: str, rules: Mapping[str, TypeRule] | None = None) -> str:
    rule = (rules if rules is not None else _rules()).get(type_)
    return rule.label if rule else f"Unrecognised mismatch type {type_}"


# ── approvers ────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Approver:
    name: str  # what Warrant is told, and what the approver types to decide
    role: str

    def __str__(self) -> str:
        return f"{self.name} ({self.role})"


def same_person(a: str, b: str) -> bool:
    """Case- and whitespace-insensitive, as Warrant compares names."""
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def load_approvers(path: Path = CONFIG) -> Mapping[str, Approver]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    table = data.get("approvers")
    if not isinstance(table, dict):
        raise ConfigError(f"{path.name}: no `approvers` table")
    approvers: dict[str, Approver] = {}
    for change_class, spec in table.items():
        if change_class == "routine":
            raise ConfigError(f"{path.name}: routine fixes are applied automatically and have no "
                              f"approver")
        if change_class not in HELD:
            raise ConfigError(f"{path.name}: approver for unknown class {change_class!r}")
        if not isinstance(spec, dict) or not {"name", "role"} <= spec.keys():
            raise ConfigError(f"{path.name}: the {change_class} approver needs a name and a role")
        name, role = " ".join(str(spec["name"]).split()), str(spec["role"]).strip()
        if len(name.split()) < 2 or not role:
            raise ConfigError(f"{path.name}: the {change_class} approver must be a named person "
                              f"(first and last name) with a role")
        approvers[change_class] = Approver(name, role)
    missing = [c for c in HELD if c not in approvers]
    if missing:
        raise ConfigError(f"{path.name}: no approver for {', '.join(missing)}")
    return approvers


@cache
def _approvers() -> Mapping[str, Approver]:
    return load_approvers()


def approver(change_class: str,
             approvers: Mapping[str, Approver] | None = None) -> Approver | None:
    """Who decides a held correction; None for routine. An unknown class gets the strictest
    class's approver, never None — unknown is held, not applied."""
    if change_class == "routine":
        return None
    table = approvers if approvers is not None else _approvers()
    return table.get(change_class) or table[STRICTEST]


def check_principal(principal: str,
                    approvers: Mapping[str, Approver] | None = None) -> None:
    """Refuse a run whose principal is also an approver. Warrant would accept the request but
    nobody could ever decide it (the principal cannot approve their own run), so it would sit
    held until it lapsed. Better to stop before asking."""
    for change_class, a in (approvers if approvers is not None else _approvers()).items():
        if same_person(a.name, principal):
            raise ConfigError(f"{a.name} is both the run's principal and the {change_class} "
                              f"approver — approval needs a second person")
