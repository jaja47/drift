"""Root causes: mismatches that share something, rolled up so the fix is the pipe, not the rows.

Mismatches are grouped by what they share — the org effective date, the comp effective date,
being dated after the payroll cutoff, the cost centre. Any group of at least PATTERN_MIN becomes
a pattern with a one-line diagnosis. A group whose mismatches an earlier pattern already explains
is dropped, so the reorg shows up once, not again as "five mismatches in CC-420".

Deterministic: no LLM. The diagnosis says what is likely; the rows still go through governance
one by one.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from drift.constants import PAYROLL_CUTOFF
from drift.detect import Mismatch
from drift.normalize import Pair

PATTERN_MIN = 3
FIELD_WORDS = {"cost_center": "cost centre", "manager": "manager", "pay": "pay"}


@dataclass(frozen=True)
class Pattern:
    kind: str  # org_date | comp_date | after_cutoff | cost_center
    key: str  # what the group shares
    fingerprints: tuple[str, ...]
    diagnosis: str
    suggested_owner: str

    @property
    def size(self) -> int:
        return len(self.fingerprints)


def event_date(m: Mismatch, pair: Pair | None) -> date | None:
    """The date of the change behind a mismatch, where there is one."""
    if m.org_effective_date or m.comp_effective_date:
        return m.org_effective_date or m.comp_effective_date
    if pair and m.type == "M5":
        return pair.hire_date
    if pair and m.field == "worker_status":
        return pair.wd_termination_date
    return None


def _breakdown(group: list[Mismatch]) -> str:
    counts = Counter(FIELD_WORDS.get(m.field, m.field) for m in group)
    return ", ".join(f"{n} {what}" for what, n in counts.most_common())


def find_patterns(mismatches: Iterable[Mismatch], pairs: Iterable[Pair]) -> list[Pattern]:
    mismatches = list(mismatches)
    by_ref = {p.employee_ref: p for p in pairs}
    cutoff = PAYROLL_CUTOFF

    def grouped(key) -> dict[str, list[Mismatch]]:
        out: dict[str, list[Mismatch]] = defaultdict(list)
        for m in mismatches:
            k = key(m)
            if k is not None:
                out[str(k)].append(m)
        return out

    def cost_center(m: Mismatch) -> str | None:
        p = by_ref.get(m.employee_ref)
        if p is None:
            return None
        return p.wd_cost_center or (f"ADP department {p.adp_department}"
                                    if p.adp_department else None)

    candidates: list[tuple[str, str, list[Mismatch], str, str]] = []
    for key, group in grouped(lambda m: m.org_effective_date).items():
        candidates.append((
            "org_date", key, group,
            f"{len(group)} org changes share the effective date {key} ({_breakdown(group)}) — "
            f"likely one reorg that did not reach ADP.",
            "IT Application Engineering (integration)"))
    for key, group in grouped(lambda m: m.comp_effective_date).items():
        candidates.append((
            "comp_date", key, group,
            f"{len(group)} pay mismatches share the comp effective date {key} — likely one pay "
            f"change, such as a merit cycle, that did not reach ADP.",
            "Payroll (with IT Application Engineering for the integration)"))
    late = [m for m in mismatches
            if (d := event_date(m, by_ref.get(m.employee_ref))) and d > cutoff]
    if late:
        candidates.append((
            "after_cutoff", cutoff.isoformat(), late,
            f"{len(late)} mismatches are dated after the {cutoff} payroll cutoff — likely "
            f"timing; check them again after the next payroll run.",
            "Payroll"))
    for key, group in grouped(cost_center).items():
        candidates.append((
            "cost_center", key, group,
            f"{len(group)} mismatches in {key} ({_breakdown(group)}) — check how that cost "
            f"centre is set up in ADP and the integration.",
            "HR Operations"))

    patterns: list[Pattern] = []
    explained: set[str] = set()
    for kind, key, group, diagnosis, owner in candidates:
        prints = tuple(m.fingerprint for m in group)
        if len(prints) < PATTERN_MIN or set(prints) <= explained:
            continue
        patterns.append(Pattern(kind, key, prints, diagnosis, owner))
        explained |= set(prints)
    return patterns
