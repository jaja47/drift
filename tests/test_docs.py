"""Step 13: the documentation says what the code does."""

from __future__ import annotations

import json
import re
from pathlib import Path

from drift.cli import detection_from
from drift.connectors.csv_source import DEFAULT_DIR
from drift.govern import warrant_request

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
ARCHITECTURE = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")


def test_the_readme_says_what_the_ledger_records() -> None:
    """GATE B: a refused request leaves no event. PLAN.md's "every … refusal … lands in the
    ledger" must not be repeated."""
    assert "A refused request leaves no event" in README
    assert not re.search(r"every action, refusal", README, re.I)


def test_every_make_target_the_readme_names_exists() -> None:
    targets = set(re.findall(r"^([a-z]+):", (ROOT / "Makefile").read_text(), re.M))
    named = set(re.findall(r"`make ([a-z]+)", README))
    assert named and named <= targets, named - targets


def test_the_readme_images_exist() -> None:
    images = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", README)
    assert images
    for image in images:
        assert (ROOT / image).is_file(), image


def test_the_documented_warrant_requests_are_the_ones_drift_sends() -> None:
    documented = [json.loads(block) for block in
                  re.findall(r"```json\n(.*?)```", ARCHITECTURE, re.S)]
    mismatches = {m.employee_ref: m for m in detection_from(DEFAULT_DIR).mismatches}
    assert [d["object"] for d in documented] == ["W10015", "W10112"]
    for d in documented:
        assert d == warrant_request(mismatches[d["object"]], principal=d["principal"],
                                    run_id=d["context"]["run"])
