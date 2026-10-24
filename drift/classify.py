"""Mismatch type + field → change_class, from config/change_classes.yaml.

Deterministic code reading a reviewed table. The LLM is not involved: which corrections need a
human is decided here, and explanations (explain.py) cannot change it.

Two rules hold whatever the file says:
- anything not listed — an unknown type, or a known type with a different field — is `identity`,
  the strictest class, never `routine`;
- a pay, status, record or name field can never be `routine`; a file that says so is refused.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parent.parent / "config" / "change_classes.yaml"

CLASSES = ("routine", "compensation", "employment_status", "identity")
STRICTEST = "identity"
NEVER_ROUTINE = frozenset({"pay", "worker_status", "record", "legal_name"})


class ConfigError(ValueError):
    """change_classes.yaml is malformed or would weaken a rule. Drift does not run on it."""


@dataclass(frozen=True)
class TypeRule:
    field: str
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
        field, change_class = str(spec["field"]), str(spec["class"])
        if change_class not in CLASSES:
            raise ConfigError(f"{path.name}: {type_} has unknown class {change_class!r}")
        if change_class == "routine" and field in NEVER_ROUTINE:
            raise ConfigError(f"{path.name}: {type_} would make {field} routine — never allowed")
        rules[str(type_)] = TypeRule(field, change_class, str(spec["label"]))
    return rules


@cache
def _rules() -> Mapping[str, TypeRule]:
    return load_rules()


def change_class(type_: str, field: str, rules: Mapping[str, TypeRule] | None = None) -> str:
    rule = (rules if rules is not None else _rules()).get(type_)
    if rule is None or rule.field != field:
        return STRICTEST
    return rule.change_class


def label(type_: str, rules: Mapping[str, TypeRule] | None = None) -> str:
    rule = (rules if rules is not None else _rules()).get(type_)
    return rule.label if rule else f"Unrecognised mismatch type {type_}"
