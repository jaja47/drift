"""Step 4: the CSV connectors meet the source contract, return rows untouched, and never write."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from drift.connectors import (
    ADP_COLUMNS,
    WORKDAY_COLUMNS,
    CsvAdpSource,
    CsvWorkdaySource,
    PayrollSource,
    Reference,
    SourceDataError,
    WorkerSource,
    read_reference,
)
from drift.connectors.csv_source import DEFAULT_DIR, read_export

PLANTED = json.loads((DEFAULT_DIR / "planted.json").read_text(encoding="utf-8"))


def write_csv(path: Path, header: list[str], *rows: list[str], bom: bool = False) -> Path:
    with path.open("w", newline="", encoding="utf-8-sig" if bom else "utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
    return path


def test_csv_sources_meet_the_contract() -> None:
    assert isinstance(CsvWorkdaySource(), WorkerSource)
    assert isinstance(CsvAdpSource(), PayrollSource)


def test_reads_the_committed_exports() -> None:
    workers = CsvWorkdaySource().list_workers()
    payroll = CsvAdpSource().list_payroll()
    assert len(workers) == 200 and len(payroll) == 199
    assert workers[0].source_ref == "workday_workers.csv#Employee_ID=W10001"
    assert payroll[0].source_ref.startswith("adp_payroll.csv#Associate_ID=G")
    assert set(WORKDAY_COLUMNS) <= set(workers[0].values)
    assert set(ADP_COLUMNS) <= set(payroll[0].values)

    ref = read_reference()
    assert len(ref.crosswalk) == 198
    assert ref.cost_centers["CC-410 Supply Chain Analytics"] == "410"
    assert ref.locations["Toronto, ON"] == "TOR"


def test_values_come_back_exactly_as_exported() -> None:
    """Trimming and case are normalisation's job. The reader must not quietly do them, or the
    whitespace decoy would stop testing anything."""
    (d1_space,) = [d for d in PLANTED["decoys"] if d["id"] == "D1-2"]
    payroll = {r.values["Associate_ID"]: r for r in CsvAdpSource().list_payroll()}
    raw = payroll[d1_space["associate_id"]].values["Work_Email"]
    assert raw == d1_space["adp"]["Work_Email"] and raw.endswith(" ")


def test_reading_never_modifies_a_source(tmp_path: Path) -> None:
    files = sorted(DEFAULT_DIR.glob("*.csv"))
    before = {f: (f.read_bytes(), f.stat().st_mtime_ns) for f in files}
    CsvWorkdaySource().list_workers()
    CsvAdpSource().list_payroll()
    read_reference()
    assert {f: (f.read_bytes(), f.stat().st_mtime_ns) for f in files} == before


def test_a_byte_order_mark_is_accepted(tmp_path: Path) -> None:
    path = write_csv(tmp_path / "x.csv", ["Employee_ID", "Name"], ["W1", "A"], bom=True)
    (row,) = read_export(path, ("Employee_ID",), "Employee_ID")
    assert row.source_ref == "x.csv#Employee_ID=W1"


@pytest.mark.parametrize("header, rows, message", [
    (["Employee_ID"], [["W1"]], "missing column"),
    (["Employee_ID", "Name"], [["W1", "A", "extra"]], "wrong number of fields"),
    (["Employee_ID", "Name"], [["W1"]], "wrong number of fields"),
    (["Employee_ID", "Name"], [["  ", "A"]], "Employee_ID is empty"),
])
def test_malformed_exports_are_refused(tmp_path: Path, header, rows, message) -> None:
    path = write_csv(tmp_path / "x.csv", header, *rows)
    with pytest.raises(SourceDataError, match=message):
        read_export(path, ("Employee_ID", "Name"), "Employee_ID")


def test_a_mapping_key_listed_twice_is_refused(tmp_path: Path) -> None:
    write_csv(tmp_path / "id_crosswalk.csv", ["Employee_ID", "Associate_ID"],
              ["W1", "G1"], ["W1", "G2"])
    write_csv(tmp_path / "cost_center_map.csv", ["Workday_Cost_Center", "ADP_Home_Department"])
    write_csv(tmp_path / "location_map.csv", ["Workday_Location", "ADP_Location_Code"])
    with pytest.raises(SourceDataError, match="W1.*more than once"):
        read_reference(tmp_path)


def test_a_crosswalk_that_maps_two_ways_is_refused() -> None:
    with pytest.raises(SourceDataError, match="G1 to both W1 and W2"):
        Reference(crosswalk={"W1": "G1", "W2": "G1"}, cost_centers={}, locations={})
