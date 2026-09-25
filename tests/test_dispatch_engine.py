import tempfile
import unittest
from pathlib import Path

from src.babysitter_agent import Babysitter
from src.dispatch_engine import DispatchEngine, size_tier


def event(pull_number=7, changed_files=2, conclusion='failure'):
    return dict(repository='untool-ai/example', run_id=1, attempt=1, sha='a' * 40, workflow_id=2, branch='main',
                status='completed', conclusion=conclusion, evidence=['configuration error'], history=[],
                pull_number=pull_number, changed_files=changed_files)


class Client:
    def __init__(self):
        self.labels = []
        self.comments = []
        self.error = False

    def add_labels(self, repository, number, labels):
        if self.error:
            raise TimeoutError('fixture')
        self.labels.append((repository, number, tuple(labels)))
        return [{'name': label} for label in labels]

    def create_comment(self, repository, number, body):
        self.comments.append((repository, number, body))
        return {'id': 1}


DISPATCH_POLICY = {
    'enabled': True,
    'dry_run': False,
    'allowed_repositories': ['untool-ai/example'],
    'size_tiers': {'xs_max_changed_files': 3, 's_max_changed_files': 10, 'm_max_changed_files': 30},
    'routing': {'xs': 'copilot', 's': 'copilot', 'm': 'jules', 'l': 'jules', 'unsized': 'codex'},
    'agent_labels': {'copilot': 'copilot-task', 'jules': 'jules'},
    'agent_review_mentions': {'codex': '@codex review'},
    'monthly_budget_usd': 10.0,
    'estimated_cost_usd': {'copilot': 0.0, 'jules': 2.0, 'codex': 0.0},
}


class SizeTierTests(unittest.TestCase):
    def test_thresholds(self):
        policy = {'size_tiers': {'xs_max_changed_files': 3, 's_max_changed_files': 10, 'm_max_changed_files': 30}}
        self.assertEqual(size_tier({'changed_files': 1}, policy), 'xs')
        self.assertEqual(size_tier({'changed_files': 3}, policy), 'xs')
        self.assertEqual(size_tier({'changed_files': 4}, policy), 's')
        self.assertEqual(size_tier({'changed_files': 30}, policy), 'm')
        self.assertEqual(size_tier({'changed_files': 31}, policy), 'l')

    def test_unmeasured_is_none_not_zero(self):
        policy = {'size_tiers': {'xs_max_changed_files': 3, 's_max_changed_files': 10, 'm_max_changed_files': 30}}
        self.assertIsNone(size_tier({}, policy))
        self.assertIsNone(size_tier({'changed_files': -1}, policy))
        self.assertIsNone(size_tier({'changed_files': True}, policy))

    def test_invalid_thresholds_raise(self):
        with self.assertRaises(ValueError):
            size_tier({'changed_files': 1}, {'size_tiers': {'xs_max_changed_files': 10, 's_max_changed_files': 3, 'm_max_changed_files': 30}})


class DispatchEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'test.db'
        self.policy = {'allowed_repositories': ['untool-ai/example'], 'dry_run': True, 'dispatch': dict(DISPATCH_POLICY)}
        self.agent = Babysitter(self.path, self.policy)
        self.client = Client()
        self.engine = DispatchEngine(self.agent.store, self.client, self.policy, clock=lambda: 1_700_000_000)

    def tearDown(self):
        self.agent.close()
        self.tmp.cleanup()

    def test_config_category_routes_small_pr_to_copilot_label(self):
        self.agent.observe(event())
        result = self.engine.dispatch(event())
        self.assertEqual(result['state'], 'accepted')
        self.assertEqual(result['agent'], 'copilot')
        self.assertEqual(self.client.labels, [('untool-ai/example', 7, ('copilot-task',))])
        self.assertTrue(self.agent.store.verify())

    def test_medium_pr_routes_to_jules(self):
        self.agent.observe(event(changed_files=15))
        result = self.engine.dispatch(event(changed_files=15))
        self.assertEqual(result['agent'], 'jules')
        self.assertEqual(self.client.labels, [('untool-ai/example', 7, ('jules',))])

    def test_unsized_pr_routes_to_codex_review_mention(self):
        self.agent.observe(event(changed_files=None))
        result = self.engine.dispatch(event(changed_files=None))
        self.assertEqual(result['agent'], 'codex')
        self.assertEqual(self.client.comments, [('untool-ai/example', 7, '@codex review')])

    def test_security_and_healthy_never_dispatched(self):
        security = event()
        security['evidence'] = ['secret leak']
        self.agent.observe(security)
        result = self.engine.dispatch(security)
        self.assertEqual(result['state'], 'denied')
        self.assertEqual(result['reason'], 'Category is not eligible for cloud-agent dispatch')
        self.assertEqual(self.client.labels, [])

        healthy = event(conclusion='success')
        self.agent.observe(healthy)
        self.assertEqual(self.engine.dispatch(healthy)['state'], 'denied')

    def test_no_pull_number_is_denied(self):
        self.agent.observe(event(pull_number=None))
        result = self.engine.dispatch(event(pull_number=None))
        self.assertEqual(result['reason'], 'No associated pull request to dispatch')

    def test_dry_run_never_submits(self):
        self.policy['dispatch']['dry_run'] = True
        self.agent.observe(event())
        result = self.engine.dispatch(event())
        self.assertEqual(result['state'], 'dry-run')
        self.assertEqual(self.client.labels, [])

    def test_duplicate_dispatch_never_double_submits(self):
        self.agent.observe(event())
        self.engine.dispatch(event())
        result = self.engine.dispatch(event())
        self.assertTrue(result['duplicate'])
        self.assertEqual(len(self.client.labels), 1)

    def test_monthly_budget_cap_denies_over_spend(self):
        self.policy['dispatch']['monthly_budget_usd'] = 1.0
        self.agent.observe(event(changed_files=15))
        result = self.engine.dispatch(event(changed_files=15))
        self.assertEqual(result['state'], 'denied')
        self.assertEqual(result['reason'], 'Monthly agent budget exhausted')
        self.assertEqual(self.client.labels, [])

    def test_unknown_agent_cost_refuses_rather_than_assumes_free(self):
        del self.policy['dispatch']['estimated_cost_usd']['jules']
        self.agent.observe(event(changed_files=15))
        result = self.engine.dispatch(event(changed_files=15))
        self.assertEqual(result['reason'], 'Unknown agent cost; refuse rather than assume free')

    def test_not_opted_in_repository_denied(self):
        self.policy['dispatch']['allowed_repositories'] = []
        self.agent.observe(event())
        self.assertEqual(self.engine.dispatch(event())['reason'], 'Repository not opted into dispatch')

    def test_dispatch_disabled_by_default(self):
        self.policy['dispatch']['enabled'] = False
        self.agent.observe(event())
        self.assertEqual(self.engine.dispatch(event())['reason'], 'Dispatch not enabled')

    def test_uncertain_submission_recorded_not_retried_twice(self):
        self.client.error = True
        self.agent.observe(event())
        result = self.engine.dispatch(event())
        self.assertEqual(result['state'], 'uncertain')
        self.engine.dispatch(event())
        self.assertEqual(len(self.client.labels), 0)


if __name__ == '__main__':
    unittest.main()
