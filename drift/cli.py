"""Drift command line. It calls the same functions as app.py; only the printing lives here.

    python -m drift.cli detect  [--source DIR] [--explain]
    python -m drift.cli run     [--source DIR] [--state DIR] [--reports DIR]
    python -m drift.cli approve REF --as "NAME" --decision approved|rejected [--note TEXT]
    python -m drift.cli sync    [--source DIR] [--state DIR]

`run`, `approve` and `sync` need Warrant (WARRANT_URL, WARRANT_TOKEN) and a principal
(DRIFT_PRINCIPAL); `scripts/dev.sh` provides all three. `demo` arrives in Step 11.

Exit codes: 0 done; 1 refused or Warrant unreachable (nothing more was written); 2 usage or
configuration error (nothing was requested or written).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

from drift import classify, govern
from drift.connectors import CsvAdpSource, CsvWorkdaySource, read_reference
from drift.connectors.csv_source import ADP_EXPORT, DEFAULT_DIR, WORKDAY_EXPORT
from drift.detect import Detection, detect_sources
from drift.explain import explain
from drift.patterns import find_patterns
from drift.report import STATUS_ORDER, STATUS_WORDS, verify_line


def detection_from(directory: Path) -> Detection:
    return detect_sources(CsvWorkdaySource(directory / WORKDAY_EXPORT),
                          CsvAdpSource(directory / ADP_EXPORT), read_reference(directory))


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


def settings(args: argparse.Namespace) -> govern.Settings | None:
    principal = " ".join(os.environ.get("DRIFT_PRINCIPAL", "").split())
    if not principal:
        print("drift: DRIFT_PRINCIPAL is not set — a run needs the named person who authorised "
              "it. Run through scripts/dev.sh (make run), which reads it from .env.",
              file=sys.stderr)
        return None
    return govern.Settings(principal=principal, source_dir=args.source,
                           state_dir=args.state, reports_dir=args.reports)


def cmd_run(args: argparse.Namespace) -> int:
    s = settings(args)
    if s is None:
        return 2
    client = govern.WarrantClient.from_env()
    try:
        r = govern.run(s, client)
    except classify.ConfigError as e:
        print(f"drift: {e}. Nothing was requested or written.", file=sys.stderr)
        return 2
    finally:
        client.close()

    statuses = Counter(rec.status for rec in r.records)
    print(f"{r.run_id} — principal {r.principal}, Warrant {r.warrant_url}")
    print(f"Compared {len(r.detection.pairs)} people: {len(r.records)} mismatches, "
          f"{len(r.already)} already handled by an earlier run.\n")
    for status in STATUS_ORDER:
        if statuses[status]:
            print(f"  {statuses[status]:>3}  {STATUS_WORDS[status]}")
    if r.outcome != "completed":
        print(f"\nFAIL CLOSED — {r.detail}. Nothing more was written.")
    if r.patterns:
        print("\nPatterns")
        for p in r.patterns:
            print(f"  • {p.diagnosis}\n    Suggested owner: {p.suggested_owner}")
    held = [rec for rec in r.records if rec.status in ("held", "approved")]
    if held:
        print("\nHeld for approval — nothing written until the named person decides:")
        print(table(["EMPLOYEE", "TYPE", "WHAT", "CLASS", "WAITING ON", "STATUS"], [
            [rec.employee_ref, rec.type, classify.label(rec.type), rec.change_class,
             str(classify.approver(rec.change_class) or rec.requested_of), rec.status]
            for rec in held]))
        print('\nDecide with: python -m drift.cli approve <EMPLOYEE> --as "<name>" '
              "--decision approved|rejected, then `sync`.")
    print(f"\nLedger: {verify_line(r.verify)}")
    print(f"Report: {r.report}")
    return 0 if r.outcome == "completed" else 1


def cmd_approve(args: argparse.Namespace) -> int:
    s = settings(args)
    if s is None:
        return 2
    client = govern.WarrantClient.from_env()
    try:
        d = govern.approve(s, client, args.ref, args.approver, args.decision, args.note)
    except govern.WarrantUnreachable as e:
        print(f"drift: {e}. No decision was recorded.", file=sys.stderr)
        return 1
    finally:
        client.close()
    what = f"{d.record.employee_ref} {d.record.field}: " if d.record else ""
    print(f"{what}{d.message}", file=sys.stdout if d.ok else sys.stderr)
    for v in d.violations:
        print(f"  {v.get('field')}: {v.get('message')}", file=sys.stderr)
    return 0 if d.ok else 1


def cmd_sync(args: argparse.Namespace) -> int:
    s = settings(args)
    if s is None:
        return 2
    client = govern.WarrantClient.from_env()
    try:
        r = govern.sync(s, client)
    finally:
        client.close()
    for rec in r.changed:
        print(f"  {rec.employee_ref:<15} {rec.type}  {rec.status:<9} {rec.detail or ''}")
    print(f"{len(r.changed)} moved on, {len(r.waiting)} still waiting.")
    if r.outcome != "completed":
        print(f"FAIL CLOSED — {r.detail}. Nothing more was written.", file=sys.stderr)
        return 1
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

    def governed(name: str, help: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help)
        p.add_argument("--source", type=Path, default=DEFAULT_DIR)
        p.add_argument("--state", type=Path, default=govern.STATE_DIR)
        p.add_argument("--reports", type=Path, default=govern.REPORTS_DIR)
        return p

    governed("run", "detect, then govern every mismatch through Warrant").set_defaults(
        run=cmd_run)
    approve = governed("approve", "decide a held correction, as the named approver")
    approve.add_argument("ref", help="employee ref (e.g. W10026) or fingerprint prefix")
    approve.add_argument("--as", dest="approver", required=True, help="the approver's name")
    approve.add_argument("--decision", required=True, choices=["approved", "rejected"])
    approve.add_argument("--note")
    approve.set_defaults(run=cmd_approve)
    governed("sync", "apply + commit held corrections that have been approved").set_defaults(
        run=cmd_sync)
    sub.add_parser("demo").set_defaults(run=not_built("Step 11"))
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
