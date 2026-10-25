"""A plain-English explanation of each mismatch: display text only.

Nothing here decides anything. The class, the approver and whether a fix is applied come from
classify.py and change_classes.yaml; an explanation is shown next to them and cannot change them.

With credentials, each mismatch gets one call to the Claude API (`DRIFT_MODEL`). The model sees
only `payload()`: the type, the field, the employee ref, the two values (pay replaced by a
percentage, names and emails withheld), the effective dates and the payroll cutoff. Answers are
cached by fingerprint in state/explanations.json.

With no credentials — or when a call fails, is refused, or returns something unusable — a
deterministic template per type is used instead.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import anthropic

from drift import classify
from drift.constants import PAYROLL_CUTOFF
from drift.detect import Mismatch

ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = ROOT / "state" / "explanations.json"
DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_CAUSES = 3
RECENT = timedelta(days=60)  # a comp change this close to the cutoff may not have synced yet

# Models that accept server-side refusal fallback in its "default" form (Claude API).
FALLBACK_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"

WITHHELD = "withheld (personal data)"
PERSONAL_FIELDS = {"legal_name", "work_email"}  # their values are never sent
FIELD_NAMES = {
    "worker_status": "Employment status", "pay": "Annual pay", "cost_center": "Cost centre",
    "manager": "Manager", "record": "Payroll record", "legal_name": "Legal name",
    "location": "Location", "work_email": "Work email", "rate_type": "Rate type",
}

# An explanation may not tell anyone to route around the hold. Output containing any of these is
# discarded in favour of the template.
BYPASS_PHRASES = (
    "bypass", "skip the approval", "skip approval", "skip review", "without approval",
    "without waiting", "override the hold", "ignore the hold", "edit adp directly",
    "update adp directly", "change it directly in adp",
)

SYSTEM_PROMPT = """\
You explain discrepancies found when reconciling Workday (the HR system of record for people and \
organisation) against ADP (payroll), for an HR operations reader who is not technical.

You are advisory only. A separate, deterministic process has already decided how this discrepancy \
is handled — corrected automatically, or held for a named person to approve — and nothing you \
write changes that. Never suggest bypassing, skipping or hurrying past review or approval, and \
never tell anyone to edit payroll directly.

You get one discrepancy as JSON. Personal data has been removed on purpose: the employee is an ID, \
pay is a percentage difference, and names and emails are withheld. Do not ask for them or guess \
them.

The payroll cutoff is the last date a change could reach the most recent pay run; a change dated \
after it may simply not have arrived yet.

Return:
- explanation: one or two plain sentences on what disagrees and why it matters.
- likely_causes: up to three short, specific causes, most likely first.
- suggested_owner: the team that should look at it, for example Payroll, HR Operations, Total \
Rewards, or IT Application Engineering (integration)."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "explanation": {"type": "string"},
        "likely_causes": {"type": "array", "items": {"type": "string"}},
        "suggested_owner": {"type": "string"},
    },
    "required": ["explanation", "likely_causes", "suggested_owner"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Explanation:
    explanation: str
    likely_causes: tuple[str, ...]
    suggested_owner: str
    source: str  # "llm" or "template"


@dataclass(frozen=True)
class Explained:
    by_fingerprint: dict[str, Explanation]
    note: str  # how they were produced, for the report


# ── what the model may see ───────────────────────────────────────────────────────────────────


def annual_dollars(value: str | None) -> Decimal | None:
    """'154000.00/yr' (detect.sql's format) → Decimal('154000.00')."""
    return Decimal(value.removesuffix("/yr")) if value else None


def pay_difference(workday: str | None, adp: str | None) -> str:
    wd, ad = annual_dollars(workday), annual_dollars(adp)
    if wd is None or ad is None:
        return f"only {'Workday' if wd is not None else 'ADP'} has a pay figure"
    if ad == 0:
        return "ADP's pay figure is zero"
    pct = (wd - ad) / ad * 100
    return f"Workday is {abs(pct):.1f}% {'higher' if pct > 0 else 'lower'} than ADP"


def name_difference(workday: str | None, adp: str | None) -> str:
    wd, ad = (workday or "").split(), (adp or "").split()
    if wd[:1] == ad[:1] and wd[-1:] != ad[-1:]:
        return "first name matches; surname differs"
    if wd[-1:] == ad[-1:] and wd[:1] != ad[:1]:
        return "surname matches; first name differs"
    return "the legal names differ"


def payload(m: Mismatch) -> dict[str, str | None]:
    """Everything the model sees about a mismatch — built from an allow-list, never from a Pair."""
    out: dict[str, str | None] = {
        "type": f"{m.type} · {classify.label(m.type)}",
        "field": m.field,
        "employee_ref": m.employee_ref,
        "workday_value": m.workday_value,
        "adp_value": m.adp_value,
        "org_effective_date": m.org_effective_date.isoformat() if m.org_effective_date else None,
        "comp_effective_date": (
            m.comp_effective_date.isoformat() if m.comp_effective_date else None),
        "payroll_cutoff": PAYROLL_CUTOFF.isoformat(),
    }
    if m.field == "pay":
        out["difference"] = pay_difference(m.workday_value, m.adp_value)
        out["workday_value"] = out["adp_value"] = "withheld (pay)"
    elif m.field in PERSONAL_FIELDS:
        if m.field == "legal_name":
            out["difference"] = name_difference(m.workday_value, m.adp_value)
        out["workday_value"] = out["adp_value"] = WITHHELD
    return out


# ── templates ────────────────────────────────────────────────────────────────────────────────


def _date_in(value: str | None) -> str | None:
    found = re.search(r"\d{4}-\d{2}-\d{2}", value or "")
    return found.group(0) if found else None


def template(m: Mismatch) -> Explanation:
    p = payload(m)
    ref, cutoff = m.employee_ref, PAYROLL_CUTOFF.isoformat()
    wd, adp = p["workday_value"] or "nothing", p["adp_value"] or "nothing"

    if m.type == "M1":
        when = _date_in(m.workday_value) or "an unrecorded date"
        late = when > cutoff
        text = (f"Workday shows {ref} terminated on {when}, but ADP still has them as {adp}, so "
                f"they may still be paid.")
        causes = ["Termination not sent by the Workday → ADP integration",
                  "Termination entered in Workday but the payroll step was missed"]
        if late:
            text += f" The termination is dated after the {cutoff} payroll cutoff."
            causes.insert(0, "Terminated after the payroll cutoff — may simply be timing")
        return Explanation(text, tuple(causes[:MAX_CAUSES]), "Payroll (with HR Operations)",
                           "template")
    if m.type == "M2":
        causes = ["Pay keyed differently in ADP (for example a typo)",
                  "Rate updated in one system only"]
        recent = m.comp_effective_date and m.comp_effective_date >= PAYROLL_CUTOFF - RECENT
        if recent:
            causes.insert(0, f"Workday pay change effective {m.comp_effective_date} did not "
                             f"reach ADP")
        return Explanation(f"Annual pay for {ref} disagrees: {p['difference']}.",
                           tuple(causes[:MAX_CAUSES]), "Payroll (with Total Rewards)", "template")
    if m.type in ("M3", "M4"):
        what = "cost centre" if m.type == "M3" else "manager"
        causes = [f"Org change effective {m.org_effective_date} did not sync to ADP"
                  if m.org_effective_date else "Org change did not sync to ADP",
                  "Updated in Workday only"]
        if m.type == "M3":
            causes.append("Cost-centre mapping missing or out of date")
        return Explanation(f"Workday and ADP record a different {what} for {ref}: {wd} in "
                           f"Workday, {adp} in ADP.", tuple(causes[:MAX_CAUSES]),
                           "HR Operations (with IT Application Engineering for the integration)",
                           "template")
    if m.type == "M5":
        hired = _date_in(m.workday_value) or "an unrecorded date"
        text = (f"{ref} is active in Workday (hired {hired}) but has no ADP record, so they may "
                f"not be paid.")
        causes = ["New hire not yet set up in payroll", "Hire not sent by the integration"]
        if hired > cutoff:
            text += f" The hire is dated after the {cutoff} payroll cutoff."
            causes.insert(0, "Hired after the payroll cutoff — may simply be timing")
        return Explanation(text, tuple(causes[:MAX_CAUSES]), "Payroll", "template")
    if m.type == "M6":
        return Explanation(
            f"ADP has an {adp} payroll record ({ref}) that matches no Workday worker.",
            ("Worker removed from Workday while the payroll record stayed active",
             "Record created directly in ADP",
             "Possible ghost record — verify before any change"),
            "Payroll (with Internal Audit)", "template")
    if m.type == "M7":
        return Explanation(f"The legal name for {ref} differs between Workday and ADP "
                           f"({p['difference']}).",
                           ("Legal name change recorded in Workday only", "Typo in one system"),
                           "HR Operations", "template")
    field = FIELD_NAMES.get(m.field, m.field)
    return Explanation(f"{field} differs for {ref} ({wd} in Workday, {adp} in ADP), and no "
                       f"specific rule explains it.",
                       ("Updated in one system only",
                        "Not covered by the integration's field mapping"),
                       "HR Operations (triage)", "template")


# ── the API ──────────────────────────────────────────────────────────────────────────────────


def default_client() -> anthropic.Anthropic | None:
    """A client only when credentials are explicitly configured; otherwise templates."""
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return anthropic.Anthropic()
    return None


def request(m: Mismatch, model: str) -> dict:
    """The keyword arguments for one Messages API call."""
    kwargs: dict = {
        "model": model,
        "max_tokens": 2048,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": "Discrepancy:\n" + json.dumps(
            payload(m), indent=2, sort_keys=True)}],
        "output_config": {"effort": "low",
                          "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
    }
    if model in FALLBACK_MODELS:
        kwargs |= {"betas": [FALLBACK_BETA], "fallbacks": "default"}
    return kwargs


def parse(response) -> Explanation | None:
    """A usable explanation from a response, or None (refused, truncated, malformed, unsafe)."""
    if getattr(response, "stop_reason", None) != "end_turn":
        return None
    text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), None)
    try:
        data = json.loads(text or "")
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    explanation, causes, owner = (data.get("explanation"), data.get("likely_causes"),
                                  data.get("suggested_owner"))
    if not (isinstance(explanation, str) and explanation.strip() and isinstance(owner, str)
            and owner.strip() and isinstance(causes, list)
            and all(isinstance(c, str) for c in causes)):
        return None
    blob = " ".join([explanation, owner, *causes]).lower()
    if any(phrase in blob for phrase in BYPASS_PHRASES):
        return None
    return Explanation(explanation.strip(), tuple(c.strip() for c in causes if c.strip())[
        :MAX_CAUSES], owner.strip(), "llm")


def _load_cache(path: Path) -> dict[str, dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cache(path: Path, cache: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def explain(mismatches: Iterable[Mismatch], *, client=None, model: str | None = None,
            cache_path: Path = CACHE_PATH) -> Explained:
    """An explanation for every mismatch. Pass `client=None` to use credentials from the
    environment if there are any."""
    mismatches = list(mismatches)
    model = model or os.environ.get("DRIFT_MODEL") or DEFAULT_MODEL
    client = client if client is not None else default_client()
    cache = _load_cache(cache_path)
    out: dict[str, Explanation] = {}
    counts = {"cached": 0, "llm": 0, "template": 0}
    stopped: str | None = None

    for m in mismatches:
        hit = cache.get(m.fingerprint)
        if hit and hit.get("source") == "llm":
            out[m.fingerprint] = Explanation(hit["explanation"], tuple(hit["likely_causes"]),
                                             hit["suggested_owner"], "llm")
            counts["cached"] += 1
            continue
        result = None
        if client is not None and stopped is None:
            kwargs = request(m, model)
            api = client.beta.messages if "fallbacks" in kwargs else client.messages
            try:
                result = parse(api.create(**kwargs))
            except (anthropic.AuthenticationError, anthropic.PermissionDeniedError,
                    anthropic.NotFoundError, anthropic.BadRequestError) as e:
                stopped = type(e).__name__  # the same error would repeat for every mismatch
            except (anthropic.RateLimitError, anthropic.APIStatusError,
                    anthropic.APIConnectionError):
                pass  # the SDK has already retried; this one gets the template
        if result is not None:
            cache[m.fingerprint] = asdict(result) | {"model": model}
            _save_cache(cache_path, cache)
            counts["llm"] += 1
        else:
            result = template(m)
            counts["template"] += 1
        out[m.fingerprint] = result

    if client is None:
        note = "templates (no Anthropic credentials configured)"
        if counts["cached"]:
            note += f"; {counts['cached']} cached from an earlier run"
    else:
        note = (f"{model}: {counts['llm']} new, {counts['cached']} cached, "
                f"{counts['template']} from templates")
        if stopped:
            note += f" (API calls stopped after {stopped})"
    return Explained(out, note)
