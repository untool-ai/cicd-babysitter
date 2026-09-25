"""Merge, issue, and PR-readiness capability tests."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import URLError

from src.babysitter_agent import Babysitter
from src.github_client import GitHubClient, GitHubError
from src.pr_readiness import evaluate_pull, summarize_checks, scan_repository_pulls
from src.remediation_engine import RemediationEngine
from src.store import canonical


def pr_event(number=7, sha=None, ready=True, author='trusted-bot[bot]', blockers=None):
    return {
        'kind': 'pull_request',
        'repository': 'untool-ai/example',
        'number': number,
        'head_sha': sha or ('b' * 40),
        'base_ref': 'main',
        'author': author,
        'draft': False,
        'merged': False,
        'ready': ready,
        'blockers': blockers or [],
        'approvals': 1,
    }


def workflow_event(attempt=1, evidence=None):
    return dict(
        repository='untool-ai/example', run_id=1, attempt=attempt, sha='a' * 40,
        workflow_id=2, branch='main', status='completed', conclusion='failure',
        evidence=evidence or ['configuration error'], history=[False] * 10)


class FakeMergeClient:
    def __init__(self):
        self.pull = {
            'number': 7, 'state': 'open', 'draft': False, 'merged': False,
            'mergeable': True, 'mergeable_state': 'clean',
            'title': 'fix', 'html_url': 'https://example.test/pr/7',
            'user': {'login': 'trusted-bot[bot]'},
            'head': {'sha': 'b' * 40}, 'base': {'ref': 'main'},
        }
        self.checks = {'check_runs': [{'name': 'build', 'status': 'completed', 'conclusion': 'success'}]}
        self.statuses = {'state': 'success', 'statuses': [{'context': 'build', 'state': 'success'}]}
        self.required = ['build']
        self.approvals = 1
        self.merge_calls = 0
        self.issue_calls = 0
        self.error = False
        self.merge_result = {'merged': True, 'sha': 'c' * 40}

    def get_pull(self, repository, number):
        assert repository == 'untool-ai/example'
        assert number == 7
        return dict(self.pull)

    def get_combined_status(self, repository, sha):
        return dict(self.statuses)

    def list_check_runs(self, repository, sha):
        return dict(self.checks)

    def get_required_status_contexts(self, repository, branch):
        return list(self.required)

    def get_pull_approval_count(self, repository, number):
        return self.approvals

    def merge_pull(self, repository, number, *, head_sha, merge_method='squash', commit_title=None):
        self.merge_calls += 1
        if self.error:
            raise TimeoutError('fixture')
        self.pull['merged'] = True
        self.pull['merge_commit_sha'] = self.merge_result.get('sha')
        return dict(self.merge_result)

    def create_issue(self, repository, title, body):
        self.issue_calls += 1
        if self.error:
            err = GitHubError(uncertain=True)
            raise err
        return {'number': 99, 'title': title}

    def get_run(self, *a, **k):
        raise AssertionError('unexpected')

    def retry_run(self, *a, **k):
        raise AssertionError('unexpected')


class ReadinessTests(unittest.TestCase):
    def test_summarize_checks_precedence(self):
        summary = summarize_checks(
            [{'context': 'lint', 'state': 'success'}],
            [{'name': 'build', 'status': 'completed', 'conclusion': 'failure'}])
        self.assertEqual(summary['overall'], 'failure')
        self.assertEqual(summary['failed'], ['build'])

    def test_evaluate_ready_and_blocked(self):
        client = FakeMergeClient()
        ready = evaluate_pull(client, 'untool-ai/example', 7)
        self.assertTrue(ready['ready'])
        self.assertEqual(ready['proposed_action'], 'merge')
        client.pull['draft'] = True
        blocked = evaluate_pull(client, 'untool-ai/example', 7)
        self.assertFalse(blocked['ready'])
        self.assertIn('draft', blocked['blockers'])

    def test_scan_repository_pulls(self):
        client = FakeMergeClient()
        client.list_pull_requests = lambda repository, state='open': type('R', (), {
            'items': [{'number': 7}], 'truncated': False, 'pages': 1})()
        result = scan_repository_pulls(client, 'untool-ai/example')
        self.assertEqual(result['ready_count'], 1)


class MergeEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'test.db'
        self.policy = dict(
            allowed_repositories=['untool-ai/example'],
            allowed_merge_repositories=['untool-ai/example'],
            allowed_workflows=['2'],
            dry_run=False,
            enable_merge=True,
            enable_issues=True,
            merge_method='squash',
            merge_required_approvals=1,
            merge_require_human_approval=True,
            trusted_merge_authors=['trusted-bot[bot]'],
            max_retries=3,
            retry_backoff_seconds=1,
        )
        self.agent = Babysitter(self.path, self.policy)
        self.client = FakeMergeClient()
        self.engine = RemediationEngine(self.agent.store, self.client, self.policy, clock=lambda: 100)

    def tearDown(self):
        self.agent.close()
        self.tmp.cleanup()

    def test_observe_pr_proposes_merge(self):
        result = self.agent.observe_pr(pr_event())
        self.assertFalse(result['duplicate'])
        self.assertEqual(result['decision']['action'], 'merge')

    def test_merge_requires_policy_and_approval_path(self):
        self.agent.observe_pr(pr_event())
        self.policy['enable_merge'] = False
        denied = self.engine.merge(pr_event())
        self.assertEqual(denied['state'], 'denied')
        self.policy['enable_merge'] = True
        self.policy['trusted_merge_authors'] = []
        denied = self.engine.merge(pr_event(author='human'))
        self.assertEqual(denied['reason'], 'Human approval or trusted author required')
        self.assertEqual(self.client.merge_calls, 0)

    def test_merge_trusted_author_and_reconcile(self):
        recorded = self.agent.observe_pr(pr_event())
        result = self.engine.merge(pr_event())
        self.assertEqual(result['state'], 'accepted')
        self.assertEqual(self.client.merge_calls, 1)
        self.assertTrue(self.engine.merge(pr_event())['duplicate'])
        self.assertEqual(self.client.merge_calls, 1)
        verified = self.engine.reconcile(result['action_key'])
        self.assertEqual(verified['state'], 'verified_success')
        self.assertTrue(self.agent.store.verify())
        self.assertEqual(recorded['decision']['action'], 'merge')

    def test_merge_human_approval_when_not_trusted(self):
        self.policy['trusted_merge_authors'] = []
        self.agent.observe_pr(pr_event(author='contributor'))
        self.client.pull['user'] = {'login': 'contributor'}
        result = self.engine.merge(
            pr_event(author='contributor'),
            approval={'operator': 'oncall', 'reason': 'green and reviewed'})
        self.assertEqual(result['state'], 'accepted')

    def test_merge_denied_when_live_not_ready(self):
        self.agent.observe_pr(pr_event())
        self.client.checks = {
            'check_runs': [{'name': 'build', 'status': 'completed', 'conclusion': 'failure'}]}
        result = self.engine.merge(pr_event())
        self.assertEqual(result['state'], 'denied')
        self.assertIn('checks-failed', result['reason'])
        self.assertEqual(self.client.merge_calls, 0)

    def test_merge_uncertain_never_resubmits(self):
        self.agent.observe_pr(pr_event())
        self.client.error = True
        result = self.engine.merge(pr_event())
        self.assertEqual(result['state'], 'uncertain')
        self.assertTrue(self.engine.merge(pr_event())['duplicate'])
        self.assertEqual(self.client.merge_calls, 1)

    def test_create_issue_bound_to_investigation(self):
        event = workflow_event()
        self.agent.observe(event)
        result = self.engine.create_issue(event)
        self.assertEqual(result['state'], 'verified_success')
        self.assertEqual(result['issue_number'], 99)
        self.assertEqual(self.client.issue_calls, 1)
        self.assertTrue(self.engine.create_issue(event)['duplicate'])

    def test_create_issue_disabled_fail_closed(self):
        event = workflow_event()
        self.agent.observe(event)
        self.policy['enable_issues'] = False
        self.assertEqual(self.engine.create_issue(event)['state'], 'denied')
        self.assertEqual(self.client.issue_calls, 0)

    def test_execute_dispatch_and_unknown_denied(self):
        self.agent.observe_pr(pr_event())
        self.assertEqual(self.engine.execute('merge', pr_event())['state'], 'accepted')
        with self.assertRaises(PermissionError):
            self.engine.execute('revert')
        with self.assertRaises(PermissionError):
            self.engine.execute('quarantine')

    def test_dry_run_blocks_merge_and_issue(self):
        self.policy['dry_run'] = True
        self.agent.observe_pr(pr_event())
        self.agent.observe(workflow_event())
        self.assertEqual(self.engine.merge(pr_event())['state'], 'dry-run')
        self.assertEqual(self.engine.create_issue(workflow_event())['state'], 'dry-run')
        self.assertEqual(self.client.merge_calls, 0)
        self.assertEqual(self.client.issue_calls, 0)


class GitHubMergeClientTests(unittest.TestCase):
    def test_merge_pull_puts_sha_and_method(self):
        calls = []

        def open_(request, timeout):
            calls.append((request.method, request.full_url, request.data))
            class Resp:
                status = 200
                def read(self, n=-1):
                    return json.dumps({'merged': True, 'sha': 'd' * 40}).encode()
                def __enter__(self): return self
                def __exit__(self, *a): pass
            return Resp()

        client = GitHubClient('token', opener=open_)
        result = client.merge_pull('untool-ai/test', 3, head_sha='a' * 40, merge_method='squash')
        self.assertTrue(result['merged'])
        method, url, data = calls[0]
        self.assertEqual(method, 'PUT')
        self.assertIn('/pulls/3/merge', url)
        payload = json.loads(data.decode())
        self.assertEqual(payload['sha'], 'a' * 40)
        self.assertEqual(payload['merge_method'], 'squash')

    def test_merge_pull_rejects_bad_method_and_sha(self):
        client = GitHubClient('token', opener=lambda *a, **k: self.fail('network'))
        with self.assertRaises(ValueError):
            client.merge_pull('untool-ai/test', 1, head_sha='a' * 40, merge_method='fast')
        with self.assertRaises(ValueError):
            client.merge_pull('untool-ai/test', 1, head_sha='nope')

    def test_get_pull_approval_count_uses_latest_per_user(self):
        pages = [[
            {'user': {'login': 'a'}, 'state': 'APPROVED'},
            {'user': {'login': 'a'}, 'state': 'CHANGES_REQUESTED'},
            {'user': {'login': 'b'}, 'state': 'APPROVED'},
        ]]

        def open_(request, timeout):
            class Resp:
                status = 200
                def read(self, n=-1):
                    return json.dumps(pages[0]).encode()
                def __enter__(self): return self
                def __exit__(self, *a): pass
            return Resp()

        count = GitHubClient('token', opener=open_).get_pull_approval_count('untool-ai/test', 1)
        self.assertEqual(count, 1)

    def test_merge_transport_failure_is_uncertain(self):
        def open_(*a, **k):
            raise URLError('secret')
        with self.assertRaises(GitHubError) as err:
            GitHubClient('token', opener=open_).merge_pull(
                'untool-ai/test', 1, head_sha='a' * 40)
        self.assertTrue(err.exception.uncertain)
        self.assertNotIn('secret', str(err.exception))


if __name__ == '__main__':
    unittest.main()
