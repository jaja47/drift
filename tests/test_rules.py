"""CLAUDE.md rules that can be checked mechanically, kept in one place as the build grows."""

from pathlib import Path

DRIFT = Path(__file__).resolve().parent.parent / "drift"


def test_the_detector_never_reads_planted_json() -> None:
    """Ground truth is for scoring. Code under drift/ must not even name the file."""
    offenders = [p.relative_to(DRIFT.parent) for p in DRIFT.rglob("*")
                 if p.suffix in (".py", ".sql") and "planted" in p.read_text(encoding="utf-8")]
    assert offenders == []
