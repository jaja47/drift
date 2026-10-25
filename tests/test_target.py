"""Step 8: AdpTarget writes Workday's value into a copy of the ADP export — and nothing else.

The strongest check here re-runs detection against the copy after each fix: the mismatch must be
gone, and no new one may appear.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
from datetime import date
from pathlib import Path

import pytest

from drift.cli import detection_from
from drift.connectors import CsvAdpSource, CsvWorkdaySource, Reference, read_reference
from drift.connectors.csv_source import ADP_EXPORT, DEFAULT_DIR, WORKDAY_EXPORT
from drift.detect import Mismatch, detect_sources
from drift.target import AdpTarget, TargetError
from drift.target.adp_target import CREATED_ID_PREFIX

PLANTED = json.loads((DEFAULT_DIR / "planted.json").read_text(encoding="utf-8"))
TODAY = date(2026, 10, 20)
DETECTION = detection_from(DEFAULT_DIR)
BY_REF = {(m.type, m.employee_ref): m for m in DETECTION.mismatches}


def workers(directory: Path = DEFAULT_DIR) -> dict:
    rows = CsvWorkdaySource(directory / WORKDAY_EXPORT).list_workers()
    return {r.values["Employee_ID"].strip(): r for r in rows}


def make_target(tmp_path: Path, directory: Path = DEFAULT_DIR,
                reference: Reference | None = None) -> AdpTarget:
    return AdpTarget(directory / ADP_EXPORT, tmp_path / "state" / "adp_current.csv",
                     workers(directory), reference or read_reference(directory), TODAY)


def rows(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return {r["Associate_ID"]: r for r in csv.DictReader(f)}


def redetect(target: AdpTarget, directory: Path = DEFAULT_DIR,
             reference: Reference | None = None) -> list[Mismatch]:
    return detect_sources(CsvWorkdaySource(directory / WORKDAY_EXPORT),
                          CsvAdpSource(target.current),
                          reference or read_reference(directory)).mismatches


# ── the interlock and the source ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("warrant_id", ["", "   "])
def test_nothing_is_written_without_a_warrant_id(tmp_path: Path, warrant_id: str) -> None:
    target = make_target(tmp_path)
    with pytest.raises(TargetError, match="no warrant id"):
        target.apply(BY_REF[("M3", "W10015")], warrant_id=warrant_id)
    assert not target.current.exists()


def test_the_copy_is_made_by_the_first_write_and_the_source_is_never_touched(
        tmp_path: Path) -> None:
    source = DEFAULT_DIR / ADP_EXPORT
    before = (source.read_bytes(), source.stat().st_mtime_ns)
    target = make_target(tmp_path)
    assert not target.current.exists()
    for m in DETECTION.mismatches:
        target.apply(m, warrant_id="wrt_test")
    assert target.current.exists()
    assert (source.read_bytes(), source.stat().st_mtime_ns) == before


def test_the_target_can_never_be_the_source_or_sit_beside_it(tmp_path: Path) -> None:
    export = DEFAULT_DIR / ADP_EXPORT
    for current in (export, DEFAULT_DIR / "adp_current.csv"):
        with pytest.raises(ValueError, match="overwrite the source"):
            AdpTarget(export, current, workers(), read_reference(), TODAY)


def test_untouched_rows_stay_byte_identical(tmp_path: Path) -> None:
    target = make_target(tmp_path)
    target.apply(BY_REF[("M3", "W10015")], warrant_id="wrt_test")
    old = (DEFAULT_DIR / ADP_EXPORT).read_text(encoding="utf-8").splitlines()
    new = target.current.read_text(encoding="utf-8").splitlines()
    assert len(old) == len(new)
    assert sum(a != b for a, b in zip(old, new)) == 1


# ── each fix resolves its mismatch, and only that one ────────────────────────────────────────


@pytest.mark.parametrize("m", DETECTION.mismatches, ids=lambda m: f"{m.type}-{m.employee_ref}")
def test_each_fix_resolves_its_mismatch_and_changes_nothing_else(tmp_path: Path,
                                                                 m: Mismatch) -> None:
    target = make_target(tmp_path)
    source_rows = rows(DEFAULT_DIR / ADP_EXPORT)
    result = target.apply(m, warrant_id="wrt_test")
    after_rows = rows(target.current)
    touched = result["row"].removeprefix("Associate_ID=")
    assert {k: v for k, v in after_rows.items() if k != touched} == {
        k: v for k, v in source_rows.items() if k != touched}

    reference = read_reference()
    if m.type == "M5":  # a new record joins only once the crosswalk knows it
        reference = Reference({**reference.crosswalk, m.employee_ref: touched},
                              reference.cost_centers, reference.locations)
    before = {x.fingerprint for x in DETECTION.mismatches}
    after = redetect(target, reference=reference)
    new = [x for x in after if x.fingerprint not in before]
    assert m.fingerprint not in {x.fingerprint for x in after}
    if m.type == "M6":  # deactivated, but a payroll record with no worker is still reported
        assert [(x.type, x.employee_ref, x.adp_value) for x in new] == [
            ("M6", m.employee_ref, f"terminated {TODAY}")]
    else:
        assert new == []


def test_the_pay_fix_writes_only_the_column_adp_pays_from(tmp_path: Path) -> None:
    target = make_target(tmp_path)
    hourly = target.apply(BY_REF[("M2", "W10103")], warrant_id="wrt_test")
    salaried = target.apply(BY_REF[("M2", "W10148")], warrant_id="wrt_test")
    assert list(hourly["changed"]) == ["Rate_Amount"]
    assert list(salaried["changed"]) == ["Annual_Salary"]


def test_a_created_record_follows_its_location_and_is_not_created_twice(tmp_path: Path) -> None:
    target = make_target(tmp_path)
    m = BY_REF[("M5", "W10199")]  # Toronto: a Canadian company code and pay frequency
    first = target.apply(m, warrant_id="wrt_test")
    row = rows(target.current)[CREATED_ID_PREFIX + "W10199"]
    numbers = [int(r["File_Number"]) for r in rows(DEFAULT_DIR / ADP_EXPORT).values()]
    assert first["action"] == "create"
    assert row["File_Number"] == str(max(numbers) + 1).zfill(6)
    assert row["Position_ID"] == "NPC" + row["File_Number"]
    assert (row["Location_Code"], row["Pay_Frequency"], row["Position_Status"]) == (
        "TOR", "Semimonthly", "A")
    again = target.apply(m, warrant_id="wrt_test")
    assert again["changed"] == {} and "nothing to change" in again["note"]
    assert len(rows(target.current)) == len(numbers) + 1


def test_a_ghost_record_is_terminated_as_of_the_apply_date(tmp_path: Path) -> None:
    target = make_target(tmp_path)
    target.apply(BY_REF[("M6", "ADP:GWQG9KXERY")], warrant_id="wrt_test")
    row = rows(target.current)["GWQG9KXERY"]
    assert (row["Position_Status"], row["Termination_Date"]) == ("T", TODAY.isoformat())


def test_applying_twice_changes_nothing_the_second_time(tmp_path: Path) -> None:
    target = make_target(tmp_path)
    m = BY_REF[("M1", "W10026")]
    target.apply(m, warrant_id="wrt_test")
    written = target.current.read_bytes()
    again = target.apply(m, warrant_id="wrt_test")
    assert again["changed"] == {}
    assert target.current.read_bytes() == written


# ── what reaches the ledger ──────────────────────────────────────────────────────────────────


def test_the_committed_result_never_carries_pay_names_or_emails(tmp_path: Path) -> None:
    target = make_target(tmp_path)
    tokens = {t.casefold() for p in DETECTION.pairs for n in (p.wd_name, p.adp_name) if n
              for t in n.split()}
    for m in DETECTION.mismatches:
        blob = json.dumps(target.apply(m, warrant_id="wrt_test"))
        assert "@" not in blob, m.type
        assert not re.search(r"\d+\.\d{2}", blob), (m.type, blob)
        words = {w.casefold() for w in re.findall(r"[A-Za-z']+", blob)}
        assert not words & tokens, (m.type, words & tokens)


# ── nothing is guessed ───────────────────────────────────────────────────────────────────────


def test_an_unmapped_cost_centre_is_an_error_and_nothing_is_written(tmp_path: Path) -> None:
    ref = read_reference()
    centres = {k: v for k, v in ref.cost_centers.items() if not k.startswith("CC-420")}
    target = make_target(tmp_path, reference=Reference(ref.crosswalk, centres, ref.locations))
    with pytest.raises(TargetError, match="not in the cost centre map"):
        target.apply(BY_REF[("M3", "W10015")], warrant_id="wrt_test")
    assert not target.current.exists()


def test_a_manager_missing_from_the_crosswalk_is_an_error(tmp_path: Path) -> None:
    ref = read_reference()
    crosswalk = {k: v for k, v in ref.crosswalk.items() if k != "W10055"}
    target = make_target(tmp_path, reference=Reference(crosswalk, ref.cost_centers,
                                                       ref.locations))
    with pytest.raises(TargetError, match="manager W10055 has no entry"):
        target.apply(BY_REF[("M4", "W10191")], warrant_id="wrt_test")


def test_an_unknown_type_has_no_automatic_fix(tmp_path: Path) -> None:
    m = BY_REF[("M3", "W10015")]
    unknown = Mismatch(m.fingerprint, "M9", m.employee_ref, "favourite_colour", "x", "y",
                       m.workday_source, m.adp_source, None, None)
    with pytest.raises(TargetError, match="no automatic fix"):
        make_target(tmp_path).apply(unknown, warrant_id="wrt_test")


# ── the catch-all (M8), on edited copies of the exports ──────────────────────────────────────


def clean_worker(rate_type: str = "Salary") -> str:
    """A worker with no planted mismatch or decoy, active in both systems."""
    planted = {e["employee_ref"] for e in PLANTED["mismatches"] + PLANTED["decoys"]}
    for ref, row in workers().items():
        v = row.values
        if (ref not in planted and v["Worker_Status"] == "Active"
                and v["Pay_Rate_Type"] == rate_type and v["Manager_Employee_ID"]):
            return ref
    raise AssertionError("no clean worker")


def edited_exports(tmp_path: Path, ref: str, workday: dict[str, str],
                   adp: dict[str, str] | None = None) -> Path:
    directory = tmp_path / "source"
    shutil.copytree(DEFAULT_DIR, directory)
    associate = read_reference().crosswalk[ref]
    for name, key, ident, changes in ((WORKDAY_EXPORT, "Employee_ID", ref, workday),
                                      (ADP_EXPORT, "Associate_ID", associate, adp or {})):
        path = directory / name
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            header, data = list(reader.fieldnames or []), list(reader)
        for row in data:
            if row[key] == ident:
                row.update(changes)
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=header, lineterminator="\n")
            writer.writeheader()
            writer.writerows(data)
    return directory


@pytest.mark.parametrize("field, workday, adp", [
    ("location", {"Location": "Calgary, AB"}, None),
    ("work_email", {"Work_Email": "someone.else@northpine.example"}, None),
    ("worker_status", {"Worker_Status": "Leave"}, None),
    ("rate_type", {"Pay_Rate_Type": "Hourly", "Annual_Base_Pay": "", "Hourly_Rate": "50.00"},
     {"Annual_Salary": "104000.00"}),
])
def test_each_catch_all_fix_resolves_its_mismatch(tmp_path: Path, field: str, workday: dict,
                                                  adp: dict | None) -> None:
    ref = clean_worker()
    if field == "location" and workers()[ref].values["Location"] == "Calgary, AB":
        workday = {"Location": "Toronto, ON"}
    directory = edited_exports(tmp_path, ref, workday, adp)
    found = [m for m in detection_from(directory).mismatches if m.employee_ref == ref]
    assert [(m.type, m.field) for m in found] == [("M8", field)]
    target = make_target(tmp_path, directory)
    target.apply(found[0], warrant_id="wrt_test")
    assert [m for m in redetect(target, directory) if m.employee_ref == ref] == []
