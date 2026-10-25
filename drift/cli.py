"""Drift command line. It calls the same functions as app.py; only the printing lives here.

    python -m drift.cli detect [--source DIR] [--explain]

`run` / `approve` / `sync` arrive in Step 8, `demo` in Step 11.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from drift import classify
from drift.connectors import CsvAdpSource, CsvWorkdaySource, read_reference
from drift.connectors.csv_source import DEFAULT_DIR
from drift.detect import Detection, detect_sources
from drift.explain import explain
from drift.patterns import find_patterns


def detection_from(directory: Path) -> Detection:
    return detect_sources(CsvWorkdaySource(directory / "workday_workers.csv"),
                          CsvAdpSource(directory / "adp_payroll.csv"),
                          read_reference(directory))


def table(header: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]
    line = lambda r: "  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip()  # noqa: E731
    return "\n".join([line(header), line(["-" * w for w in widths]), *map(line, rows)])


def cmd_detect(args: argparse.Namespace) -> int:
    d = detection_from(args.source)
    matched = Counter(p.match for p in d.pairs)
    classes = Counter(classify.change_class(m.type, m.field) for m in d.mismatches)
    print(f"Compared {len(d.pairs)} people from {args.source}: "
          f"{matched['both']} matched through the crosswalk, "
          f"{matched['workday_only']} only in Workday, {matched['adp_only']} only in ADP.")
    print(f"{len(d.mismatches)} mismatches — " + ", ".join(
        f"{classes[c]} {c}" for c in classify.CLASSES if classes[c]) + ".\n")
    patterns = find_patterns(d.mismatches, d.pairs)
    print("Patterns" if patterns else "Patterns: none (no group of 3 or more shares a cause).")
    for p in patterns:
        print(f"  • {p.diagnosis}\n    Suggested owner: {p.suggested_owner}")
    print()
    rows = [[m.type, classify.label(m.type), m.employee_ref, m.field,
             classify.change_class(m.type, m.field), m.workday_value or "—",
             m.adp_value or "—", str(m.org_effective_date or m.comp_effective_date or "")]
            for m in d.mismatches]
    print(table(["TYPE", "WHAT", "EMPLOYEE", "FIELD", "CLASS", "WORKDAY", "ADP", "EFFECTIVE"],
                rows))
    if args.explain:
        explained = explain(d.mismatches)
        print(f"\nExplanations — {explained.note}. Advisory only: they do not change what "
              f"happens.")
        for m in d.mismatches:
            e = explained.by_fingerprint[m.fingerprint]
            print(f"\n{m.type} {m.employee_ref}: {e.explanation}\n"
                  f"  Likely: {'; '.join(e.likely_causes)}\n  Owner: {e.suggested_owner}")
    return 0


def not_built(step: str):
    def run(args: argparse.Namespace) -> int:
        print(f"drift: `{args.command}` is not built yet — {step} (see BUILD.md).", file=sys.stderr)
        return 2
    return run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="drift", description="Workday ↔ ADP reconciler")
    sub = parser.add_subparsers(dest="command", required=True)
    detect = sub.add_parser("detect", help="compare the exports and print the mismatches")
    detect.add_argument("--source", type=Path, default=DEFAULT_DIR)
    detect.add_argument("--explain", action="store_true",
                        help="add a plain-English explanation per mismatch (advisory)")
    detect.set_defaults(run=cmd_detect)
    for name, step in [("run", "Step 8"), ("approve", "Step 8"), ("sync", "Step 8"),
                       ("demo", "Step 11")]:
        sub.add_parser(name).set_defaults(run=not_built(step))
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
