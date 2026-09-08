import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("babysitter_analytics", ROOT / "src" / "analytics.py")
analytics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analytics)


class AnalyticsTests(unittest.TestCase):
    def test_empty_samples_are_unknown_not_perfect(self):
        report = analytics.summarize([])
        self.assertIsNone(report["reliability"]["success_rate"])
        self.assertIsNone(report["detection_latency"]["mean_seconds"])
        self.assertIsNone(report["estimated_cost"]["estimated_total"])
        self.assertEqual(report["record_count"], 0)

    def test_reliability_uses_only_known_outcomes(self):
        records = [{"conclusion": c} for c in ["success", "failure", "timed_out", "startup_failure", "cancelled", "neutral", None]]
        report = analytics.summarize(records)
        self.assertEqual(report["reliability"], {"successes": 1, "failures": 3, "known_samples": 4,
                                                "unknown_samples": 3, "success_rate": 0.25})

    def test_received_at_detection_latency_requires_timezone_and_nonnegative_interval(self):
        records = [
            {"event": {"updated_at": "2026-09-07T10:00:00Z"}, "received_at": "2026-09-07T10:00:30Z"},
            {"event": {"updated_at": "2026-09-07T12:00:00+02:00"}, "received_at": "2026-09-07T10:01:00Z"},
            {"event": {"updated_at": "2026-09-07T10:00:00"}, "received_at": "2026-09-07T10:00:30Z"},
            {"event": {"updated_at": "2026-09-07T10:00:00Z"}, "received_at": "2026-09-07T09:00:00Z"},
            {"event": {"updated_at": "bad"}, "received_at": "bad"},
        ]
        report = analytics.summarize(records)["detection_latency"]
        self.assertEqual(report["known_samples"], 2)
        self.assertEqual(report["unknown_samples"], 3)
        self.assertEqual(report["mean_seconds"], 45)
        self.assertEqual(report["p50_seconds"], 30)
        self.assertEqual(report["p95_seconds"], 60)

    def test_detection_excludes_run_duration_and_end_to_end_is_separate(self):
        records = [{"event": {"created_at": "2026-09-07T09:00:00Z", "updated_at": "2026-09-07T10:00:00Z"},
                    "received_at": "2026-09-07T10:00:30Z"},
                   {"event": {"created_at": "2026-09-07T09:00:00Z"}, "received_at": "2026-09-07T10:00:30Z"}]
        report = analytics.summarize(records)
        self.assertEqual(report["detection_latency"]["mean_seconds"], 30)
        self.assertEqual(report["detection_latency"]["unknown_samples"], 1)
        self.assertEqual(report["run_end_to_end"]["mean_seconds"], 3630)

    def test_invalid_numeric_measurements_remain_unknown(self):
        records = [{"duration_seconds": x, "detection_latency_seconds": x} for x in [None, True, "3", -1, float("inf"), float("nan")]]
        report = analytics.summarize(records, 0.1)
        self.assertEqual(report["run_duration"]["known_samples"], 0)
        self.assertEqual(report["detection_latency"]["known_samples"], 0)
        self.assertIsNone(report["estimated_cost"]["estimated_total"])

    def test_only_explicit_price_and_measured_durations_enable_cost(self):
        records = [{"event": {"duration_seconds": 120}}, {"event": {"started_at": "2026-09-07T10:00:00Z", "completed_at": "2026-09-07T10:01:00Z"}}, {}]
        unpriced = analytics.summarize(records)
        self.assertIsNone(unpriced["estimated_cost"]["estimated_total"])
        self.assertEqual(unpriced["estimated_cost"]["unknown_samples"], 3)
        priced = analytics.summarize(records, 0.2)["estimated_cost"]
        self.assertAlmostEqual(priced["estimated_total"], 0.6)
        self.assertEqual(priced["known_samples"], 2)
        self.assertEqual(priced["unknown_samples"], 1)
        self.assertIsNone(priced["currency"])
        self.assertEqual(analytics.summarize(records, 0)["estimated_cost"]["estimated_total"], 0)

    def test_remediation_requires_verified_boolean_outcome(self):
        records = [
            {"outcome": {"verified": True, "success": True, "latency_seconds": 5}},
            {"outcome": {"verified": True, "success": False, "latency_seconds": 7}},
            {"outcome": {"verified": False, "success": True, "latency_seconds": 2}},
            {"outcome": {"verified": True, "success": "true"}},
            {"outcome": {"success": True}},
        ]
        report = analytics.summarize(records)
        self.assertEqual(report["verified_remediation"]["known_samples"], 2)
        self.assertEqual(report["verified_remediation"]["unknown_samples"], 3)
        self.assertEqual(report["verified_remediation"]["success_rate"], 0.5)
        self.assertEqual(report["remediation_latency"]["mean_seconds"], 6)

    def test_malformed_records_are_counted_not_dropped_from_denominator(self):
        records = [None, [], "bad", {"event": None}, {"conclusion": ["success"]}]
        report = analytics.summarize(records)
        self.assertEqual(report["record_count"], 5)
        self.assertEqual(report["invalid_record_count"], 4)
        self.assertEqual(report["reliability"]["unknown_samples"], 5)
        for invalid in [None, {}, "records"]:
            with self.assertRaises(ValueError):
                analytics.summarize(invalid)
        for invalid in [True, -1, "0.1", float("inf"), float("nan")]:
            with self.assertRaises(ValueError):
                analytics.summarize([], invalid)

    def test_proposals_are_not_measured_actions_or_policy_changes(self):
        records = [{"decision": {"category": "security", "action": "escalate"}},
                   {"decision": {"category": "flaky", "action": "retry"}},
                   {"decision": {"category": "injected-secret", "action": "delete-all"}}, {}]
        report = analytics.summarize(records)
        self.assertEqual(report["category_counts"], {"flaky": 1, "security": 1, "unknown": 1})
        self.assertEqual(report["verified_remediation"]["known_samples"], 0)
        self.assertEqual(report["unclassified_records"], 1)
        before = copy.deepcopy(report)
        text = analytics.generate_runbook(report)
        self.assertIn("Recommendations only", text)
        self.assertIn("no actions or policy changes", text)
        self.assertIn("human review", text)
        self.assertNotIn("injected-secret", text)
        self.assertNotIn("delete-all", text)
        self.assertEqual(report, before)

    def test_summary_is_deterministic_and_nonmutating(self):
        records = [{"event": {"conclusion": "success", "duration_seconds": 30}}]
        before = copy.deepcopy(records)
        self.assertEqual(analytics.summarize(records), analytics.summarize(records))
        self.assertEqual(records, before)


if __name__ == "__main__":
    unittest.main()
