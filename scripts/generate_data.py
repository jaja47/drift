"""Generate Drift's synthetic Workday and ADP exports, with planted mismatches and decoys.

    python -m scripts.generate_data             # writes data/source/
    python -m scripts.generate_data --out DIR

Deterministic: seed 42 and a pinned Faker give the same bytes on every run. Everything is
fictional: the company (Northpine), the people, and the email domain (`.example` is reserved by
RFC 2606, so it can never belong to anyone).

The ground truth goes to planted.json: every planted mismatch and decoy with its type, field and
expected change class. Tests score detection against it; the detector never reads it.

The story the data tells:
- On 2026-10-01 Supply Chain Analytics (CC-410) was reorganised. Five analysts' cost was moved to
  CC-420 (their reporting line did not change: in Workday cost centre and supervisory org are
  separate), and two analysts moved to the other team lead. None of it reached ADP.
- Everything else is an isolated defect, spread out so that no cost centre or date collects
  three of them by accident — the only pattern in the data is the one that was planted.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from faker import Faker

from drift.constants import PAYROLL_CUTOFF

SEED = 42
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "data" / "source"

EXPORT_DATE = date(2026, 10, 16)
REORG_DATE = date(2026, 10, 1)
COMP_CHANGE_DATE = date(2026, 10, 1)  # the one Workday pay change that did not carry over
LAST_ORDINARY_CHANGE = date(2026, 9, 30)  # every clean org/comp change predates the reorg
EMAIL_DOMAIN = "northpine.example"
HOURS_PER_YEAR = 2080

# Workday location name → (ADP location code, country)
LOCATIONS = {
    "Toronto, ON": ("TOR", "CA"),
    "Calgary, AB": ("CGY", "CA"),
    "Chicago, IL": ("CHI", "US"),
    "Columbus, OH": ("CMH", "US"),
}
CURRENCY = {"CA": "CAD", "US": "USD"}
PAY_FREQUENCY = {"CA": "Semimonthly", "US": "Biweekly"}
ADP_COMPANY_CODE = {"CA": "NPC", "US": "NPU"}  # prefixes Position_ID, as ADP does
ADP_STATUS = {"Active": "A", "Terminated": "T", "Leave": "L"}

NICKNAMES = {
    "Abigail": "Abby", "Alexander": "Alex", "Andrew": "Drew", "Benjamin": "Ben",
    "Charles": "Charlie", "Christopher": "Chris", "Daniel": "Dan", "David": "Dave",
    "Deborah": "Debbie", "Edward": "Ted", "Elizabeth": "Liz", "James": "Jim", "Jeffrey": "Jeff",
    "Jennifer": "Jen", "Jessica": "Jess", "Jonathan": "Jon", "Joseph": "Joe", "Katherine": "Kate",
    "Kimberly": "Kim", "Margaret": "Peggy", "Matthew": "Matt", "Michael": "Mike",
    "Nicholas": "Nick", "Patricia": "Trish", "Rebecca": "Becca", "Richard": "Rick",
    "Robert": "Bob", "Samantha": "Sam", "Stephanie": "Steph", "Steven": "Steve",
    "Thomas": "Tom", "Timothy": "Tim", "William": "Will",
}

WORKDAY_COLUMNS = [
    "Employee_ID", "Legal_First_Name", "Legal_Last_Name", "Preferred_Name", "Work_Email",
    "Worker_Status", "Hire_Date", "Termination_Date", "Supervisory_Organization",
    "Manager_Employee_ID", "Cost_Center", "Location", "Pay_Rate_Type", "Annual_Base_Pay",
    "Hourly_Rate", "Currency", "Comp_Effective_Date", "Org_Effective_Date",
]
ADP_COLUMNS = [
    "Associate_ID", "File_Number", "Position_ID", "First_Name", "Last_Name", "Work_Email",
    "Position_Status", "Termination_Date", "Home_Department", "Reports_To_Associate_ID",
    "Location_Code", "Rate_Type", "Annual_Salary", "Rate_Amount", "Pay_Frequency",
]

OUTPUT_FILES = [
    "workday_workers.csv", "adp_payroll.csv", "id_crosswalk.csv", "cost_center_map.csv",
    "location_map.csv", "planted.json",
]


@dataclass(frozen=True)
class Dept:
    code: str  # ADP Home_Department
    name: str
    size: int  # people, managers included
    leads: int  # team leads under the head
    locations: tuple[str, ...]
    salary: tuple[int, int]  # IC band, whole dollars
    hourly_share: float = 0.0
    hourly_cents: tuple[int, int] = (0, 0)

    @property
    def cost_center(self) -> str:
        return f"CC-{self.code} {self.name}"


TOR, CGY, CHI, CMH = LOCATIONS
DEPTS = [
    Dept("110", "Executive Office", 4, 0, (TOR,), (64_000, 98_000)),
    Dept("210", "Finance", 12, 1, (TOR, CHI), (68_000, 115_000)),
    Dept("220", "People Operations", 8, 0, (TOR, CHI), (62_000, 105_000)),
    Dept("310", "Retail Account Sales", 18, 2, (CHI, TOR, CGY), (64_000, 118_000)),
    Dept("320", "Customer Success", 15, 1, (CHI, CGY), (58_000, 96_000)),
    Dept("410", "Supply Chain Analytics", 24, 2, (TOR, CHI), (78_000, 128_000)),
    Dept("420", "Fulfillment Network Engineering", 15, 1, (TOR, CGY), (92_000, 150_000)),
    Dept("430", "Platform Engineering", 24, 2, (TOR, CGY, CHI), (98_000, 165_000)),
    Dept("510", "Product Management", 9, 0, (TOR, CHI), (95_000, 155_000)),
    Dept("610", "Marketing", 10, 0, (CHI, TOR), (66_000, 112_000)),
    Dept("710", "Distribution Centre Operations", 40, 3, (CMH,), (52_000, 72_000),
         hourly_share=0.92, hourly_cents=(1875, 2750)),
    Dept("720", "Customer Support", 21, 1, (CGY, CMH), (50_000, 68_000),
         hourly_share=0.75, hourly_cents=(2050, 2900)),
]
DEPT = {d.code: d for d in DEPTS}
REORG_FROM, REORG_TO = DEPT["410"], DEPT["420"]

# Planted types (PLAN.md §2): description, field, expected change_class (None for decoys).
TYPES: dict[str, tuple[str, str, str | None]] = {
    "M1": ("Terminated in Workday, active in ADP", "worker_status", "employment_status"),
    "M2": ("Pay differs", "pay", "compensation"),
    "M3": ("Cost centre differs", "cost_center", "routine"),
    "M4": ("Manager differs", "manager", "routine"),
    "M5": ("In Workday, missing from ADP", "record", "employment_status"),
    "M6": ("In ADP, missing from Workday", "record", "identity"),
    "M7": ("Legal name differs", "legal_name", "identity"),
    "D1": ("Email case / whitespace only", "work_email", None),
    "D2": ("Hourly rate × 2080 = annual salary", "pay", None),
    "D3": ("Preferred name ≠ legal first name", "preferred_name", None),
}

# Columns each planted entry quotes from the exports, so a test can find them there.
EVIDENCE: dict[str, tuple[list[str], list[str]]] = {
    "M1": (["Worker_Status", "Termination_Date"], ["Position_Status", "Termination_Date"]),
    "M2": (["Pay_Rate_Type", "Annual_Base_Pay", "Hourly_Rate", "Comp_Effective_Date"],
           ["Rate_Type", "Annual_Salary", "Rate_Amount"]),
    "M3": (["Cost_Center", "Org_Effective_Date"], ["Home_Department"]),
    "M4": (["Manager_Employee_ID", "Org_Effective_Date"], ["Reports_To_Associate_ID"]),
    "M5": (["Worker_Status", "Hire_Date"], []),
    "M6": ([], ["Position_Status", "File_Number"]),
    "M7": (["Legal_First_Name", "Legal_Last_Name"], ["First_Name", "Last_Name"]),
    "D1": (["Work_Email"], ["Work_Email"]),
    "D2": (["Pay_Rate_Type", "Annual_Base_Pay", "Hourly_Rate"],
           ["Rate_Type", "Annual_Salary", "Rate_Amount"]),
    "D3": (["Legal_First_Name", "Preferred_Name"], ["First_Name"]),
}


@dataclass(eq=False)
class Person:
    dept: Dept  # Workday's current cost centre
    role: str  # "ceo" | "head" | "lead" | "ic"
    location: str
    first: str
    last: str
    email: str
    hire: date
    salaried: bool
    pay: int  # whole dollars a year if salaried, cents an hour if hourly
    org_effective: date
    comp_effective: date
    manager: Person | None = None
    preferred: str | None = None
    status: str = "Active"
    termination: date | None = None
    in_workday: bool = True
    in_adp: bool = True
    adp_manager: Person | None = None  # set when ADP still reports to someone else
    adp: dict[str, str] = field(default_factory=dict)  # ADP columns that differ from Workday
    planted: str | None = None
    employee_id: str = ""
    associate_id: str = ""
    file_number: str = ""

    @property
    def country(self) -> str:
        return LOCATIONS[self.location][1]


@dataclass
class Plant:
    id: str  # "M3-2"
    type: str  # "M3"
    person: Person
    note: str = ""


def iso(d: date | None) -> str:
    return d.isoformat() if d else ""


def dollars(whole: int) -> str:
    return f"{whole}.00"


def rate(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def email_local(first: str, last: str) -> str:
    def part(s: str) -> str:
        ascii_ = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
        return "".join(c for c in ascii_.lower() if c.isalnum() or c == "-")

    return f"{part(first)}.{part(last)}"


class Generator:
    def __init__(self, seed: int = SEED) -> None:
        self.rng = random.Random(seed)
        self.fake = Faker("en_US")
        self.fake.seed_instance(seed)
        self.emails_taken: set[str] = set()
        self.people: list[Person] = []
        self.plants: list[Plant] = []

    # ── basics ───────────────────────────────────────────────────────────────────────────────

    def date_between(self, lo: date, hi: date) -> date:
        return lo + timedelta(days=self.rng.randint(0, (hi - lo).days))

    def round500(self, n: float) -> int:
        return int(round(n / 500) * 500)

    def new_name(self) -> tuple[str, str]:
        while True:
            first, last = self.fake.first_name(), self.fake.last_name()
            if email_local(first, last) not in self.emails_taken:
                self.emails_taken.add(email_local(first, last))
                return first, last

    def new_last_name(self, first: str) -> str:
        while True:
            last = self.fake.last_name()
            if email_local(first, last) not in self.emails_taken:
                self.emails_taken.add(email_local(first, last))
                return last

    def person(self, dept: Dept, role: str, manager: Person | None) -> Person:
        first, last = self.new_name()
        hire_window = {
            "ceo": (date(2011, 3, 1), date(2012, 6, 30)),
            "head": (date(2012, 9, 1), date(2020, 12, 31)),
            "lead": (date(2014, 1, 1), date(2023, 6, 30)),
            "ic": (date(2016, 1, 1), date(2026, 9, 15)),
        }[role]
        hire = self.date_between(*hire_window)
        lo, hi = dept.salary
        if role == "ceo":
            salaried, pay = True, 285_000
        elif role == "head":
            salaried, pay = True, self.round500(self.rng.uniform(hi * 1.20, hi * 1.45))
        elif role == "lead":
            salaried, pay = True, self.round500(self.rng.uniform(hi * 1.02, hi * 1.18))
        elif self.rng.random() < dept.hourly_share:
            salaried, pay = False, self.rng.randint(*dept.hourly_cents)
        else:
            salaried, pay = True, self.round500(self.rng.uniform(lo, hi))
        p = Person(
            dept=dept, role=role, location=self.rng.choice(dept.locations),
            first=first, last=last, email=f"{email_local(first, last)}@{EMAIL_DOMAIN}",
            hire=hire, salaried=salaried, pay=pay,
            org_effective=hire if self.rng.random() < 0.5
            else self.date_between(hire, LAST_ORDINARY_CHANGE),
            comp_effective=hire if self.rng.random() < 0.3
            else self.date_between(hire, LAST_ORDINARY_CHANGE),
            manager=manager,
        )
        self.people.append(p)
        return p

    # ── the company ──────────────────────────────────────────────────────────────────────────

    def build_company(self) -> None:
        ceo: Person | None = None
        for dept in DEPTS:
            if dept.code == "110":
                head = ceo = self.person(dept, "ceo", manager=None)
            else:
                head = self.person(dept, "head", manager=ceo)
            leads = [self.person(dept, "lead", manager=head) for _ in range(dept.leads)]
            for _ in range(dept.size - 1 - dept.leads):
                self.person(dept, "ic", manager=self.rng.choice(leads) if leads else head)

    def leads_of(self, dept: Dept) -> list[Person]:
        return [p for p in self.people if p.dept is dept and p.role == "lead"]

    def head_of(self, dept: Dept) -> Person:
        return next(p for p in self.people if p.dept is dept and p.role in ("head", "ceo"))

    # ── planting ─────────────────────────────────────────────────────────────────────────────

    def plant(self, type_: str, p: Person, note: str = "") -> None:
        p.planted = type_
        n = sum(1 for x in self.plants if x.type == type_) + 1
        self.plants.append(Plant(f"{type_}-{n}", type_, p, note))

    def pool(self, *, depts: list[Dept] | None = None, isolated: bool = False,
             where=lambda p: True) -> list[Person]:
        """Unplanted, active individual contributors. `isolated` keeps a planted mismatch away
        from the reorg's cost centres and from any cost centre that already holds two."""
        out = []
        for p in self.people:
            if p.role != "ic" or p.planted or p.status != "Active" or not p.in_workday:
                continue
            if depts is not None and p.dept not in depts:
                continue
            if isolated and (p.dept in (REORG_FROM, REORG_TO) or self.load(p.dept) >= 2):
                continue
            if where(p):
                out.append(p)
        if not out:
            raise RuntimeError("no eligible person left to plant on — adjust the generator")
        return out

    def load(self, dept: Dept) -> int:
        """Planted mismatches (not decoys) already in a cost centre."""
        return sum(1 for x in self.plants if x.type.startswith("M") and x.person.dept is dept)

    def plant_reorg(self) -> None:
        lead_a, lead_b = self.leads_of(REORG_FROM)
        moved = self.rng.sample(self.pool(depts=[REORG_FROM]), 7)
        for p in moved[:5]:  # M3: cost moved to CC-420, reporting line unchanged
            p.manager = lead_a
            p.dept = REORG_TO
            p.org_effective = REORG_DATE
            p.adp["Home_Department"] = REORG_FROM.code
            self.plant("M3", p, "Moved to CC-420 in the 2026-10-01 reorg; ADP still has 410.")
        for p in moved[5:]:  # M4: moved to the other team lead
            p.manager = lead_b
            p.adp_manager = lead_a
            p.org_effective = REORG_DATE
            self.plant("M4", p, "New manager in the 2026-10-01 reorg; ADP has the old one.")

    def plant_ghost(self) -> None:
        dc = DEPT["710"]
        first, last = self.new_name()
        p = Person(
            dept=dc, role="ic", location=CMH, first=first, last=last,
            email=f"{email_local(first, last)}@{EMAIL_DOMAIN}",
            hire=self.date_between(date(2024, 1, 1), date(2025, 12, 31)),
            salaried=False, pay=self.rng.randint(*dc.hourly_cents),
            org_effective=date(2024, 1, 1), comp_effective=date(2024, 1, 1),
            manager=self.rng.choice(self.leads_of(dc)), in_workday=False,
        )
        self.people.append(p)
        self.plant("M6", p, "Active in ADP with no Workday worker behind it — possible ghost.")

    def plant_isolated(self) -> None:
        for term in (date(2026, 9, 18), date(2026, 9, 30), date(2026, 10, 13)):
            p = self.rng.choice(self.pool(isolated=True,
                                          where=lambda p: p.hire < term - timedelta(days=60)))
            p.status, p.termination = "Terminated", term
            p.adp.update(Position_Status="A", Termination_Date="")
            after = term > PAYROLL_CUTOFF
            self.plant("M1", p, f"Terminated after the {PAYROLL_CUTOFF} payroll cutoff." if after
                       else f"Terminated before the {PAYROLL_CUTOFF} payroll cutoff.")

        p = self.rng.choice(self.pool(isolated=True, where=lambda p: p.salaried))
        old = p.pay
        p.pay = self.round500(old * 1.045)
        p.comp_effective = COMP_CHANGE_DATE
        p.adp["Annual_Salary"] = dollars(old)
        self.plant("M2", p, "Workday pay change effective 2026-10-01 not carried over.")

        def transposable(p: Person) -> bool:
            s = str(p.pay)
            return p.salaried and s[1] != s[2]

        p = self.rng.choice(self.pool(isolated=True, where=transposable))
        s = str(p.pay)
        p.adp["Annual_Salary"] = dollars(int(s[0] + s[2] + s[1] + s[3:]))
        self.plant("M2", p, "Two digits transposed when the salary was keyed into ADP.")

        p = self.rng.choice(self.pool(isolated=True, where=lambda p: not p.salaried))
        p.adp["Rate_Amount"] = rate(p.pay - 125)
        self.plant("M2", p, "ADP hourly rate is $1.25 below Workday.")

        p = self.rng.choice(self.pool(isolated=True,
                                      where=lambda p: p.manager and p.manager.role == "lead"))
        p.adp_manager = self.head_of(p.dept)
        p.org_effective = date(2026, 8, 17)
        self.plant("M4", p, "Manager changed 2026-08-17; ADP still reports to the previous one.")

        for hire in (date(2026, 10, 5), date(2026, 10, 14)):
            p = self.rng.choice(self.pool(isolated=True))
            p.hire = p.org_effective = p.comp_effective = hire
            p.in_adp = False
            self.plant("M5", p, f"New hire ({hire}) not set up in payroll.")

        p = self.rng.choice(self.pool(isolated=True))
        old_last = p.last
        p.last = self.new_last_name(p.first)  # email keeps the old surname, as it usually does
        p.adp["Last_Name"] = old_last
        self.plant("M7", p, "Legal surname changed in Workday; ADP has the previous one.")

    def plant_decoys(self) -> None:
        p, q = self.rng.sample(self.pool(), 2)
        local, _, domain = p.email.partition("@")
        p.adp["Work_Email"] = ".".join(s.capitalize() for s in local.split(".")) + "@" + domain
        self.plant("D1", p, "Same address, different case.")
        q.adp["Work_Email"] = q.email + " "
        self.plant("D1", q, "Same address with a trailing space.")

        # A rate whose annual figure has cents, so ADP's whole-dollar salary is off by under $1.
        p = self.rng.choice(self.pool(where=lambda p: not p.salaried and (p.pay * 80) % 100))
        total_cents = p.pay * HOURS_PER_YEAR
        p.adp.update(Rate_Amount="", Annual_Salary=dollars((total_cents + 50) // 100))
        self.plant("D2", p, "ADP holds the annualised hourly rate, rounded to whole dollars.")

        p = self.rng.choice(self.pool(where=lambda p: p.first in NICKNAMES))
        p.preferred = NICKNAMES[p.first]
        self.plant("D3", p, "Preferred name set in Workday; ADP has the legal name, as it should.")

    def clean_inactive(self) -> None:
        """Workers who are terminated or on leave in BOTH systems — consistent, never flagged."""
        for p in self.rng.sample(self.pool(where=lambda p: p.hire < date(2025, 12, 1)), 6):
            p.status = "Terminated"
            p.termination = self.date_between(date(2026, 1, 12), date(2026, 8, 28))
        for p in self.rng.sample(self.pool(), 4):
            p.status = "Leave"

    # ── ids ──────────────────────────────────────────────────────────────────────────────────

    def assign_ids(self) -> None:
        # Both systems number people in hire order; sorting is stable, so ties keep build order.
        workers = sorted((p for p in self.people if p.in_workday), key=lambda p: p.hire)
        for i, p in enumerate(workers):
            p.employee_id = f"W{10001 + i}"
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        taken: set[str] = set()
        payroll = sorted((p for p in self.people if p.in_adp), key=lambda p: p.hire)
        for i, p in enumerate(payroll):
            p.file_number = f"{1001 + i:06d}"
            while not p.associate_id or p.associate_id in taken:
                p.associate_id = "G" + "".join(self.rng.choice(alphabet) for _ in range(9))
            taken.add(p.associate_id)

    # ── rendering ────────────────────────────────────────────────────────────────────────────

    def workday_row(self, p: Person) -> dict[str, str]:
        org_owner = p.manager or p
        return {
            "Employee_ID": p.employee_id,
            "Legal_First_Name": p.first,
            "Legal_Last_Name": p.last,
            "Preferred_Name": p.preferred or p.first,
            "Work_Email": p.email,
            "Worker_Status": p.status,
            "Hire_Date": iso(p.hire),
            "Termination_Date": iso(p.termination),
            "Supervisory_Organization":
                f"{org_owner.dept.name} ({org_owner.first} {org_owner.last})",
            "Manager_Employee_ID": p.manager.employee_id if p.manager else "",
            "Cost_Center": p.dept.cost_center,
            "Location": p.location,
            "Pay_Rate_Type": "Salary" if p.salaried else "Hourly",
            "Annual_Base_Pay": dollars(p.pay) if p.salaried else "",
            "Hourly_Rate": "" if p.salaried else rate(p.pay),
            "Currency": CURRENCY[p.country],
            "Comp_Effective_Date": iso(p.comp_effective),
            "Org_Effective_Date": iso(p.org_effective),
        }

    def adp_row(self, p: Person) -> dict[str, str]:
        reports_to = p.adp_manager or p.manager
        row = {
            "Associate_ID": p.associate_id,
            "File_Number": p.file_number,
            "Position_ID": ADP_COMPANY_CODE[p.country] + p.file_number,
            "First_Name": p.first,
            "Last_Name": p.last,
            "Work_Email": p.email,
            "Position_Status": ADP_STATUS[p.status],
            "Termination_Date": iso(p.termination),
            "Home_Department": p.dept.code,
            "Reports_To_Associate_ID": reports_to.associate_id if reports_to else "",
            "Location_Code": LOCATIONS[p.location][0],
            "Rate_Type": "Salary" if p.salaried else "Hourly",
            "Annual_Salary": dollars(p.pay) if p.salaried else "",
            "Rate_Amount": "" if p.salaried else rate(p.pay),
            "Pay_Frequency": PAY_FREQUENCY[p.country],
        }
        row.update(p.adp)  # what ADP holds where it disagrees with Workday
        return row

    def planted_entry(self, plant: Plant, wd: dict[str, dict[str, str]],
                      adp: dict[str, dict[str, str]]) -> dict:
        p = plant.person
        description, field_, change_class = TYPES[plant.type]
        wd_cols, adp_cols = EVIDENCE[plant.type]
        entry = {
            "id": plant.id,
            "type": plant.type,
            "description": description,
            "employee_ref": p.employee_id if p.in_workday else f"ADP:{p.associate_id}",
            "associate_id": p.associate_id or None,
            "field": field_,
        }
        if change_class:
            entry["change_class"] = change_class
        entry["workday"] = ({c: wd[p.employee_id][c] for c in wd_cols} if p.in_workday else None)
        entry["adp"] = ({c: adp[p.associate_id][c] for c in adp_cols} if p.in_adp else None)
        entry["note"] = plant.note
        return entry

    # ── run ──────────────────────────────────────────────────────────────────────────────────

    def generate(self, out: Path) -> None:
        self.build_company()
        self.plant_reorg()
        self.plant_ghost()
        self.plant_isolated()
        self.plant_decoys()
        self.clean_inactive()
        self.assign_ids()

        workers = sorted((p for p in self.people if p.in_workday), key=lambda p: p.employee_id)
        payroll = sorted((p for p in self.people if p.in_adp), key=lambda p: p.file_number)
        wd = {p.employee_id: self.workday_row(p) for p in workers}
        adp = {p.associate_id: self.adp_row(p) for p in payroll}

        out.mkdir(parents=True, exist_ok=True)
        write_csv(out / "workday_workers.csv", WORKDAY_COLUMNS, list(wd.values()))
        write_csv(out / "adp_payroll.csv", ADP_COLUMNS, list(adp.values()))
        write_csv(out / "id_crosswalk.csv", ["Employee_ID", "Associate_ID"],
                  [{"Employee_ID": p.employee_id, "Associate_ID": p.associate_id}
                   for p in workers if p.in_adp])
        write_csv(out / "cost_center_map.csv", ["Workday_Cost_Center", "ADP_Home_Department"],
                  [{"Workday_Cost_Center": d.cost_center, "ADP_Home_Department": d.code}
                   for d in DEPTS])
        write_csv(out / "location_map.csv", ["Workday_Location", "ADP_Location_Code"],
                  [{"Workday_Location": name, "ADP_Location_Code": code}
                   for name, (code, _) in LOCATIONS.items()])

        entries = [self.planted_entry(x, wd, adp) for x in self.plants]
        planted = {
            "about": "Ground truth for Drift's synthetic data. Read by tests only — the detector "
                     "must never read this file.",
            "seed": SEED,
            "export_date": iso(EXPORT_DATE),
            "payroll_cutoff": iso(PAYROLL_CUTOFF),
            "reorg_effective_date": iso(REORG_DATE),
            "mismatches": sorted((e for e in entries if e["type"].startswith("M")),
                                 key=lambda e: e["id"]),
            "decoys": sorted((e for e in entries if e["type"].startswith("D")),
                             key=lambda e: e["id"]),
        }
        (out / "planted.json").write_text(
            json.dumps(planted, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_csv(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def generate(out: Path = DEFAULT_OUT, seed: int = SEED) -> None:
    Generator(seed).generate(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    generate(args.out)
    print(f"wrote {', '.join(OUTPUT_FILES)} to {args.out}")


if __name__ == "__main__":
    main()
