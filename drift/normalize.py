"""Turn raw Workday and ADP rows into one comparable record (a `Pair`) per person.

Each rule below removes a difference that is only formatting, so detection sees what really
disagrees:

- **identity** — the systems share no ID, so the crosswalk joins them. A row it cannot join is
  kept, marked `workday_only` (M5) or `adp_only` (M6), never dropped.
- **status** — Workday `Active/Terminated/Leave` and ADP `A/T/L` → `active/terminated/leave`.
- **cost centre** — Workday `CC-410 Supply Chain Analytics` → ADP department `410`, by the map.
- **location** — Workday `Toronto, ON` → ADP `TOR`, by the map.
- **manager** — ADP's `Reports_To_Associate_ID` → a Workday `Employee_ID`, by the crosswalk.
- **pay** — both sides annualised in cents, hourly × 2080; equal within ±$1 (`pay_matches`).
- **email** — trimmed and lower-cased.
- **names** — legal names only (Workday's `Preferred_Name` is never compared); whitespace
  collapsed; compared case-insensitively.

`format_differences` lists the values these rules reconcile record by record — what a naive
comparison would have flagged and Drift deliberately ignores.

Normalisation never hides a difference by guessing. A value it cannot map is kept with a marker
(`unrecognised:…`, `unmapped:…`, `ADP:…`), so it compares unequal to anything and gets looked at.
A value it cannot parse, or an ID that appears twice, stops the run (`SourceDataError`).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, fields
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal, TypeVar

from drift.connectors.base import Reference, SourceDataError, SourceRow

HOURS_PER_YEAR = 2080
PAY_TOLERANCE_CENTS = 100  # ±$1 on the annual figure

WORKDAY_STATUS = {"Active": "active", "Terminated": "terminated", "Leave": "leave"}
ADP_STATUS = {"A": "active", "T": "terminated", "L": "leave"}

Match = Literal["both", "workday_only", "adp_only"]
T = TypeVar("T")


@dataclass(frozen=True)
class Pair:
    """One person as both systems see them. `wd_*` is Workday's side, `adp_*` is ADP's, already
    in the same terms; a side the person is missing from is all None."""

    employee_ref: str  # Workday Employee_ID, or "ADP:<Associate_ID>" when only ADP has them
    match: Match
    workday_source: str | None
    adp_source: str | None
    associate_id: str | None
    # Workday-only context
    hire_date: date | None
    org_effective_date: date | None
    comp_effective_date: date | None
    # compared fields
    wd_status: str | None
    adp_status: str | None
    wd_termination_date: date | None
    adp_termination_date: date | None
    wd_name: str | None  # legal name, for display
    adp_name: str | None
    wd_name_key: str | None  # legal name, for comparison
    adp_name_key: str | None
    wd_email: str | None
    adp_email: str | None
    wd_cost_center: str | None  # Workday's cost centre as exported, for display
    wd_department: str | None  # in ADP's terms (Home_Department code)
    adp_department: str | None
    wd_location: str | None  # in ADP's terms (Location_Code)
    adp_location: str | None
    wd_manager: str | None  # in Workday's terms (Employee_ID)
    adp_manager: str | None
    wd_rate_type: str | None
    adp_rate_type: str | None
    wd_annual_pay_cents: int | None
    adp_annual_pay_cents: int | None


PAIR_COLUMNS = tuple(f.name for f in fields(Pair))
_WD_SIDE = (
    "hire_date", "org_effective_date", "comp_effective_date", "wd_status", "wd_termination_date",
    "wd_name", "wd_name_key", "wd_email", "wd_cost_center", "wd_department", "wd_location",
    "wd_manager", "wd_rate_type", "wd_annual_pay_cents",
)
_ADP_SIDE = (
    "adp_status", "adp_termination_date", "adp_name", "adp_name_key", "adp_email",
    "adp_department", "adp_location", "adp_manager", "adp_rate_type", "adp_annual_pay_cents",
)


# ── the rules ────────────────────────────────────────────────────────────────────────────────


def text(raw: str) -> str | None:
    """Trimmed, or None if blank."""
    s = raw.strip()
    return s or None


def status(raw: str, vocabulary: Mapping[str, str]) -> str | None:
    s = text(raw)
    if s is None:
        return None
    return vocabulary.get(s, f"unrecognised:{s}")


def parse_date(raw: str) -> date | None:
    s = text(raw)
    return date.fromisoformat(s) if s else None


def money_cents(raw: str) -> int | None:
    """'72000.00' → 7200000. Thousands separators are accepted; anything else is an error."""
    s = text(raw)
    if s is None:
        return None
    try:
        amount = Decimal(s.replace(",", ""))
    except InvalidOperation:
        raise ValueError(f"not an amount: {raw!r}") from None
    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def annual_pay_cents(rate_type: str, annual: str, hourly: str) -> int | None:
    """Annual pay in cents. Uses the figure that matches the declared rate type, and falls back to
    the other when that one is blank — ADP sometimes holds an hourly worker's pay annualised."""
    annual_cents, hourly_cents = money_cents(annual), money_cents(hourly)
    as_annual = None if hourly_cents is None else hourly_cents * HOURS_PER_YEAR
    if text(rate_type) == "Hourly":
        return as_annual if as_annual is not None else annual_cents
    return annual_cents if annual_cents is not None else as_annual


def pay_matches(a: int | None, b: int | None) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) <= PAY_TOLERANCE_CENTS


def email(raw: str) -> str | None:
    s = text(raw)
    return s.lower() if s else None


def legal_name(first: str, last: str) -> str | None:
    s = " ".join(f"{first} {last}".split())
    return unicodedata.normalize("NFC", s) or None


def name_key(name: str | None) -> str | None:
    return name.casefold() if name else None


def mapped(raw: str, mapping: Mapping[str, str]) -> str | None:
    """Workday's value in ADP's terms. Unmapped values are kept, marked, and never equal a code."""
    s = text(raw)
    if s is None:
        return None
    return mapping.get(s, f"unmapped:{s}")


def manager_from_adp(raw: str, employee_of: Mapping[str, str]) -> str | None:
    """ADP's reports-to associate id as a Workday employee id, or `ADP:<id>` if the crosswalk
    has no such associate."""
    s = text(raw)
    if s is None:
        return None
    return employee_of.get(s, f"ADP:{s}")


# ── the join ─────────────────────────────────────────────────────────────────────────────────


def _checked(row: SourceRow, what: str, value: Callable[[], T]) -> T:
    """Run one parse, and say which row and column it failed on."""
    try:
        return value()
    except ValueError as e:
        raise SourceDataError(f"{row.source_ref}: {what}: {e}") from None


def _day(row: SourceRow, column: str) -> date | None:
    return _checked(row, column, lambda: parse_date(row.values[column]))


def _workday_side(w: SourceRow, ref: Reference) -> dict[str, object]:
    v = w.values
    name = legal_name(v["Legal_First_Name"], v["Legal_Last_Name"])
    return {
        "hire_date": _day(w, "Hire_Date"),
        "org_effective_date": _day(w, "Org_Effective_Date"),
        "comp_effective_date": _day(w, "Comp_Effective_Date"),
        "wd_status": status(v["Worker_Status"], WORKDAY_STATUS),
        "wd_termination_date": _day(w, "Termination_Date"),
        "wd_name": name,
        "wd_name_key": name_key(name),
        "wd_email": email(v["Work_Email"]),
        "wd_cost_center": text(v["Cost_Center"]),
        "wd_department": mapped(v["Cost_Center"], ref.cost_centers),
        "wd_location": mapped(v["Location"], ref.locations),
        "wd_manager": text(v["Manager_Employee_ID"]),
        "wd_rate_type": text(v["Pay_Rate_Type"]),
        "wd_annual_pay_cents": _checked(w, "pay", lambda: annual_pay_cents(
            v["Pay_Rate_Type"], v["Annual_Base_Pay"], v["Hourly_Rate"])),
    }


def _adp_side(a: SourceRow, employee_of: Mapping[str, str]) -> dict[str, object]:
    v = a.values
    name = legal_name(v["First_Name"], v["Last_Name"])
    return {
        "adp_status": status(v["Position_Status"], ADP_STATUS),
        "adp_termination_date": _day(a, "Termination_Date"),
        "adp_name": name,
        "adp_name_key": name_key(name),
        "adp_email": email(v["Work_Email"]),
        "adp_department": text(v["Home_Department"]),
        "adp_location": text(v["Location_Code"]),
        "adp_manager": manager_from_adp(v["Reports_To_Associate_ID"], employee_of),
        "adp_rate_type": text(v["Rate_Type"]),
        "adp_annual_pay_cents": _checked(a, "pay", lambda: annual_pay_cents(
            v["Rate_Type"], v["Annual_Salary"], v["Rate_Amount"])),
    }


def _index(rows: Iterable[SourceRow], key: str) -> dict[str, SourceRow]:
    out: dict[str, SourceRow] = {}
    for row in rows:
        ident = row.values[key].strip()
        if ident in out:
            raise SourceDataError(f"{key} {ident} appears more than once ({row.source_ref})")
        out[ident] = row
    return out


def normalize(workers: Iterable[SourceRow], payroll: Iterable[SourceRow],
              ref: Reference) -> list[Pair]:
    """One Pair per person: every Workday worker, then every ADP record nobody in Workday claims."""
    workday = _index(workers, "Employee_ID")
    adp = _index(payroll, "Associate_ID")
    employee_of = {associate: employee for employee, associate in ref.crosswalk.items()}

    def pair(employee_ref: str, w: SourceRow | None, a: SourceRow | None) -> Pair:
        match: Match = "both" if w and a else "workday_only" if w else "adp_only"
        return Pair(
            employee_ref=employee_ref,
            match=match,
            workday_source=w.source_ref if w else None,
            adp_source=a.source_ref if a else None,
            associate_id=a.values["Associate_ID"].strip() if a else None,
            **(_workday_side(w, ref) if w else dict.fromkeys(_WD_SIDE)),
            **(_adp_side(a, employee_of) if a else dict.fromkeys(_ADP_SIDE)),
        )

    pairs = []
    for employee, w in workday.items():
        associate = ref.crosswalk.get(employee)
        pairs.append(pair(employee, w, adp.get(associate) if associate else None))
    for associate, a in adp.items():
        if employee_of.get(associate) not in workday:
            pairs.append(pair(f"ADP:{associate}", None, a))
    return pairs


# ── what normalisation reconciles ────────────────────────────────────────────────────────────


FORMAT_KINDS = {
    "work_email": "email differs only in case or spacing",
    "pay": "same pay held in a different form (hourly vs annual, within $1)",
    "legal_name": "legal name differs only in case or spacing",
    "preferred_name": "Workday preferred name differs from the legal name ADP holds",
}


@dataclass(frozen=True)
class FormatDifference:
    employee_ref: str
    field: str  # a key of FORMAT_KINDS
    why: str


def format_differences(workers: Iterable[SourceRow], payroll: Iterable[SourceRow],
                       ref: Reference) -> list[FormatDifference]:
    """Matched people whose values differ as exported but agree once normalised: the
    differences Drift deliberately does not flag.

    Only per-record differences count. The translations every row goes through — status
    vocabularies, cost-centre and location codes, manager ids through the crosswalk — are how
    the two systems are joined, not differences in any one record."""
    workday = _index(workers, "Employee_ID")
    adp = _index(payroll, "Associate_ID")
    out: list[FormatDifference] = []
    for employee, w in workday.items():
        a = adp.get(ref.crosswalk.get(employee, ""))
        if a is None:
            continue
        wv, av = w.values, a.values
        found = []
        if wv["Work_Email"] != av["Work_Email"] and email(wv["Work_Email"]) == email(
                av["Work_Email"]):
            found.append("work_email")
        wd_pay = (text(wv["Annual_Base_Pay"]), text(wv["Hourly_Rate"]))
        adp_pay = (text(av["Annual_Salary"]), text(av["Rate_Amount"]))
        if wd_pay != adp_pay and pay_matches(
                annual_pay_cents(wv["Pay_Rate_Type"], wv["Annual_Base_Pay"], wv["Hourly_Rate"]),
                annual_pay_cents(av["Rate_Type"], av["Annual_Salary"], av["Rate_Amount"])):
            found.append("pay")
        wd_name = legal_name(wv["Legal_First_Name"], wv["Legal_Last_Name"])
        adp_name = legal_name(av["First_Name"], av["Last_Name"])
        if (wv["Legal_First_Name"], wv["Legal_Last_Name"]) != (
                av["First_Name"], av["Last_Name"]) and name_key(wd_name) == name_key(adp_name):
            found.append("legal_name")
        preferred = text(wv["Preferred_Name"])
        if preferred and name_key(preferred) != name_key(text(wv["Legal_First_Name"])):
            found.append("preferred_name")
        out += [FormatDifference(employee, f, FORMAT_KINDS[f]) for f in found]
    return out
