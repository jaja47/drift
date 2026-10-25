"""Step 10: the review page, driven headlessly (Streamlit's AppTest) against a real Warrant.

The main test is BUILD.md's "done when": the whole demo, done from the page — compare, open a
warrant, see a termination held, be refused as the principal, approve as the named person, see it
applied, read the pattern and the audit check — with no terminal.
"""

from __future__ import annotations

import csv
import re
import tomllib
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from drift import govern, review
from drift.cli import detection_from
from drift.connectors.csv_source import DEFAULT_DIR
from drift.state import State

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "drift" / "app.py")
PRINCIPAL = "Dana Okafor"
DETECTION = detection_from(DEFAULT_DIR)
FP = {(m.type, m.employee_ref): m.fingerprint for m in DETECTION.mismatches}


@pytest.fixture
def page(warrant, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WARRANT_URL", warrant.url)
    monkeypatch.setenv("WARRANT_TOKEN", warrant.token)
    monkeypatch.setenv("DRIFT_PRINCIPAL", PRINCIPAL)
    monkeypatch.setenv("DRIFT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DRIFT_REPORTS_DIR", str(tmp_path / "reports"))
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(key, raising=False)  # templates only: no live API call from tests
    return AppTest.from_file(APP, default_timeout=60)


def text(at: AppTest) -> str:
    return "\n".join(str(e.value) for kind in ("markdown", "success", "error", "info", "caption")
                     for e in getattr(at, kind))


def expander(at: AppTest, prefix: str):
    (found,) = [e for e in at.expander if e.label.startswith(prefix)]
    return found


def inside(at: AppTest, prefix: str) -> str:
    return "\n".join(m.value for m in expander(at, prefix).markdown)


def metrics(at: AppTest) -> dict[str, str]:
    return {m.label: m.value for m in at.metric}


def decide(at: AppTest, key: tuple[str, str], who: str, button: str) -> AppTest:
    fp = FP[key]
    at.text_input(key=f"who-{fp}").input(who).run()
    return at.button(key=f"{button}-{fp}").click().run()


def adp_row(tmp_path: Path, associate: str) -> dict[str, str]:
    path = tmp_path / "state" / "adp_current.csv"
    with path.open(newline="", encoding="utf-8") as f:
        return next(r for r in csv.DictReader(f) if r["Associate_ID"] == associate)


def test_before_a_run_the_page_shows_the_banner_and_one_button(page, tmp_path: Path) -> None:
    at = page.run()
    assert not at.exception
    assert [i.value for i in at.info] == [
        "Synthetic data. No real Workday or ADP system is connected."]
    assert [b.label for b in at.button] == ["Compare Workday and ADP"]
    assert PRINCIPAL in text(at)
    assert "Audit log: verified ✓" in text(at)
    assert not (tmp_path / "state").exists()  # looking at the page writes nothing


def test_the_whole_demo_can_be_done_from_the_page(page, tmp_path: Path) -> None:
    # 1 · Compare
    at = page.run().button(key="run").click().run()
    assert not at.exception
    assert metrics(at) == {"Employees compared": "201", "Mismatches": "18",
                           "Fixed automatically": "8", "Waiting for approval": "10",
                           "Format differences ignored": "4"}
    ignored = inside(at, "Format differences ignored")
    assert all(ref in ignored for ref in ("W10022", "W10025", "W10054", "W10137"))
    table = at.dataframe[0].value
    assert len(table) == 18
    assert list(table.columns) == ["Employee", "What differs", "Class", "Status", "Approver"]
    assert list(table["Status"][:10]) == ["Waiting for approval"] * 10  # what needs a person first
    assert len([t for t in at.text_input if t.key.startswith("who-")]) == 10  # held rows only

    # 2 · a routine fix, opened: its warrant, purpose and sources
    w10015 = inside(at, "W10015")
    assert "committed" in w10015 and "none needed — routine" in w10015
    assert "Change the ADP home department for W10015" in w10015
    assert "workday_workers.csv#Employee_ID=W10015" in w10015

    # 3 · a terminated employee still active in payroll: held, untouched
    held = inside(at, "W10112")
    assert "No warrant exists until Owen Castellanos decides" in held
    assert "Owen Castellanos (Payroll Operations Lead)" in held
    assert adp_row(tmp_path, "GPTEGMZWN7")["Position_Status"] == "A"

    at = decide(at, ("M1", "W10112"), PRINCIPAL, "approve")
    assert any("second person" in e.value for e in expander(at, "W10112").error)  # in the row
    assert adp_row(tmp_path, "GPTEGMZWN7")["Position_Status"] == "A"

    at = decide(at, ("M1", "W10112"), "Owen Castellanos", "approve")
    done = expander(at, "W10112")  # now under "Everything else", open, with the outcome
    assert done.label.endswith("— Fixed")
    assert any("Approved by Owen Castellanos — applied" in s.value
               and "Position_Status: A → T" in s.value for s in done.success)
    assert [t.value for t in at.toast] == ["Approved by Owen Castellanos — applied."]
    row = adp_row(tmp_path, "GPTEGMZWN7")
    assert (row["Position_Status"], row["Termination_Date"]) == ("T", "2026-09-18")
    assert metrics(at)["Waiting for approval"] == "9"
    assert metrics(at)["Fixed automatically"] == "8"  # an approved fix is not an automatic one
    assert "1 more fixed after a named person approved" in text(at)
    assert "approved by Owen Castellanos" in inside(at, "W10112")

    # 4 · the pattern, before the rows
    assert "7 org changes share the effective date 2026-10-01" in text(at)
    assert [h.value for h in at.subheader][0] == "Root causes"

    # 5 · the audit check
    assert any(s.value.startswith("Audit log: verified ✓") for s in at.success)

    # it reads cleanly: no stray markdown, a readable time
    assert not any("****" in m.value for m in at.markdown)
    assert re.search(r"last run \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC", text(at))

    # and a rejection: never applied
    at = decide(at, ("M2", "W10076"), "Priya Raman", "reject")
    assert any("Rejected by Priya Raman" in s.value for s in at.success)
    assert "Rejected — left as it is" in list(at.dataframe[0].value["Status"])
    with State(tmp_path / "state" / "drift.db") as state:
        assert state.get(FP[("M2", "W10076")]).status == "rejected"
    assert not at.exception


def test_check_for_decisions_applies_one_made_elsewhere(page, warrant, tmp_path: Path) -> None:
    at = page.run().button(key="run").click().run()
    settings = review.settings_from_env()
    d = govern.approve(settings, govern.WarrantClient(warrant.url, warrant.token), "W10126",
                       "Owen Castellanos", "approved")  # through the CLI's code, not the page
    assert d.ok
    at = at.button(key="sync").click().run()
    assert any("1 fixed" in s.value for s in at.success)
    assert metrics(at)["Waiting for approval"] == "9"


def test_an_empty_name_decides_nothing(page) -> None:
    at = page.run().button(key="run").click().run()
    at = at.button(key=f"approve-{FP[('M1', 'W10112')]}").click().run()
    assert any("Type your full name" in e.value for e in at.error)
    assert metrics(at)["Waiting for approval"] == "10"


def test_with_warrant_down_the_page_fails_closed(page, warrant_factory, tmp_path: Path,
                                                 monkeypatch) -> None:
    w = warrant_factory()
    w.stop()
    monkeypatch.setenv("WARRANT_URL", w.url)
    at = page.run()
    assert any("audit service (Warrant) is unreachable" in e.value for e in at.error)
    at = at.button(key="run").click().run()
    assert any("gave no usable answer" in e.value for e in at.error)
    assert not (tmp_path / "state" / "adp_current.csv").exists()


def test_without_a_principal_the_page_refuses_to_run(page, monkeypatch) -> None:
    monkeypatch.delenv("DRIFT_PRINCIPAL")
    at = page.run()
    assert any("DRIFT_PRINCIPAL is not set" in e.value for e in at.error)
    assert len(at.button) == 0


def test_the_page_listens_on_this_machine_only_and_shows_no_developer_toolbar() -> None:
    config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert config["server"]["address"] == "localhost"
    assert config["server"]["showEmailPrompt"] is False
    assert config["browser"]["gatherUsageStats"] is False
    assert config["client"]["toolbarMode"] == "minimal"


# ── the plain words ──────────────────────────────────────────────────────────────────────────


def test_every_mismatch_is_described_in_plain_words() -> None:
    words = {(m.type, m.employee_ref): review.what(m) for m in DETECTION.mismatches}
    assert words[("M2", "W10148")] == "Annual pay: $154,000 in Workday, $147,500 in ADP."
    assert words[("M1", "W10112")] == "Workday: terminated 2026-09-18. ADP: still active."
    assert words[("M5", "W10199")] == "Workday: active hired 2026-10-05. ADP: no payroll record."
    assert words[("M3", "W10015")].startswith("Cost centre: CC-420 Fulfillment Network")
    assert all(w.endswith(".") for w in words.values())


@pytest.mark.parametrize("value, expected", [
    ("154000.00/yr", "$154,000"), ("58656.40/yr", "$58,656.40"), (None, "nothing")])
def test_money(value: str | None, expected: str) -> None:
    assert review.money(value) == expected
