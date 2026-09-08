"""Deterministic, offline CI triage. Returned actions are proposals, never executed."""
import json
from pathlib import Path

_RULESET_PATH = Path(__file__).resolve().parents[1] / "config" / "org-ruleset.yaml"
# JSON is a YAML subset; no YAML parser, network, ML, or optional dependencies.
RULESET = json.loads(_RULESET_PATH.read_text(encoding="utf-8-sig"))
_CONCLUSIONS = frozenset({
    "success", "failure", "timed_out", "cancelled", "action_required",
    "neutral", "skipped", "stale", "startup_failure",
})


def _result(category: str, action: str, rule: str, reason: str) -> dict:
    return {"category": category, "action": action,
            "rule": f"{RULESET['version']}:{rule}", "reason": reason}


def _match(evidence: list[str], signatures: list[str]) -> bool:
    return any(signature in line.casefold() for line in evidence for signature in signatures)


def classify(event: dict) -> dict:
    """Return a proposal from literal evidence and observed history, never speculation.

    History contains prior run outcomes: True=success, False=failure. Thresholds
    use the supplied sample only; no missing history is synthesized. Security
    takes precedence over a success conclusion. Reasons never echo raw evidence.
    """
    if not isinstance(event, dict):
        return _result("unknown", "investigate", "invalid-event", "Manual review: event must be an object.")
    evidence = event.get("evidence")
    history = event.get("history")
    conclusion = event.get("conclusion")
    if not isinstance(evidence, list) or not all(isinstance(line, str) for line in evidence):
        return _result("unknown", "investigate", "invalid-evidence", "Manual review: evidence must be a list of strings.")
    # Concrete security evidence remains actionable even when other metadata is bad.
    if _match(evidence, RULESET["security_signatures"]):
        return _result("security", "escalate", "security-literal", "Security signature found; propose human escalation, not retry.")
    if not isinstance(conclusion, str) or conclusion not in _CONCLUSIONS:
        return _result("unknown", "investigate", "invalid-conclusion", "Manual review: conclusion is missing or unsupported.")
    if not isinstance(history, list) or not all(type(outcome) is bool for outcome in history):
        return _result("unknown", "investigate", "invalid-history", "Manual review: history must be a list of boolean outcomes.")
    if conclusion == "success":
        return _result("healthy", "none", "successful-run", "Successful run; no action proposed.")
    if conclusion not in {"failure", "timed_out", "startup_failure"}:
        return _result("unknown", "investigate", "nonfailure-conclusion", "Nonfailure terminal conclusion; propose manual review, not retry.")
    if _match(evidence, RULESET["config_signatures"]):
        return _result("config", "investigate", "configuration-literal", "Configuration signature found; propose configuration review.")
    count = len(history)
    successes = sum(history)
    if count >= RULESET["systemic_min_samples"] and successes == 0:
        return _result("systemic", "investigate", "all-observed-failures", "At least ten observed runs all failed; propose systemic investigation.")
    if count >= RULESET["flaky_min_samples"]:
        rate = successes / count
        if RULESET["flaky_success_rate_min"] <= rate <= RULESET["flaky_success_rate_max"]:
            return _result("flaky", "retry", "observed-mixed-history", "Observed success rate is within the configured flaky range; propose a bounded retry.")
    if _match(evidence, RULESET["transient_signatures"]):
        return _result("transient", "retry", "transient-literal", "Explicit transient signature found; propose a bounded retry.")
    return _result("unknown", "investigate", "insufficient-evidence", "Insufficient evidence for a known category; propose manual review.")
