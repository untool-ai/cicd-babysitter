import unittest

from src.github_client import GitHubError, PageResult
from src.org_health import discover_active_repositories, scan_organization, scan_repository


class FakeClient:
    """Deterministic stand-in exercising org_health's read-only contract only."""

    def __init__(self, repos, runs_by_repo, prs_by_repo, required_by_repo=None, *, truncated_repos=()):
        self.repos = repos
        self.runs_by_repo = runs_by_repo
        self.prs_by_repo = prs_by_repo
        self.required_by_repo = required_by_repo or {}
        self.truncated_repos = set(truncated_repos)
        self.mutating_calls = []

    def list_repositories(self, *, include_archived=False):
        items = self.repos if include_archived else [r for r in self.repos if not r.get("archived")]
        return PageResult(items, False, 1)

    def list_workflow_runs(self, repository, *, days=30):
        runs = self.runs_by_repo.get(repository, [])
        return PageResult(runs, repository in self.truncated_repos, 1)

    def list_pull_requests(self, repository, *, state="open"):
        prs = self.prs_by_repo.get(repository, [])
        return PageResult(prs, False, 1)

    def get_required_status_contexts(self, repository, branch):
        return self.required_by_repo.get(repository, [])

    # Any accidental use would prove a mutation leaked into a read-only scan.
    def retry_run(self, *a, **k):
        self.mutating_calls.append(("retry_run", a, k))
        raise AssertionError("org_health must never call a mutating endpoint")

    def create_issue(self, *a, **k):
        self.mutating_calls.append(("create_issue", a, k))
        raise AssertionError("org_health must never call a mutating endpoint")


def run(id_, workflow_id, branch, conclusion, sha="a" * 40, created_at="2024-01-01T00:00:00Z", name="CI"):
    return {"id": id_, "workflow_id": workflow_id, "head_branch": branch, "conclusion": conclusion,
            "head_sha": sha, "created_at": created_at, "updated_at": created_at, "run_attempt": 1, "name": name}


class DiscoveryTests(unittest.TestCase):
    def test_excludes_archived_and_keeps_default_branch(self):
        client = FakeClient(
            [{"full_name": "untool-ai/a", "default_branch": "main", "archived": False},
             {"full_name": "untool-ai/b", "default_branch": "main", "archived": True}],
            {}, {})
        result = discover_active_repositories(client)
        self.assertEqual(result["repositories"], [{"repository": "untool-ai/a", "default_branch": "main"}])
        self.assertFalse(result["truncated"])


class ScanRepositoryTests(unittest.TestCase):
    def test_current_default_branch_failure_is_top_priority(self):
        client = FakeClient([], {"untool-ai/a": [run(1, 5, "main", "failure")]}, {})
        result = scan_repository(client, "untool-ai/a", "main")
        self.assertEqual(len(result["receipts"]), 1)
        self.assertEqual(result["receipts"][0]["priority"], "default_branch_regression")
        self.assertFalse(result["receipts"][0]["superseded"])

    def test_superseded_historical_failure_is_distinguished_from_current(self):
        runs = [run(1, 5, "main", "failure", created_at="2024-01-01T00:00:00Z"),
                run(2, 5, "main", "success", created_at="2024-01-02T00:00:00Z")]
        client = FakeClient([], {"untool-ai/a": runs}, {})
        result = scan_repository(client, "untool-ai/a", "main")
        self.assertEqual(len(result["receipts"]), 1)
        self.assertEqual(result["receipts"][0]["priority"], "superseded_historical_failure")
        self.assertTrue(result["receipts"][0]["superseded"])
        self.assertEqual(result["receipts"][0]["run_id"], 1)

    def test_pull_request_head_failure_flagged_as_pr_regression(self):
        runs = [run(1, 5, "feature", "failure", sha="b" * 40)]
        prs = [{"number": 9, "head": {"sha": "b" * 40}}]
        client = FakeClient([], {"untool-ai/a": runs}, {"untool-ai/a": prs})
        result = scan_repository(client, "untool-ai/a", "main")
        self.assertEqual(result["receipts"][0]["priority"], "pull_request_regression")
        self.assertEqual(result["open_pull_requests"], 1)

    def test_unmatched_branch_failure_is_lowest_actionable_priority(self):
        runs = [run(1, 5, "some-branch", "failure", sha="c" * 40)]
        client = FakeClient([], {"untool-ai/a": runs}, {"untool-ai/a": []})
        result = scan_repository(client, "untool-ai/a", "main")
        self.assertEqual(result["receipts"][0]["priority"], "branch_failure")

    def test_required_check_flag_set_only_on_matching_workflow_name(self):
        runs = [run(1, 5, "main", "failure", name="Build")]
        client = FakeClient([], {"untool-ai/a": runs}, {}, required_by_repo={"untool-ai/a": ["build"]})
        result = scan_repository(client, "untool-ai/a", "main")
        self.assertTrue(result["receipts"][0]["required_check"])

    def test_required_status_lookup_failure_is_non_fatal(self):
        class Failing(FakeClient):
            def get_required_status_contexts(self, repository, branch):
                raise GitHubError(403)
        client = Failing([], {"untool-ai/a": [run(1, 5, "main", "failure")]}, {})
        result = scan_repository(client, "untool-ai/a", "main")
        self.assertFalse(result["receipts"][0]["required_check"])

    def test_successful_runs_never_produce_receipts(self):
        client = FakeClient([], {"untool-ai/a": [run(1, 5, "main", "success")]}, {})
        result = scan_repository(client, "untool-ai/a", "main")
        self.assertEqual(result["receipts"], [])


class ScanOrganizationTests(unittest.TestCase):
    def test_enumerates_full_org_and_never_mutates(self):
        repos = [{"full_name": f"untool-ai/r{i}", "default_branch": "main", "archived": False} for i in range(12)]
        runs = {f"untool-ai/r{i}": [run(i, 1, "main", "failure")] for i in range(12)}
        client = FakeClient(repos, runs, {})
        result = scan_organization(client)
        self.assertEqual(result["repositories_discovered"], 12)
        self.assertEqual(len(result["receipts"]), 12)
        self.assertTrue(result["coverage_complete"])
        self.assertEqual(client.mutating_calls, [])

    def test_any_truncation_flips_coverage_incomplete(self):
        repos = [{"full_name": "untool-ai/a", "default_branch": "main", "archived": False}]
        client = FakeClient(repos, {"untool-ai/a": [run(1, 1, "main", "failure")]}, {}, truncated_repos={"untool-ai/a"})
        result = scan_organization(client)
        self.assertFalse(result["coverage_complete"])
        self.assertEqual(result["truncated_repositories"], ["untool-ai/a"])

    def test_receipts_sorted_by_priority_then_repository(self):
        repos = [{"full_name": "untool-ai/z", "default_branch": "main", "archived": False},
                  {"full_name": "untool-ai/a", "default_branch": "main", "archived": False}]
        runs = {
            "untool-ai/z": [run(1, 1, "main", "failure")],
            "untool-ai/a": [run(2, 1, "feature", "failure", sha="d" * 40)],
        }
        prs = {"untool-ai/a": [{"number": 1, "head": {"sha": "d" * 40}}]}
        client = FakeClient(repos, runs, prs)
        result = scan_organization(client)
        priorities = [r["priority"] for r in result["receipts"]]
        self.assertEqual(priorities, ["default_branch_regression", "pull_request_regression"])

    def test_empty_org_is_not_complete_coverage(self):
        client = FakeClient([], {}, {})
        result = scan_organization(client)
        self.assertFalse(result["coverage_complete"])
        self.assertEqual(result["receipts"], [])


if __name__ == "__main__":
    unittest.main()
