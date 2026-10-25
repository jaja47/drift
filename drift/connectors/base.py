"""Where HR data comes from: the contract every source must meet.

Drift reads people from a `WorkerSource` (Workday, the system of record for people and org) and pay
records from a `PayrollSource` (ADP), plus the `Reference` data that joins them. Today all three
are CSV exports in `data/source/` (see `csv_source.py`). In production:

- **WorkerSource** → a Workday RaaS (Report-as-a-Service) custom report, fetched over HTTPS as
  JSON or CSV with an integration system user.
- **PayrollSource** → ADP's Workers API (for Workforce Now, `GET /hr/v2/workers`), called with an
  ADP API client's OAuth credentials and certificate.
- **Reference** → the crosswalk usually comes from an ID stored on the Workday worker or from the
  integration's own mapping table; the code maps come from whoever owns the integration.

IT would own those connections. A replacement only has to return rows carrying the columns below,
each with a `source_ref` that points back to where the row came from. Normalisation, detection and
governance do not change.

Sources are read-only. Nothing in Drift writes back to a source; corrections go to a separate
target (`drift/target/`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# The columns Drift reads. A source may carry more; it must carry these.
WORKDAY_COLUMNS = (
    "Employee_ID", "Legal_First_Name", "Legal_Last_Name", "Preferred_Name", "Work_Email",
    "Worker_Status",
    "Hire_Date", "Termination_Date", "Manager_Employee_ID", "Cost_Center", "Location",
    "Pay_Rate_Type", "Annual_Base_Pay", "Hourly_Rate", "Comp_Effective_Date",
    "Org_Effective_Date",
)
ADP_COLUMNS = (
    "Associate_ID", "First_Name", "Last_Name", "Work_Email", "Position_Status",
    "Termination_Date", "Home_Department", "Reports_To_Associate_ID", "Location_Code",
    "Rate_Type", "Annual_Salary", "Rate_Amount",
)


class SourceDataError(ValueError):
    """The input cannot be used without guessing — a missing column, a malformed value, or an
    identity that maps two ways. Drift stops rather than guess."""


@dataclass(frozen=True)
class SourceRow:
    """One row exactly as the source holds it: no trimming, no case changes, no parsing."""

    source_ref: str  # e.g. "workday_workers.csv#Employee_ID=W10042"
    values: Mapping[str, str]


@dataclass(frozen=True)
class Reference:
    """What joins the two systems. They share no ID, so the crosswalk is the only link."""

    crosswalk: Mapping[str, str]  # Workday Employee_ID → ADP Associate_ID
    cost_centers: Mapping[str, str]  # Workday Cost_Center → ADP Home_Department
    locations: Mapping[str, str]  # Workday Location → ADP Location_Code

    def __post_init__(self) -> None:
        seen: dict[str, str] = {}
        for employee, associate in self.crosswalk.items():
            if associate in seen:
                raise SourceDataError(
                    f"crosswalk maps ADP {associate} to both {seen[associate]} and {employee}")
            seen[associate] = employee


@runtime_checkable
class WorkerSource(Protocol):
    def list_workers(self) -> list[SourceRow]:
        """Every worker, with at least WORKDAY_COLUMNS."""
        ...


@runtime_checkable
class PayrollSource(Protocol):
    def list_payroll(self) -> list[SourceRow]:
        """Every payroll record, with at least ADP_COLUMNS."""
        ...
