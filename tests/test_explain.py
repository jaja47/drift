"""Step 7: what the LLM may see, how its answer is used, and the offline templates.

The LLM is replaced by a fake client throughout; nothing here makes a network call.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from drift import explain as ex
from drift.cli import detection_from
from drift.connectors.csv_source import DEFAULT_DIR
from drift.constants import PAYROLL_CUTOFF
from drift.detect import Detection, Mismatch

PLANTED = json.loads((DEFAULT_DIR / "planted.json").read_text(encoding="utf-8"))
ALLOWED_KEYS = {"type", "field", "employee_ref", "workday_value", "adp_value",
                "org_effective_date", "comp_effective_date", "payroll_cutoff", "difference"}


@pytest.fixture(scope="module")
def detection() -> Detection:
    return detection_from(DEFAULT_DIR)


@pytest.fixture(autouse=True)
def no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may reach the real API, whatever the developer's shell has set."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("DRIFT_MODEL", raising=False)

    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to construct a real Anthropic client")
    monkeypatch.setattr(anthropic, "Anthropic", refuse)


def by_id(detection: Detection, planted_id: str) -> Mismatch:
    (e,) = [e for e in PLANTED["mismatches"] if e["id"] == planted_id]
    (m,) = [m for m in detection.mismatches
            if (m.type, m.employee_ref) == (e["type"], e["employee_ref"])]
    return m


def extra_mismatches(detection: Detection) -> list[Mismatch]:
    """M8 rows the real data does not contain, carrying an email and a rate type."""
    base = detection.mismatches[0]
    return [replace(base, type="M8", field="work_email", workday_value="ada.lovelace@x.example",
                    adp_value="ada.byron@x.example"),
            replace(base, type="M8", field="location", workday_value="TOR", adp_value="CGY")]


# ── the payload: no names, emails or pay figures ─────────────────────────────────────────────


def test_payload_is_an_allow_list(detection: Detection) -> None:
    for m in detection.mismatches + extra_mismatches(detection):
        assert set(ex.payload(m)) <= ALLOWED_KEYS


def test_payload_never_carries_names_emails_or_pay(detection: Detection) -> None:
    pairs = {p.employee_ref: p for p in detection.pairs}
    for m in detection.mismatches + extra_mismatches(detection):
        sent = json.dumps(ex.payload(m))
        # emails
        assert "@" not in sent, m
        # pay: no amount with cents, and no bare number of 4+ digits outside dates and IDs
        assert not re.search(r"\d+\.\d\d", sent), (m.type, sent)
        assert not re.search(r"(?<![\w-])\d{4,}(?![\w-])", sent), (m.type, sent)
        # names — the person's own, from both systems
        p = pairs.get(m.employee_ref)
        for name in ([p.wd_name, p.adp_name] if p else []):
            for token in (name or "").split():
                assert not re.search(rf"\b{re.escape(token)}\b", sent, re.I), (m.type, token)


def test_pay_is_sent_as_a_percentage(detection: Detection) -> None:
    sent = ex.payload(by_id(detection, "M2-1"))  # 154000.00 in Workday vs 147500.00 in ADP
    assert sent["difference"] == "Workday is 4.4% higher than ADP"
    assert sent["workday_value"] == sent["adp_value"] == "withheld (pay)"


def test_a_name_mismatch_says_which_part_differs_without_the_name(detection: Detection) -> None:
    sent = ex.payload(by_id(detection, "M7-1"))
    assert sent["difference"] == "first name matches; surname differs"
    assert sent["workday_value"] == sent["adp_value"] == ex.WITHHELD


def test_payload_carries_dates_and_the_cutoff(detection: Detection) -> None:
    sent = ex.payload(by_id(detection, "M3-1"))
    assert sent["org_effective_date"] == PLANTED["reorg_effective_date"]
    assert sent["payroll_cutoff"] == PAYROLL_CUTOFF.isoformat()


# ── the call ─────────────────────────────────────────────────────────────────────────────────


def text_response(data: object, stop_reason: str = "end_turn"):
    body = data if isinstance(data, str) else json.dumps(data)
    return SimpleNamespace(stop_reason=stop_reason,
                           content=[SimpleNamespace(type="text", text=body)])


GOOD = {"explanation": "ADP still pays someone Workday has terminated.",
        "likely_causes": ["integration missed it", "manual step skipped", "timing", "fourth"],
        "suggested_owner": "Payroll"}


class FakeClient:
    """Records every call; answers with `respond(kwargs)`, which may also raise."""

    def __init__(self, respond):
        self.calls: list[dict] = []

        def create(**kwargs):
            self.calls.append(kwargs)
            return respond(kwargs)
        self.messages = SimpleNamespace(create=create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create))


def test_one_call_per_mismatch_with_only_the_payload(detection: Detection, tmp_path: Path) -> None:
    client = FakeClient(lambda kw: text_response(GOOD))
    run = ex.explain(detection.mismatches, client=client, cache_path=tmp_path / "x.json")
    assert len(client.calls) == 18
    for m, call in zip(detection.mismatches, client.calls):
        assert call["messages"] == [{"role": "user", "content": "Discrepancy:\n" + json.dumps(
            ex.payload(m), indent=2, sort_keys=True)}]
    assert all(e.source == "llm" for e in run.by_fingerprint.values())
    assert run.by_fingerprint[detection.mismatches[0].fingerprint].likely_causes == (
        "integration missed it", "manual step skipped", "timing")  # at most three


def test_the_request_is_advisory_structured_and_uses_drift_model(
        detection: Detection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRIFT_MODEL", "claude-sonnet-5-5")
    client = FakeClient(lambda kw: text_response(GOOD))
    ex.explain(detection.mismatches[:1], client=client, cache_path=tmp_path / "x.json")
    (call,) = client.calls
    assert call["model"] == "claude-sonnet-5-5"
    assert "advisory only" in call["system"]
    assert "Never suggest bypassing" in call["system"]
    assert call["output_config"]["format"] == {"type": "json_schema", "schema": ex.OUTPUT_SCHEMA}
    assert call["fallbacks"] == "default" and call["betas"] == [ex.FALLBACK_BETA]
    assert "thinking" not in call and "temperature" not in call


def test_answers_are_cached_by_fingerprint(detection: Detection, tmp_path: Path) -> None:
    cache = tmp_path / "state" / "explanations.json"
    ex.explain(detection.mismatches, client=FakeClient(lambda kw: text_response(GOOD)),
               cache_path=cache)
    assert set(json.loads(cache.read_text())) == {m.fingerprint for m in detection.mismatches}

    def must_not_call(kw):
        raise AssertionError("cached explanations must not be requested again")
    again = ex.explain(detection.mismatches, client=FakeClient(must_not_call), cache_path=cache)
    assert all(e.source == "llm" for e in again.by_fingerprint.values())
    assert "18 cached" in again.note


@pytest.mark.parametrize("respond", [
    lambda kw: text_response(GOOD, stop_reason="refusal"),
    lambda kw: text_response(GOOD, stop_reason="max_tokens"),
    lambda kw: text_response("not json"),
    lambda kw: text_response({"explanation": "", "likely_causes": [], "suggested_owner": "x"}),
    lambda kw: text_response({**GOOD, "likely_causes": "one string"}),
    lambda kw: text_response({**GOOD, "explanation": "Safe to bypass the approval and fix it."}),
    lambda kw: text_response({**GOOD, "likely_causes": ["Just update ADP directly"]}),
], ids=["refusal", "truncated", "not-json", "empty", "causes-not-a-list", "bypass",
        "edit-directly"])
def test_an_unusable_answer_falls_back_to_the_template_and_is_not_cached(
        detection: Detection, tmp_path: Path, respond) -> None:
    m = detection.mismatches[0]
    cache = tmp_path / "x.json"
    run = ex.explain([m], client=FakeClient(respond), cache_path=cache)
    assert run.by_fingerprint[m.fingerprint] == ex.template(m)
    assert not cache.exists()


def test_a_connection_error_falls_back_for_that_mismatch_only(detection: Detection,
                                                               tmp_path: Path) -> None:
    calls = iter([anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x")),
                  text_response(GOOD)])

    def respond(kw):
        nxt = next(calls)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt
    first, second = detection.mismatches[:2]
    run = ex.explain([first, second], client=FakeClient(respond), cache_path=tmp_path / "x.json")
    assert run.by_fingerprint[first.fingerprint].source == "template"
    assert run.by_fingerprint[second.fingerprint].source == "llm"


def test_an_auth_error_stops_further_calls(detection: Detection, tmp_path: Path) -> None:
    request = httpx2.Request("POST", "https://x")

    def respond(kw):
        raise anthropic.AuthenticationError(
            "bad key", response=httpx2.Response(401, request=request), body=None)
    client = FakeClient(respond)
    run = ex.explain(detection.mismatches, client=client, cache_path=tmp_path / "x.json")
    assert len(client.calls) == 1
    assert all(e.source == "template" for e in run.by_fingerprint.values())
    assert "stopped after AuthenticationError" in run.note


# ── offline ──────────────────────────────────────────────────────────────────────────────────


def test_without_credentials_every_mismatch_gets_a_template(detection: Detection,
                                                            tmp_path: Path) -> None:
    run = ex.explain(detection.mismatches, cache_path=tmp_path / "x.json")
    assert run.note.startswith("templates (no Anthropic credentials configured)")
    again = ex.explain(detection.mismatches, cache_path=tmp_path / "x.json")
    assert run == again  # deterministic
    for m in detection.mismatches:
        e = run.by_fingerprint[m.fingerprint]
        assert e.source == "template" and e.explanation and e.suggested_owner
        assert 1 <= len(e.likely_causes) <= ex.MAX_CAUSES
    assert not (tmp_path / "x.json").exists()


def test_every_type_has_a_template_that_names_no_one(detection: Detection) -> None:
    pairs = {p.employee_ref: p for p in detection.pairs}
    for m in detection.mismatches + extra_mismatches(detection):
        e = ex.template(m)
        text = " ".join([e.explanation, *e.likely_causes, e.suggested_owner])
        assert "@" not in text and not re.search(r"\d+\.\d\d", text)
        p = pairs.get(m.employee_ref)
        if p and m.field == "legal_name":
            assert p.wd_name not in text and p.adp_name not in text


def test_templates_flag_changes_dated_after_the_cutoff(detection: Detection) -> None:
    late_termination = by_id(detection, "M1-3")  # 2026-10-13
    late_hire = by_id(detection, "M5-2")  # 2026-10-14
    on_time = by_id(detection, "M1-1")  # 2026-09-18
    for m in (late_termination, late_hire):
        assert "after the 2026-10-09 payroll cutoff" in ex.template(m).explanation
        assert "timing" in ex.template(m).likely_causes[0]
    assert "cutoff" not in ex.template(on_time).explanation


def test_an_old_comp_date_is_not_offered_as_the_cause(detection: Detection) -> None:
    raise_on_1_oct = ex.template(by_id(detection, "M2-1"))
    transposed = ex.template(by_id(detection, "M2-2"))  # comp date 2019
    assert "2026-10-01" in raise_on_1_oct.likely_causes[0]
    assert "typo" in transposed.likely_causes[0]
