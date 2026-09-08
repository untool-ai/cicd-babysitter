import unittest
from src.monitor import observe, normalize_run
from src.github_client import PageResult


class MonitorTests(unittest.TestCase):
    def test_observation_explicit_handler_and_truncation(self):
        class Client:
            def list_workflow_runs(self, repo, days):
                return PageResult([{"id":1,"run_attempt":2}], True, 1)
        events = []
        result = observe(Client(), ["untool-ai/test"], handler=events.append)
        self.assertFalse(result["coverage_complete"])
        self.assertEqual(events[0]["attempt"], 2)
        self.assertEqual(result["mode"], "observation-only")

    def test_rejects_invalid_identity(self):
        with self.assertRaises(ValueError):
            normalize_run("untool-ai/test", {"id":True})

    def test_duplicates_rejected(self):
        with self.assertRaises(ValueError):
            observe(None, ["a", "a"])

    def test_no_handler_never_reports_durable_coverage(self):
        class Client:
            def list_workflow_runs(self, repo, days):
                return PageResult([{"id":1}], False, 1)
        result = observe(Client(), ['untool-ai/test'])
        self.assertFalse(result['coverage_complete'])
        self.assertEqual(result['retrieved'],1)
        self.assertEqual(result['persisted'],0)

    def test_handler_success_is_counted_separately(self):
        class Client:
            def list_workflow_runs(self, repo, days):
                return PageResult([{"id":1}], False, 1)
        result = observe(Client(), ['untool-ai/test'], handler=lambda event: None)
        self.assertTrue(result['coverage_complete'])
        self.assertEqual(result['persisted'],1)

    def test_empty_scope_never_complete(self):
        self.assertFalse(observe(None, [], handler=lambda event: None)['coverage_complete'])
