"""Step 1 checks: the package imports, and the vendored Warrant carries no local state."""

from pathlib import Path

import drift

ROOT = Path(__file__).resolve().parent.parent
WARRANT = ROOT / "warrant"


def test_package_imports() -> None:
    assert drift.__version__ == "0.1.0"


def test_vendored_warrant_has_no_local_state() -> None:
    assert (WARRANT / "src" / "server.ts").is_file()
    assert not (WARRANT / ".git").exists()
    assert not (WARRANT / ".env").exists()
    assert not list(WARRANT.rglob("*.jsonl")), "a ledger file was copied in or written into warrant/"


def test_vendored_warrant_records_its_origin() -> None:
    vendored = (WARRANT / "VENDORED.md").read_text()
    assert "Source commit" in vendored
    assert "## Changes since copy" in vendored
