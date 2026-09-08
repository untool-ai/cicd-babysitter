import io
import json
import unittest
from urllib.error import HTTPError, URLError
from src.github_client import GitHubClient, GitHubError


class Response(io.BytesIO):
    status = 200


class ClientTests(unittest.TestCase):
    def test_pagination_reports_truncation(self):
        calls = []
        def open_(request, timeout):
            calls.append((request.full_url, timeout))
            return Response(json.dumps([{"id": i} for i in range(100)]).encode())
        result = GitHubClient("secret", max_pages=2, opener=open_).list_repositories()
        self.assertTrue(result.truncated)
        self.assertEqual(len(result.items), 200)
        self.assertEqual(len(calls), 2)
        self.assertIn("page=2", calls[-1][0])
        self.assertEqual(calls[-1][1], 15)

    def test_cross_org_and_invalid_ids_rejected(self):
        client = GitHubClient("secret", opener=lambda *a, **k: self.fail("network"))
        for repo in ["other/repo", "untool-ai/../x", "untool-ai/a?token=x"]:
            with self.assertRaises(ValueError):
                client.get_run(repo, 1)
        with self.assertRaises(ValueError):
            client.get_run("untool-ai/test", True)

    def test_mutation_failure_uncertain_no_retry_no_secret(self):
        calls = []
        def open_(*args, **kwargs):
            calls.append(1)
            raise URLError("secret")
        with self.assertRaises(GitHubError) as error:
            GitHubClient("secret", opener=open_).retry_run("untool-ai/test", 1)
        self.assertTrue(error.exception.uncertain)
        self.assertNotIn("secret", str(error.exception))
        self.assertEqual(len(calls), 1)

    def test_rate_limit_not_retried(self):
        def open_(*args, **kwargs):
            raise HTTPError("url", 429, "secret", {}, None)
        with self.assertRaises(GitHubError) as error:
            GitHubClient("secret", opener=open_).list_repositories()
        self.assertEqual(error.exception.status, 429)
        self.assertFalse(error.exception.uncertain)

    def test_backfill_cap_and_valid_run(self):
        client = GitHubClient("secret", opener=lambda *a, **k: Response(b'{"id":1}'))
        self.assertEqual(client.get_run("untool-ai/test", 1)["id"], 1)
        with self.assertRaises(ValueError):
            client.list_workflow_runs("untool-ai/test", days=31)

    def test_malformed_issue_receipt_is_uncertain(self):
        client = GitHubClient("secret", opener=lambda *a, **k: Response(b'{}'))
        with self.assertRaises(GitHubError) as error:
            client.create_issue("untool-ai/test", "title", "body")
        self.assertTrue(error.exception.uncertain)

    def test_notification_does_not_send_github_token(self):
        def open_(request, timeout):
            self.assertIsNone(request.get_header("Authorization"))
            return Response(b'ok')
        GitHubClient("secret", opener=open_).notify({"text":"test"}, webhook_url="https://hooks.slack.com/services/a/b/c")
        with self.assertRaises(ValueError):
            GitHubClient("secret", opener=open_).notify({}, webhook_url="https://evil.test/services/a/b/c")
