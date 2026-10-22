"""Drift command line. It calls the same functions as app.py.

Commands are added as their BUILD steps land: `detect` (Step 5), `run` / `approve` / `sync`
(Step 8), `demo` (Step 11).
"""

import sys


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    command = args[0] if args else "(none)"
    print(f"drift: command {command!r} is not built yet — see BUILD.md.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
