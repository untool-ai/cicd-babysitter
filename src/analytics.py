"""Offline analytics of observed runs; recommendations never change policy.

Input records are {event: {...}, decision: {...}, outcome: {...}, detected_at: ...}.
Flat normalized event records are also accepted. All timestamps must include a
UTC offset; negative/invalid intervals are unknown, never clamped to zero.
"""
from collections import Counter
from datetime import datetime
import math
from statistics import mean

_CATEGORIES = {"healthy", "security", "config", "systemic", "flaky", "transient", "unknown"}
_ACTIONS = {"retry", "investigate", "escalate", "none"}
_FAILURES = {"failure", "timed_out", "startup_failure"}


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None
    except ValueError:
        return None


def _duration(container, field, start, end):
    if field in container:
        return _number(container[field])
    began, finished = _timestamp(container.get(start)), _timestamp(container.get(end))
    if began is None or finished is None:
        return None
    return _number((finished - began).total_seconds())


def _metric(samples, total):
    values = sorted(samples)
    return {
        "known_samples": len(values), "unknown_samples": total - len(values),
        "mean_seconds": mean(values) if values else None,
        "p50_seconds": values[math.ceil(len(values) * 0.5) - 1] if values else None,
        "p95_seconds": values[math.ceil(len(values) * 0.95) - 1] if values else None,
        "percentile_method": "nearest-rank",
    }


def summarize(records: list, pricing_per_minute=None) -> dict:
    """Summarize samples without filling gaps or claiming unobserved coverage.

    Explicit nonnegative pricing enables an estimate using measured run duration
    only; no default vendor price/currency is invented. Verified remediation
    outcome samples require both verified=True and a boolean success field.
    Counts are per supplied record, not a claim of unique-run or org coverage.
    """
    if not isinstance(records, list):
        raise ValueError("records must be a list")
    if pricing_per_minute is not None and _number(pricing_per_minute) is None:
        raise ValueError("pricing_per_minute must be a finite nonnegative number")
    successes = failures = verified_successes = verified_failures = invalid = 0
    durations, detection, remediation, end_to_end = [], [], [], []
    categories, actions = Counter(), Counter()
    for record in records:
        if not isinstance(record, dict):
            invalid += 1
            continue
        event = record.get("event", record)
        if not isinstance(event, dict):
            invalid += 1
            continue
        conclusion = event.get("conclusion")
        if conclusion == "success":
            successes += 1
        elif isinstance(conclusion, str) and conclusion in _FAILURES:
            failures += 1
        duration = _duration(event, "duration_seconds", "started_at", "completed_at")
        if duration is not None:
            durations.append(duration)
        timing = {
            "updated_at": event.get("updated_at"),
            "detected_at": record.get("received_at", record.get("detected_at", event.get("detected_at"))),
        }
        if "detection_latency_seconds" in record:
            timing["detection_latency_seconds"] = record["detection_latency_seconds"]
        delay = _duration(timing, "detection_latency_seconds", "updated_at", "detected_at")
        if delay is not None:
            detection.append(delay)
        run_timing = {"created_at": event.get("created_at"), "received_at": timing["detected_at"]}
        elapsed_run = _duration(run_timing, "run_end_to_end_seconds", "created_at", "received_at")
        if elapsed_run is not None:
            end_to_end.append(elapsed_run)
        decision = record.get("decision")
        if isinstance(decision, dict):
            category = decision.get("category")
            action = decision.get("action")
            categories[category if isinstance(category, str) and category in _CATEGORIES else "unknown"] += 1
            actions[action if isinstance(action, str) and action in _ACTIONS else "unknown"] += 1
        outcome = record.get("outcome")
        if isinstance(outcome, dict):
            if outcome.get("verified") is True and type(outcome.get("success")) is bool:
                if outcome["success"]:
                    verified_successes += 1
                else:
                    verified_failures += 1
                elapsed = _duration(outcome, "latency_seconds", "requested_at", "completed_at")
                if elapsed is not None:
                    remediation.append(elapsed)
    total = len(records)
    known = successes + failures
    verified = verified_successes + verified_failures
    return {
        "schema_version": "1.0.0", "sample_basis": "supplied-records-not-org-coverage",
        "record_count": total, "invalid_record_count": invalid,
        "reliability": {
            "successes": successes, "failures": failures,
            "known_samples": known, "unknown_samples": total - known,
            "success_rate": successes / known if known else None,
        },
        "verified_remediation": {
            "successes": verified_successes, "failures": verified_failures,
            "known_samples": verified, "unknown_samples": total - verified,
            "success_rate": verified_successes / verified if verified else None,
        },
        "detection_latency": _metric(detection, total),
        "remediation_latency": _metric(remediation, total),
        "run_duration": _metric(durations, total),
        "run_end_to_end": _metric(end_to_end, total),
        "category_counts": dict(sorted(categories.items())),
        "proposed_action_counts": dict(sorted(actions.items())),
        "unclassified_records": total - sum(categories.values()),
        "estimated_cost": {
            "rate_per_minute": pricing_per_minute,
            "estimated_total": sum(durations) / 60 * pricing_per_minute if pricing_per_minute is not None and durations else None,
            "known_samples": len(durations) if pricing_per_minute is not None else 0,
            "unknown_samples": total - len(durations) if pricing_per_minute is not None else total,
            "currency": None,
            "note": "Estimate uses caller-supplied pricing and measured duration only; excludes unknown durations and unspecified charges.",
        },
    }


def generate_runbook(report: dict) -> str:
    """Create a review-oriented runbook from summarize() output, without execution."""
    reliability = report["reliability"]
    lines = ["# CI/CD observation runbook", "", "Recommendations only; no actions or policy changes have been applied.", "",
             f"- Supplied records: {report['record_count']} (not an organization coverage claim).",
             f"- Known run outcomes: {reliability['known_samples']}; unknown: {reliability['unknown_samples']}.",
             f"- Observed successful runs: {reliability['successes']}; failed runs: {reliability['failures']}.",
             f"- Verified remediation samples: {report['verified_remediation']['known_samples']}.", "", "## Recommended review"]
    counts = report["category_counts"]
    if counts.get("security", 0):
        lines.append("- Escalate security evidence for human review; do not automatically retry it.")
    if counts.get("config", 0) or counts.get("systemic", 0):
        lines.append("- Investigate configuration and repeated failures before proposing another run.")
    if counts.get("flaky", 0) or counts.get("transient", 0):
        lines.append("- Review bounded retry proposals against authorization and retry budgets; never infer approval from classification.")
    if reliability["unknown_samples"] or report["detection_latency"]["unknown_samples"]:
        lines.append("- Improve missing outcome/timing instrumentation before claiming reliability or latency targets.")
    lines.extend(["- Review measured results and approve any policy change separately.",
                  "- Verify run state and durable action records before retrying an uncertain operation.", ""])
    return "\n".join(lines)
