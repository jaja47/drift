"""CSV exports in `data/source/`: the synthetic stand-ins for a Workday report and an ADP extract.

Files are opened for reading only. Values come back exactly as written — a trailing space or an
odd capitalisation is the normaliser's business, not the reader's.
"""

from __future__ import annotations

import csv
from pathlib import Path
from types import MappingProxyType

from drift.connectors.base import (
    ADP_COLUMNS,
    WORKDAY_COLUMNS,
    Reference,
    SourceDataError,
    SourceRow,
)

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "data" / "source"
WORKDAY_EXPORT = "workday_workers.csv"
ADP_EXPORT = "adp_payroll.csv"


def read_export(path: Path, required: tuple[str, ...], key: str) -> list[SourceRow]:
    """Read a CSV export. `utf-8-sig` accepts the byte-order mark spreadsheet tools often add."""
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = [c for c in required if c not in (reader.fieldnames or [])]
        if missing:
            raise SourceDataError(f"{path.name}: missing column(s) {', '.join(missing)}")
        rows = []
        for raw in reader:
            if None in raw or None in raw.values():
                raise SourceDataError(
                    f"{path.name} line {reader.line_num}: wrong number of fields")
            ident = raw[key].strip()
            if not ident:
                raise SourceDataError(f"{path.name} line {reader.line_num}: {key} is empty")
            rows.append(SourceRow(f"{path.name}#{key}={ident}", MappingProxyType(dict(raw))))
    return rows


def read_mapping(path: Path, key: str, value: str) -> dict[str, str]:
    """A two-column lookup. A key listed twice is ambiguous, so it is an error, not last-wins."""
    out: dict[str, str] = {}
    for row in read_export(path, (key, value), key):
        k, v = row.values[key].strip(), row.values[value].strip()
        if k in out:
            raise SourceDataError(f"{path.name}: {key} {k!r} is listed more than once")
        out[k] = v
    return out


class CsvWorkdaySource:
    """A Workday worker export saved as CSV (in production: a RaaS custom report)."""

    def __init__(self, path: Path = DEFAULT_DIR / WORKDAY_EXPORT) -> None:
        self.path = path

    def list_workers(self) -> list[SourceRow]:
        return read_export(self.path, WORKDAY_COLUMNS, "Employee_ID")


class CsvAdpSource:
    """An ADP payroll export saved as CSV (in production: ADP's Workers API)."""

    def __init__(self, path: Path = DEFAULT_DIR / ADP_EXPORT) -> None:
        self.path = path

    def list_payroll(self) -> list[SourceRow]:
        return read_export(self.path, ADP_COLUMNS, "Associate_ID")


def read_reference(directory: Path = DEFAULT_DIR) -> Reference:
    return Reference(
        crosswalk=read_mapping(directory / "id_crosswalk.csv", "Employee_ID", "Associate_ID"),
        cost_centers=read_mapping(
            directory / "cost_center_map.csv", "Workday_Cost_Center", "ADP_Home_Department"),
        locations=read_mapping(
            directory / "location_map.csv", "Workday_Location", "ADP_Location_Code"),
    )
