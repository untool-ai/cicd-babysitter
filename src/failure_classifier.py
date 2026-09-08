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


def _result(category: str, action: str, rule: str, reason: str, version: str) -> dict:
    return {"category": category, "action": action,
            "rule": f"{version}:{rule}", "reason": reason}


def _match(evidence: list[str], signatures: list[str]) -> bool:
    return any(signature.casefold() in line.casefold() for line in evidence for signature in signatures)


def _classification_rules(policy):
    if policy is not None and not isinstance(policy, dict):
        raise ValueError("Classification policy must be an object")
    # Partial policies retain defaults; explicit empty signature arrays remain empty.
    rules = {**RULESET, **(policy if policy is not None else {})}
    if not isinstance(rules.get("version"), str) or not rules["version"].strip():
        raise ValueError("Classification policy requires a version")
    for key in ("security_signatures", "config_signatures", "transient_signatures"):
        if not isinstance(rules[key], list) or not all(isinstance(item, str) and item.strip() for item in rules[key]):
            raise ValueError("Invalid classification signatures")
    for key in ("systemic_min_samples", "flaky_min_samples"):
        if type(rules[key]) is not int or rules[key] < 1:
            raise ValueError("Invalid classification sample threshold")
    low, high = rules["flaky_success_rate_min"], rules["flaky_success_rate_max"]
    if (type(low) not in (int, float) or type(high) not in (int, float)
            or not 0 <= low <= high <= 1):
        raise ValueError("Invalid classification rate thresholds")
    return rules


def classify(event: dict, policy: dict | None = None) -> dict:
    """Return a proposal from literal evidence and observed history, never speculation.

    History contains prior run outcomes: True=success, False=failure. Thresholds
    use the supplied sample only; no missing history is synthesized. Security
    takes precedence over a success conclusion. Reasons never echo raw evidence.
    """
    rules = _classification_rules(policy)

    def result(category, action, rule, reason):
        return _result(category, action, rule, reason, rules["version"])

    if not isinstance(event, dict):
        return result("unknown", "investigate", "invalid-event", "Manual review: event must be an object.")
    evidence = event.get("evidence")
    history = event.get("history")
    conclusion = event.get("conclusion")
    if not isinstance(evidence, list) or not all(isinstance(line, str) for line in evidence):
        return result("unknown", "investigate", "invalid-evidence", "Manual review: evidence must be a list of strings.")
    # Concrete security evidence remains actionable even when other metadata is bad.
    if _match(evidence, rules["security_signatures"]):
        return result("security", "escalate", "security-literal", "Security signature found; propose human escalation, not retry.")
    if not isinstance(conclusion, str) or conclusion not in _CONCLUSIONS:
        return result("unknown", "investigate", "invalid-conclusion", "Manual review: conclusion is missing or unsupported.")
    if not isinstance(history, list) or not all(type(outcome) is bool for outcome in history):
        return result("unknown", "investigate", "invalid-history", "Manual review: history must be a list of boolean outcomes.")
    if conclusion == "success":
        return result("healthy", "none", "successful-run", "Successful run; no action proposed.")
    if conclusion not in {"failure", "timed_out", "startup_failure"}:
        return result("unknown", "investigate", "nonfailure-conclusion", "Nonfailure terminal conclusion; propose manual review, not retry.")
    if _match(evidence, rules["config_signatures"]):
        return result("config", "investigate", "configuration-literal", "Configuration signature found; propose configuration review.")
    count = len(history)
    successes = sum(history)
    if count >= rules["systemic_min_samples"] and successes == 0:
        return result("systemic", "investigate", "all-observed-failures", "Observed failures meet the configured sample threshold; propose systemic investigation.")
    if count >= rules["flaky_min_samples"]:
        rate = successes / count
        if rules["flaky_success_rate_min"] <= rate <= rules["flaky_success_rate_max"]:
            return result("flaky", "retry", "observed-mixed-history", "Observed success rate is within the configured flaky range; propose a bounded retry.")
    if _match(evidence, rules["transient_signatures"]):
        return result("transient", "retry", "transient-literal", "Explicit transient signature found; propose a bounded retry.")
    return result("unknown", "investigate", "insufficient-evidence", "Insufficient evidence for a known category; propose manual review.")
