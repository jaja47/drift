"""The write target: a simulated ADP payroll table, `state/adp_current.csv`.

It is a copy of the ADP export. The copy is created by the first warranted fix that is written;
until then there is no file. The export in `data/source/` is only ever read.

Every write needs a warrant id. `govern.apply_with_warrant` is the only caller, and it confirms the
warrant with Warrant first. Writes are atomic (a temporary file, then a rename), so a write that
fails leaves the file as it was.

A fix always writes Workday's value, in ADP's terms, using the same maps as normalisation:

    M1 / M8 status    Position_Status and Termination_Date
    M2 pay            the pay column ADP pays from (Annual_Salary, or Rate_Amount if Hourly)
    M3 cost centre    Home_Department, through the cost-centre map
    M4 manager        Reports_To_Associate_ID, through the crosswalk
    M5 missing        a new row, built from the Workday worker
    M6 ghost          Position_Status T, terminated today unless ADP already has a date
    M7 legal name     First_Name and Last_Name
    M8 location       Location_Code, through the location map
    M8 email          Work_Email
    M8 rate type      Rate_Type, Annual_Salary and Rate_Amount

A value it cannot map is an error (`TargetError`), never a guess, and nothing is written.

`apply` returns the result that is committed to Warrant's ledger. The ledger is exportable, so the
result names the row and the columns changed. It gives the old and new value only for columns that
are codes or dates — never for pay, name or email columns.
"""

from __future__ import annotations

import csv
import io
import os
from collections import Counter
from collections.abc import Mapping
from datetime import date
from pathlib import Path

from drift.connectors import Reference, SourceRow
from drift.detect import Mismatch
from drift.normalize import WORKDAY_STATUS, email, mapped, status, text

ADP_STATUS_CODE = {"active": "A", "terminated": "T", "leave": "L"}
# Columns whose values never go into the ledger: the result says only that they changed.
UNRECORDED = frozenset({"First_Name", "Last_Name", "Work_Email", "Annual_Salary", "Rate_Amount"})
CREATED_ID_PREFIX = "DRIFT-"  # a record Drift creates is visibly Drift's until ADP assigns an id


class TargetError(RuntimeError):
    """The fix cannot be written without guessing. Nothing was written."""


class AdpTarget:
    def __init__(self, export: Path, current: Path, workers: Mapping[str, SourceRow],
                 reference: Reference, today: date | None = None) -> None:
        export, current = export.resolve(), current.resolve()
        if current == export or current.parent == export.parent:
            raise ValueError(f"{current} would overwrite the source export — the target must be a "
                             f"copy outside {export.parent}")
        self.export = export
        self.current = current
        self.workers = workers  # Workday rows by Employee_ID
        self.reference = reference
        self.today = today or date.today()

    # ── the one write ────────────────────────────────────────────────────────────────────────

    def apply(self, m: Mismatch, *, warrant_id: str) -> dict:
        """Write Workday's value for this mismatch into the copy. Needs a warrant id."""
        if not (warrant_id or "").strip():
            raise TargetError("no warrant id — nothing is written without one")
        header, rows = self._read()
        if m.type == "M5":
            associate, changed = self._create(m, header, rows)
            action = "create"
        else:
            associate = self._associate(m)
            index = next((i for i, r in enumerate(rows)
                          if r["Associate_ID"].strip() == associate), None)
            if index is None:
                raise TargetError(f"no ADP row with Associate_ID {associate}")
            before = rows[index]
            wanted = self._changes(m, before)
            changed = {c: (before[c], v) for c, v in wanted.items() if before[c] != v}
            rows[index] = {**before, **wanted}
            action = "deactivate" if m.type == "M6" else "update"
        if changed:
            self._write(header, rows)
        return {
            "target": self.current.name,
            "row": f"Associate_ID={associate}",
            "action": action,
            "changed": {c: ("changed (value not recorded)" if c in UNRECORDED
                            else {"from": old, "to": new}) for c, (old, new) in changed.items()},
            **({} if changed else {"note": "ADP already matched Workday; nothing to change"}),
        }

    def row(self, m: Mismatch) -> dict[str, str] | None:
        """The ADP row a mismatch is about, as the copy (or, before any write, the export) holds
        it. Read-only; None if ADP has no such row."""
        if m.type == "M5":
            associate = CREATED_ID_PREFIX + m.employee_ref
        else:
            associate = self._associate(m)
        return next((r for r in self._read()[1] if r["Associate_ID"].strip() == associate), None)

    # ── what each fix writes ─────────────────────────────────────────────────────────────────

    def _worker(self, m: Mismatch) -> Mapping[str, str]:
        w = self.workers.get(m.employee_ref)
        if w is None:
            raise TargetError(f"no Workday worker {m.employee_ref} to take the value from")
        return w.values

    def _associate(self, m: Mismatch) -> str:
        if m.employee_ref.startswith("ADP:"):
            return m.employee_ref.removeprefix("ADP:")
        associate = self.reference.crosswalk.get(m.employee_ref)
        if associate is None:
            raise TargetError(f"{m.employee_ref} has no entry in the ID crosswalk")
        return associate

    def _mapped(self, raw: str, mapping: Mapping[str, str], what: str) -> str:
        code = mapped(raw, mapping)
        if code is None or code.startswith("unmapped:"):
            raise TargetError(f"{what} {raw.strip()!r} is not in the {what} map")
        return code

    def _manager(self, w: Mapping[str, str]) -> str:
        manager = text(w["Manager_Employee_ID"])
        if manager is None:
            return ""
        associate = self.reference.crosswalk.get(manager)
        if associate is None:
            raise TargetError(f"manager {manager} has no entry in the ID crosswalk")
        return associate

    def _status(self, w: Mapping[str, str]) -> dict[str, str]:
        code = ADP_STATUS_CODE.get(status(w["Worker_Status"], WORKDAY_STATUS) or "")
        if code is None:
            raise TargetError(f"Workday status {w['Worker_Status'].strip()!r} has no ADP code")
        return {"Position_Status": code, "Termination_Date": text(w["Termination_Date"]) or ""}

    def _changes(self, m: Mismatch, adp: Mapping[str, str]) -> dict[str, str]:
        if m.type == "M6":  # deactivate: there is no Workday worker to copy from
            return {"Position_Status": "T",
                    "Termination_Date": text(adp["Termination_Date"]) or self.today.isoformat()}
        w = self._worker(m)
        if m.field == "worker_status":
            return self._status(w)
        if m.field == "pay":
            rate_type = text(adp["Rate_Type"])
            column, figure = {"Salary": ("Annual_Salary", w["Annual_Base_Pay"]),
                              "Hourly": ("Rate_Amount", w["Hourly_Rate"])}.get(
                rate_type or "", (None, ""))
            if column is None:
                raise TargetError(f"ADP rate type {rate_type!r} is not Salary or Hourly")
            if text(figure) is None:
                raise TargetError(f"Workday has no {rate_type.lower()} figure for "
                                  f"{m.employee_ref} — the rate types differ")
            return {column: text(figure)}
        if m.field == "cost_center":
            return {"Home_Department": self._mapped(w["Cost_Center"],
                                                    self.reference.cost_centers, "cost centre")}
        if m.field == "manager":
            return {"Reports_To_Associate_ID": self._manager(w)}
        if m.field == "legal_name":
            return {"First_Name": " ".join(w["Legal_First_Name"].split()),
                    "Last_Name": " ".join(w["Legal_Last_Name"].split())}
        if m.field == "location":
            return {"Location_Code": self._mapped(w["Location"], self.reference.locations,
                                                  "location")}
        if m.field == "work_email":
            return {"Work_Email": email(w["Work_Email"]) or ""}
        if m.field == "rate_type":
            return {"Rate_Type": text(w["Pay_Rate_Type"]) or "",
                    "Annual_Salary": text(w["Annual_Base_Pay"]) or "",
                    "Rate_Amount": text(w["Hourly_Rate"]) or ""}
        raise TargetError(f"no automatic fix for {m.type} {m.field}")

    def _create(self, m: Mismatch, header: list[str],
                rows: list[dict[str, str]]) -> tuple[str, dict[str, tuple[str, str]]]:
        """Append a payroll record for a Workday worker ADP does not have. Fields that are ADP's
        own (pay frequency, company code) follow the other records at the same location."""
        w = self._worker(m)
        associate = CREATED_ID_PREFIX + m.employee_ref
        if any(r["Associate_ID"].strip() == associate for r in rows):
            return associate, {}  # already created by an earlier, interrupted attempt
        location = self._mapped(w["Location"], self.reference.locations, "location")
        peers = [r for r in rows if r["Location_Code"].strip() == location]
        numbers = [int(r["File_Number"]) for r in rows if r["File_Number"].strip().isdigit()]
        width = max((len(r["File_Number"].strip()) for r in rows), default=6)
        file_number = str(max(numbers, default=0) + 1).zfill(width)
        prefixes = Counter(r["Position_ID"].removesuffix(r["File_Number"]) for r in peers
                           if r["File_Number"] and r["Position_ID"].endswith(r["File_Number"]))
        frequency = Counter(r["Pay_Frequency"] for r in peers if r["Pay_Frequency"].strip())
        row = dict.fromkeys(header, "") | {
            "Associate_ID": associate,
            "File_Number": file_number,
            "Position_ID": (prefixes.most_common(1)[0][0] + file_number) if prefixes else "",
            "First_Name": " ".join(w["Legal_First_Name"].split()),
            "Last_Name": " ".join(w["Legal_Last_Name"].split()),
            "Work_Email": email(w["Work_Email"]) or "",
            **self._status(w),
            "Home_Department": self._mapped(w["Cost_Center"], self.reference.cost_centers,
                                            "cost centre"),
            "Reports_To_Associate_ID": self._manager(w),
            "Location_Code": location,
            "Rate_Type": text(w["Pay_Rate_Type"]) or "",
            "Annual_Salary": text(w["Annual_Base_Pay"]) or "",
            "Rate_Amount": text(w["Hourly_Rate"]) or "",
            "Pay_Frequency": frequency.most_common(1)[0][0] if frequency else "",
        }
        rows.append({c: row[c] for c in header})
        return associate, {c: ("", row[c]) for c in header if row[c]}

    # ── the file ─────────────────────────────────────────────────────────────────────────────

    def _read(self) -> tuple[list[str], list[dict[str, str]]]:
        """The copy, or — until the first fix is written — the export, opened read-only."""
        path = self.current if self.current.exists() else self.export
        with path.open("r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            return list(reader.fieldnames or []), list(reader)

    def _write(self, header: list[str], rows: list[dict[str, str]]) -> None:
        """Rewrite the copy atomically: a temporary file, then a rename."""
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        self.current.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.current.with_name(self.current.name + ".tmp")
        tmp.write_text(out.getvalue(), encoding="utf-8")
        os.replace(tmp, self.current)
