"""Step 2: the synthetic data holds exactly the planted mismatches and decoys, and nothing else.

`test_no_unplanted_differences` compares the two exports with a small, independent check. It is
deliberately not the detector (Steps 4–5): it exists so that an accidental extra difference is
caught here, in the data, rather than showing up later as a detector failure.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest

from drift.constants import PAYROLL_CUTOFF
from scripts.generate_data import OUTPUT_FILES, generate

ROOT = Path(__file__).resolve().parent.parent
COMMITTED = ROOT / "data" / "source"

EXPECTED = {  # type → (count, change_class); PLAN.md §2
    "M1": (3, "employment_status"), "M2": (3, "compensation"), "M3": (5, "routine"),
    "M4": (3, "routine"), "M5": (2, "employment_status"), "M6": (1, "identity"),
    "M7": (1, "identity"), "D1": (2, None), "D2": (1, None), "D3": (1, None),
}
STATUS = {"Active": "A", "Terminated": "T", "Leave": "L"}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@dataclass
class Data:
    workday: dict[str, dict[str, str]]  # by Employee_ID
    adp: dict[str, dict[str, str]]  # by Associate_ID
    crosswalk: dict[str, str]  # Employee_ID → Associate_ID
    cost_centers: dict[str, str]
    locations: dict[str, str]
    planted: dict

    @property
    def entries(self) -> list[dict]:
        return self.planted["mismatches"] + self.planted["decoys"]

    def refs(self, *types: str) -> set[str]:
        return {e["employee_ref"] for e in self.entries if e["type"] in types}


def load(directory: Path) -> Data:
    workday = read_csv(directory / "workday_workers.csv")
    adp = read_csv(directory / "adp_payroll.csv")
    crosswalk = read_csv(directory / "id_crosswalk.csv")
    return Data(
        workday={r["Employee_ID"]: r for r in workday},
        adp={r["Associate_ID"]: r for r in adp},
        crosswalk={r["Employee_ID"]: r["Associate_ID"] for r in crosswalk},
        cost_centers={r["Workday_Cost_Center"]: r["ADP_Home_Department"]
                      for r in read_csv(directory / "cost_center_map.csv")},
        locations={r["Workday_Location"]: r["ADP_Location_Code"]
                   for r in read_csv(directory / "location_map.csv")},
        planted=json.loads((directory / "planted.json").read_text(encoding="utf-8")),
    )


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("source")
    generate(out)
    return out


@pytest.fixture(scope="module")
def data(generated: Path) -> Data:
    return load(generated)


# ── reproducibility ──────────────────────────────────────────────────────────────────────────


def test_generation_is_byte_for_byte_reproducible(generated: Path, tmp_path: Path) -> None:
    generate(tmp_path)
    for name in OUTPUT_FILES:
        assert (tmp_path / name).read_bytes() == (generated / name).read_bytes(), name


def test_committed_data_matches_the_generator(generated: Path) -> None:
    for name in OUTPUT_FILES:
        assert (COMMITTED / name).read_bytes() == (generated / name).read_bytes(), (
            f"data/source/{name} is stale — run `make data`")


# ── the ground truth ─────────────────────────────────────────────────────────────────────────


def test_planted_counts_and_classes(data: Data) -> None:
    assert len(data.planted["mismatches"]) == 18
    assert len(data.planted["decoys"]) == 4
    assert Counter(e["type"] for e in data.entries) == {t: n for t, (n, _) in EXPECTED.items()}
    for e in data.entries:
        assert e.get("change_class") == EXPECTED[e["type"]][1], e["id"]
    planted_people = [e["employee_ref"] for e in data.entries]
    assert len(planted_people) == len(set(planted_people)), "one plant per person"


def test_every_planted_row_exists_in_the_exports(data: Data) -> None:
    for e in data.entries:
        ref, assoc = e["employee_ref"], e["associate_id"]
        if e["type"] == "M6":
            assert ref == f"ADP:{assoc}" and e["workday"] is None
            assert assoc not in data.crosswalk.values()
        else:
            assert ref in data.workday, e["id"]
            for col, value in e["workday"].items():
                assert data.workday[ref][col] == value, (e["id"], col)
        if e["type"] == "M5":
            assert assoc is None and e["adp"] is None
            assert ref not in data.crosswalk
        else:
            assert assoc in data.adp, e["id"]
            for col, value in e["adp"].items():
                assert data.adp[assoc][col] == value, (e["id"], col)
            if e["type"] != "M6":
                assert data.crosswalk[ref] == assoc, e["id"]


# ── no accidental extras ─────────────────────────────────────────────────────────────────────


def annual(salary: str, hourly: str) -> Decimal:
    return Decimal(salary) if salary else Decimal(hourly) * 2080


def differences(data: Data) -> set[tuple[str, str]]:
    """(employee_ref, field) for every matched pair that differs after normalisation."""
    by_assoc = {a: e for e, a in data.crosswalk.items()}
    out: set[tuple[str, str]] = set()
    for emp, assoc in data.crosswalk.items():
        w, a = data.workday[emp], data.adp[assoc]
        mgr = w["Manager_Employee_ID"]
        checks = {
            "worker_status": (STATUS[w["Worker_Status"]], w["Termination_Date"])
            != (a["Position_Status"], a["Termination_Date"]),
            "pay": abs(annual(w["Annual_Base_Pay"], w["Hourly_Rate"])
                       - annual(a["Annual_Salary"], a["Rate_Amount"])) > 1,
            "rate_type": w["Pay_Rate_Type"] != a["Rate_Type"],
            "cost_center": data.cost_centers[w["Cost_Center"]] != a["Home_Department"],
            "location": data.locations[w["Location"]] != a["Location_Code"],
            "manager": (data.crosswalk[mgr] if mgr else "") != a["Reports_To_Associate_ID"],
            "legal_name": (w["Legal_First_Name"], w["Legal_Last_Name"])
            != (a["First_Name"], a["Last_Name"]),
            "work_email": w["Work_Email"].strip().lower() != a["Work_Email"].strip().lower(),
        }
        out |= {(emp, f) for f, differs in checks.items() if differs}
        assert not a["Reports_To_Associate_ID"] or a["Reports_To_Associate_ID"] in by_assoc
    return out


def test_no_unplanted_differences(data: Data) -> None:
    expected = {(e["employee_ref"], e["field"]) for e in data.planted["mismatches"]
                if e["type"] not in ("M5", "M6")}
    assert differences(data) == expected

    unmatched_workday = set(data.workday) - set(data.crosswalk)
    unmatched_adp = set(data.adp) - set(data.crosswalk.values())
    assert unmatched_workday == data.refs("M5")
    assert {f"ADP:{a}" for a in unmatched_adp} == data.refs("M6")


def test_decoys_differ_raw_and_only_they_do(data: Data) -> None:
    raw_email, raw_pay, preferred = set(), set(), set()
    for emp, assoc in data.crosswalk.items():
        w, a = data.workday[emp], data.adp[assoc]
        if w["Work_Email"] != a["Work_Email"]:
            raw_email.add(emp)
        if (w["Annual_Base_Pay"], w["Hourly_Rate"]) != (a["Annual_Salary"], a["Rate_Amount"]):
            raw_pay.add(emp)
    for emp, w in data.workday.items():
        if w["Preferred_Name"] != w["Legal_First_Name"]:
            preferred.add(emp)
    assert raw_email == data.refs("D1")
    assert raw_pay == data.refs("D2", "M2")  # M2 differ for real; D2 only in representation
    assert preferred == data.refs("D3")

    (d2,) = [e for e in data.planted["decoys"] if e["type"] == "D2"]
    gap = abs(annual("", d2["workday"]["Hourly_Rate"]) - Decimal(d2["adp"]["Annual_Salary"]))
    assert 0 < gap <= 1, "D2 should exercise the ±$1 tolerance"


# ── the story ────────────────────────────────────────────────────────────────────────────────


def test_reorg_date_is_only_on_the_reorg_rows(data: Data) -> None:
    reorg = data.planted["reorg_effective_date"]
    on_reorg = {e for e, w in data.workday.items() if w["Org_Effective_Date"] == reorg}
    assert data.refs("M3") <= on_reorg
    m4_on_reorg = on_reorg & data.refs("M4")
    assert len(m4_on_reorg) == 2
    assert on_reorg == data.refs("M3") | m4_on_reorg


def test_one_termination_after_the_payroll_cutoff(data: Data) -> None:
    assert data.planted["payroll_cutoff"] == PAYROLL_CUTOFF.isoformat()
    terms = [e["workday"]["Termination_Date"] for e in data.planted["mismatches"]
             if e["type"] == "M1"]
    assert sum(t > PAYROLL_CUTOFF.isoformat() for t in terms) == 1


def test_no_accidental_patterns(data: Data) -> None:
    """Step 7 reports any group of ≥3 mismatches as a pattern. Only the reorg may form one."""
    reorg = data.planted["reorg_effective_date"]
    cutoff = PAYROLL_CUTOFF.isoformat()
    rows = []
    for e in data.planted["mismatches"]:
        if e["type"] == "M6":
            continue
        w = data.workday[e["employee_ref"]]
        if w["Org_Effective_Date"] != reorg:
            rows.append(w)
    for column in ("Cost_Center", "Org_Effective_Date", "Comp_Effective_Date"):
        top = Counter(w[column] for w in rows).most_common(1)[0]
        assert top[1] < 3, (column, top)
    after_cutoff = [w for w in rows if max(w["Hire_Date"], w["Termination_Date"]) > cutoff]
    assert len(after_cutoff) < 3


def test_shape(data: Data) -> None:
    workers = list(data.workday.values())
    assert len(workers) == 200
    assert len(data.cost_centers) == 12
    assert {w["Cost_Center"] for w in workers} <= set(data.cost_centers)
    assert len(data.locations) == 4
    assert {w["Location"] for w in workers} == set(data.locations)
    managers = {w["Manager_Employee_ID"] for w in workers} - {""}
    assert 20 <= len(managers) <= 30
    assert {w["Pay_Rate_Type"] for w in workers} == {"Salary", "Hourly"}
    assert len({w["Work_Email"] for w in workers}) == len(workers)
    assert len({a["File_Number"] for a in data.adp.values()}) == len(data.adp)


def test_people_are_fictional(data: Data) -> None:
    emails = [w["Work_Email"] for w in data.workday.values()]
    emails += [a["Work_Email"] for a in data.adp.values()]
    assert all(e.strip().lower().endswith("@northpine.example") for e in emails)
