"""CLAUDE.md rules that can be checked mechanically, kept in one place as the build grows."""

import re
from pathlib import Path

DRIFT = Path(__file__).resolve().parent.parent / "drift"


def test_the_detector_never_reads_planted_json() -> None:
    """Ground truth is for scoring. Code under drift/ must not even name the file."""
    offenders = [p.relative_to(DRIFT.parent) for p in DRIFT.rglob("*")
                 if p.suffix in (".py", ".sql") and "planted" in p.read_text(encoding="utf-8")]
    assert offenders == []


def test_sql_lives_in_detect_sql_not_in_python_strings() -> None:
    sql = re.compile(r"\b(SELECT|INSERT INTO|CREATE TABLE|UPDATE \w+ SET|DELETE FROM)\b")
    offenders = [p.relative_to(DRIFT.parent) for p in DRIFT.rglob("*.py")
                 if sql.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


def test_classification_does_not_depend_on_the_llm() -> None:
    """classify.py decides which changes need a human. It must not import the explainer or an
    LLM client, so nothing an LLM writes can reach that decision."""
    source = (DRIFT / "classify.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import|from)\s+(anthropic|drift\.explain|drift import explain)",
                         source, re.M)
