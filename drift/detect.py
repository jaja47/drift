"""Find the real mismatches: normalised pairs in, `Mismatch` rows out.

The rules themselves are in detect.sql; this module only loads the table and runs them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import astuple, dataclass, field
from datetime import date
from pathlib import Path

import duckdb

from drift.connectors import PayrollSource, Reference, WorkerSource
from drift.normalize import (
    PAY_TOLERANCE_CENTS,
    FormatDifference,
    Pair,
    format_differences,
    normalize,
)

SQL_PATH = Path(__file__).with_name("detect.sql")


@dataclass(frozen=True)
class Mismatch:
    fingerprint: str  # sha256(type|employee_ref|field|workday_value|adp_value)
    type: str  # M1–M8
    employee_ref: str  # Workday Employee_ID, or ADP:<Associate_ID>; never a name
    field: str
    workday_value: str | None
    adp_value: str | None
    workday_source: str | None  # e.g. workday_workers.csv#Employee_ID=W10042
    adp_source: str | None
    org_effective_date: date | None  # set on cost-centre and manager mismatches
    comp_effective_date: date | None  # set on pay mismatches


@dataclass(frozen=True)
class Detection:
    pairs: list[Pair]
    mismatches: list[Mismatch]
    ignored: list[FormatDifference] = field(default_factory=list)  # reconciled, not flagged


def queries(path: Path = SQL_PATH) -> dict[str, str]:
    """The `-- name: x` sections of detect.sql, by name."""
    out: dict[str, str] = {}
    name = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if m := re.match(r"--\s*name:\s*(\w+)\s*$", line):
            name = m.group(1)
            out[name] = ""
        elif name:
            out[name] += line + "\n"
    return out


def load(pairs: Iterable[Pair]) -> duckdb.DuckDBPyConnection:
    """An in-memory DuckDB holding the `pairs` table."""
    con = duckdb.connect(":memory:")
    con.execute(queries()["schema"])
    table = con.table("pairs")
    for pair in pairs:
        table.insert(astuple(pair))
    return con


def detect(pairs: Iterable[Pair]) -> list[Mismatch]:
    con = load(pairs)
    try:
        rows = con.execute(queries()["mismatches"],
                           {"pay_tolerance_cents": PAY_TOLERANCE_CENTS}).fetchall()
    finally:
        con.close()
    return [Mismatch(*row) for row in rows]


def detect_sources(workers: WorkerSource, payroll: PayrollSource,
                   reference: Reference) -> Detection:
    """Read, normalise and detect: the whole of detection, for the CLI and the page alike."""
    worker_rows, payroll_rows = workers.list_workers(), payroll.list_payroll()
    pairs = normalize(worker_rows, payroll_rows, reference)
    return Detection(pairs, detect(pairs),
                     format_differences(worker_rows, payroll_rows, reference))
