import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("failure_classifier", ROOT / "src" / "failure_classifier.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
classify = module.classify


def event(evidence=None, history=None, conclusion="failure"):
    return {"conclusion": conclusion, "evidence": evidence or [], "history": history or []}


class ClassifierTests(unittest.TestCase):
    def assert_category(self, expected, sample):
        result = classify(sample)
        self.assertEqual(result["category"], expected)
        self.assertEqual(set(result), {"category", "action", "rule", "reason"})
        self.assertIn(result["action"], {"retry", "investigate", "escalate", "none"})
        self.assertTrue(result["rule"].startswith("1.0.0:"))
        return result

    def test_security_precedes_success_and_all_other_signals(self):
        sample = event(["SECRET LEAK synthetic-sensitive-value", "configuration error", "timeout"], [False] * 20, "success")
        result = self.assert_category("security", sample)
        self.assertEqual(result["action"], "escalate")
        self.assertNotIn("synthetic-sensitive-value", json.dumps(result))

    def test_security_preserved_with_other_invalid_metadata(self):
        self.assert_category("security", {"conclusion": None, "history": None, "evidence": ["security alert"]})

    def test_configuration_precedes_history_and_transient(self):
        self.assert_category("config", event(["invalid YAML", "timeout"], [False] * 20))
        self.assert_category("config", event(["configuration error"], [True] * 10 + [False] * 10))

    def test_systemic_threshold_and_all_false_requirement(self):
        for size, category in [(9, "unknown"), (10, "systemic"), (20, "systemic")]:
            with self.subTest(size=size):
                self.assert_category(category, event(history=[False] * size))
        self.assert_category("unknown", event(history=[False] * 9 + [True]))
        self.assert_category("systemic", event(["timeout"], [False] * 10))

    def test_flaky_minimum_and_inclusive_rate_boundaries(self):
        for count, successes, category in [(19, 10, "unknown"), (20, 9, "unknown"),
                                          (20, 10, "flaky"), (20, 16, "flaky"),
                                          (20, 17, "unknown"), (20, 20, "unknown")]:
            with self.subTest(count=count, successes=successes):
                self.assert_category(category, event(history=[True] * successes + [False] * (count - successes)))
        self.assert_category("flaky", event(["timeout"], [True] * 10 + [False] * 10))

    def test_transient_requires_explicit_evidence(self):
        for literal in ["TIMEOUT", "connection reset", "ECONNRESET", "rate limit", "HTTP 429"]:
            with self.subTest(literal=literal):
                self.assert_category("transient", event([literal]))
        self.assert_category("unknown", event(conclusion="timed_out"))
        self.assert_category("unknown", event(["something failed unexpectedly"]))

    def test_success_is_healthy_despite_old_failure_history(self):
        result = self.assert_category("healthy", event(["timeout"], [False] * 20, "success"))
        self.assertEqual(result["action"], "none")

    def test_nonfailure_terminal_conclusions_never_propose_retry(self):
        for conclusion in ["skipped", "neutral", "cancelled", "action_required", "stale"]:
            with self.subTest(conclusion=conclusion):
                result = self.assert_category("unknown", event(["timeout", "connection reset"], [True] * 10 + [False] * 10, conclusion))
                self.assertEqual(result["action"], "investigate")
                self.assertIn("Nonfailure", result["reason"])
                self.assertEqual(result["rule"], "1.0.0:nonfailure-conclusion")
                self.assert_category("security", event(["secret leak", "timeout"], [], conclusion))

    def test_failure_conclusions_remain_eligible_for_retry(self):
        for conclusion in ["failure", "timed_out", "startup_failure"]:
            with self.subTest(conclusion=conclusion):
                result = self.assert_category("transient", event(["timeout"], [], conclusion))
                self.assertEqual(result["action"], "retry")

    def test_malformed_events_fail_to_manual_review(self):
        samples = [None, [], "failure", {}, {"conclusion": "failure", "history": []},
                   event(conclusion="made_up"), event(conclusion=42)]
        for malformed in [None, "timeout", {}, [None], [3], ["timeout", {}]]:
            samples.append({"conclusion": "failure", "evidence": malformed, "history": []})
        for malformed in [None, "false", {}, [0], [1], [False, "false"]]:
            samples.append({"conclusion": "failure", "evidence": [], "history": malformed})
        for sample in samples:
            with self.subTest(sample=sample):
                result = self.assert_category("unknown", sample)
                self.assertEqual(result["action"], "investigate")

    def test_deterministic_and_does_not_mutate_input(self):
        sample = event(["connection reset"], [True, False])
        original = copy.deepcopy(sample)
        self.assertEqual(classify(sample), classify(sample))
        self.assertEqual(sample, original)

    def test_selected_policy_removes_signature_without_mutating_defaults(self):
        selected = {"version": "custom-v2", "transient_signatures": []}
        before = copy.deepcopy(selected)
        result = classify(event(["timeout"]), policy=selected)
        self.assertEqual(result["category"], "unknown")
        self.assertEqual(result["action"], "investigate")
        self.assertEqual(result["rule"], "custom-v2:insufficient-evidence")
        self.assertEqual(selected, before)
        self.assertEqual(classify(event(["timeout"]))["action"], "retry")

    def test_selected_policy_thresholds_and_security_are_used(self):
        selected = {"version": "custom-v2", "systemic_min_samples": 30,
                    "flaky_min_samples": 30, "security_signatures": ["CUSTOM SECURITY SIGNAL"]}
        self.assertEqual(classify(event(history=[False] * 10), selected)["category"], "unknown")
        self.assertEqual(classify(event(history=[True] * 10 + [False] * 10), selected)["category"], "unknown")
        result = classify(event(["custom security signal"], conclusion="success"), selected)
        self.assertEqual(result["category"], "security")
        self.assertEqual(result["action"], "escalate")

    def test_invalid_selected_policy_cannot_silently_use_defaults(self):
        for invalid in [[], {"transient_signatures": None}, {"transient_signatures": [""]},
                        {"systemic_min_samples": 0}, {"flaky_min_samples": True},
                        {"flaky_success_rate_min": float("nan")}, {"version": None}]:
            with self.subTest(policy=invalid), self.assertRaises(ValueError):
                classify(event(["timeout"]), invalid)

    def test_babysitter_observe_uses_selected_policy(self):
        import sys
        import tempfile
        sys.path.insert(0, str(ROOT))
        try:
            from src.babysitter_agent import Babysitter
            with tempfile.TemporaryDirectory() as directory:
                policy = {"allowed_repositories": ["untool-ai/fixture"], "version": "selected-v3",
                          "transient_signatures": []}
                agent = Babysitter(str(Path(directory) / "events.sqlite"), policy)
                try:
                    sample = {**event(["timeout"]), "repository": "untool-ai/fixture", "run_id": 1,
                              "attempt": 1, "workflow_id": 1, "sha": "a" * 40,
                              "branch": "main", "status": "completed"}
                    decision = agent.observe(sample)["decision"]
                    self.assertEqual(decision["category"], "unknown")
                    self.assertEqual(decision["action"], "investigate")
                    self.assertEqual(decision["rule"], "selected-v3:insufficient-evidence")
                    self.assertFalse(decision["executed"])
                finally:
                    agent.close()
        finally:
            sys.path.pop(0)

    def test_ruleset_is_versioned_json_yaml_subset(self):
        config = json.loads((ROOT / "config" / "org-ruleset.yaml").read_text(encoding="utf-8-sig"))
        self.assertEqual(config["version"], "1.0.0")
        self.assertEqual(config["systemic_min_samples"], 10)
        self.assertEqual(config["flaky_min_samples"], 20)


if __name__ == "__main__":
    unittest.main()
