"""state/drift.db: where each mismatch stands with governance. The SQL is in state.sql.

A mismatch is keyed by its fingerprint, so a rerun recognises one it has already handled and does
not ask Warrant for a second warrant.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from drift.detect import queries

SQL_PATH = Path(__file__).with_name("state.sql")

STATUSES = (
    "applied",  # warrant issued, fix written to the ADP copy, warrant committed
    "held",  # 202: waiting for the named approver; nothing written
    "approved",  # the approver said yes; written and committed on the next sync
    "rejected",  # the approver said no; never written
    "blocked",  # 422: Warrant refused the request; nothing written
    "expired",  # the held request lapsed, or Warrant no longer knows it; nothing written
    "failed",  # had a warrant, but the write failed or was refused; warrant aborted
    "uncommitted",  # written, but Warrant went away before the commit; sync retries the commit
    "warrant_unreachable",  # never requested: Warrant gave no usable answer; nothing written
)
# A rerun requests no new warrant for these. The rest are asked again: a refusal or a lapse
# writes nothing, so asking again cannot double-apply anything.
SETTLED = frozenset({"applied", "held", "approved", "rejected", "failed", "uncommitted"})
AWAITING = frozenset({"held", "approved", "uncommitted"})  # what `sync` looks at


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Record:
    fingerprint: str
    type: str
    employee_ref: str
    field: str
    change_class: str
    action: str
    status: str
    first_run: str
    last_run: str
    detail: str | None = None
    warrant_id: str | None = None
    approval_id: str | None = None
    requested_of: str | None = None
    decided_by: str | None = None
    violations: list[dict] = field(default_factory=list)
    result: dict | None = None
    updated_at: str = ""


class State:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.sql = queries(SQL_PATH)
        self.con = sqlite3.connect(path)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(self.sql["schema"])

    def __enter__(self) -> State:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.con.close()

    @staticmethod
    def _record(row: sqlite3.Row) -> Record:
        data = dict(row)
        data["violations"] = json.loads(data["violations"] or "[]")
        data["result"] = json.loads(data["result"]) if data["result"] else None
        return Record(**data)

    def get(self, fingerprint: str) -> Record | None:
        row = self.con.execute(self.sql["get_mismatch"], {"fingerprint": fingerprint}).fetchone()
        return self._record(row) if row else None

    def all(self) -> list[Record]:
        return [self._record(r) for r in self.con.execute(self.sql["all_mismatches"])]

    def save(self, record: Record) -> Record:
        if record.status not in STATUSES:
            raise ValueError(f"unknown status {record.status!r}")
        record.updated_at = now()
        row = asdict(record)
        row["violations"] = json.dumps(record.violations)
        row["result"] = json.dumps(record.result, sort_keys=True) if record.result else None
        with self.con:
            self.con.execute(self.sql["save_mismatch"], row)
        return record

    def start_run(self, run_id: str, principal: str) -> None:
        with self.con:
            self.con.execute(self.sql["start_run"],
                             {"run_id": run_id, "started_at": now(), "principal": principal})

    def finish_run(self, run_id: str, outcome: str, detail: str | None,
                   report: Path | None) -> None:
        with self.con:
            self.con.execute(self.sql["finish_run"], {
                "run_id": run_id, "finished_at": now(), "outcome": outcome, "detail": detail,
                "report": str(report) if report else None})

    def runs(self) -> list[dict]:
        return [dict(r) for r in self.con.execute(self.sql["all_runs"])]
