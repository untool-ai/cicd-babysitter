import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path

from src.babysitter_agent import Babysitter
from src.remediation_engine import RemediationEngine


def event(attempt=1):
    return dict(repository='untool-ai/example',run_id=1,attempt=attempt,sha='a'*40,workflow_id=2,branch='main',status='completed',conclusion='failure',evidence=['connection reset'],history=[])


class Client:
    def __init__(self):
        self.current=dict(status='completed',conclusion='failure',head_sha='a'*40,run_attempt=1,workflow_id=2)
        self.calls=0
        self.error=False
    def get_run(self,*args): return self.current
    def retry_run(self,*args):
        self.calls+=1
        if self.error: raise TimeoutError('fixture sensitive text must not be retained')


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'test.db'
        self.policy=dict(allowed_repositories=['untool-ai/example'],allowed_workflows=['2'],dry_run=False,max_retries=3,retry_backoff_seconds=1)
        self.agent=Babysitter(self.path,self.policy)
        self.client=Client()
        self.engine=RemediationEngine(self.agent.store,self.client,self.policy,clock=lambda:100)
        self.decision={'category':'transient','action':'retry'}
        self.agent.observe(event())
    def tearDown(self):
        self.agent.close();self.tmp.cleanup()
    def test_persistent_duplicate_and_accepted_not_success(self):
        result=self.engine.retry(event(),self.decision)
        self.assertEqual(result['state'],'accepted');self.assertFalse(result['verified'])
        self.agent.close();self.agent=Babysitter(self.path,self.policy)
        self.engine=RemediationEngine(self.agent.store,self.client,self.policy)
        self.assertTrue(self.engine.retry(event(),self.decision)['duplicate'])
        self.assertEqual(self.client.calls,1)
        self.assertTrue(self.agent.store.verify())
    def test_unknown_submission_never_retried(self):
        self.client.error=True
        result=self.engine.retry(event(),self.decision)
        self.assertEqual(result['state'],'uncertain')
        self.engine.retry(event(),self.decision)
        self.assertEqual(self.client.calls,1)
        self.assertNotIn('sensitive',json.dumps(self.agent.store.export()))
    def test_three_retry_cap_and_backoff(self):
        for i in range(1,4):
            self.agent.observe(event(i))
            self.client.current['run_attempt']=i
            self.engine.clock=lambda i=i:100+i*100
            self.assertEqual(self.engine.retry(event(i),self.decision)['state'],'accepted')
        self.client.current['run_attempt']=4
        self.agent.observe(event(4))
        self.assertEqual(self.engine.retry(event(4),self.decision)['reason'],'Retry budget exhausted')
        self.assertEqual(self.client.calls,3)
    def test_backoff_defers_without_consuming_budget(self):
        self.engine.retry(event(),self.decision)
        self.client.current['run_attempt']=2
        self.agent.observe(event(2))
        self.assertEqual(self.engine.retry(event(2),self.decision)['state'],'deferred')
        self.assertEqual(self.client.calls,1)
    def test_dry_run_and_high_trust_denial(self):
        self.policy['dry_run']=True
        self.assertEqual(self.engine.retry(event(),self.decision)['state'],'dry-run')
        for name in ('merge','revert','update','quarantine'):
            with self.assertRaises(PermissionError): self.engine.execute(name)
        self.assertEqual(self.client.calls,0)
    def test_current_state_commit_and_workflow_rechecked(self):
        for field,value in [('status','in_progress'),('head_sha','b'*40),('run_attempt',2),('workflow_id',3)]:
            old=self.client.current[field]; self.client.current[field]=value
            self.assertEqual(self.engine.retry(event(),self.decision)['state'],'denied')
            self.client.current[field]=old
        self.assertEqual(self.client.calls,0)
    def test_reconciliation_requires_next_attempt(self):
        result=self.engine.retry(event(),self.decision)
        self.client.current.update(conclusion='success')
        self.assertFalse(self.engine.reconcile(result['action_key'])['verified'])
        self.client.current.update(run_attempt=2)
        self.assertEqual(self.engine.reconcile(result['action_key'])['state'],'verified_success')
    def test_audit_failure_prevents_external_action(self):
        self.agent.store.audit=lambda *args: (_ for _ in ()).throw(OSError('fixture'))
        with self.assertRaises(OSError): self.engine.retry(event(),self.decision)
        self.assertEqual(self.client.calls,0)
        self.assertEqual(self.agent.store.db.execute('SELECT COUNT(*) FROM actions').fetchone()[0],0)
    def test_webhook_replay_and_attempt_separation(self):
        raw=json.dumps({'repository':{'full_name':'untool-ai/example'},'workflow_run':dict(id=1,run_attempt=1,workflow_id=2,head_sha='a'*40,head_branch='main',status='completed',conclusion='failure')}).encode()
        secret=b'fixture-secret'
        signature='sha256='+hmac.new(secret,raw,hashlib.sha256).hexdigest()
        self.agent.ingest(raw,signature,'delivery-1','workflow_run',secret)
        self.assertTrue(self.agent.ingest(raw,signature,'changed-header','workflow_run',secret)['duplicate'])
        self.assertEqual(len(self.agent.store.export()['events']),1)
        with self.assertRaises(PermissionError): self.agent.ingest(raw,'bad','another','workflow_run',secret)
    def test_malformed_lifecycle_is_not_stored(self):
        bad=event();bad['status']={}
        with self.assertRaises(ValueError): self.agent.observe(bad)
        self.assertEqual(len(self.agent.store.export()['events']),1)

    def test_forged_decision_cannot_override_security(self):
        security=event(2);security['evidence']=['secret leak']
        result=self.agent.observe(security)
        self.assertEqual(result['decision']['category'],'security')
        self.client.current['run_attempt']=2
        self.assertEqual(self.engine.retry(security,self.decision)['state'],'denied')
        self.assertEqual(self.client.calls,0)

    def test_reconciliation_during_submit_preserves_terminal_state(self):
        def submit(*args):
            self.client.calls+=1
            key=self.agent.store.db.execute('SELECT action_key FROM actions').fetchone()[0]
            self.client.current.update(run_attempt=2,conclusion='success')
            self.engine.reconcile(key)
        self.client.retry_run=submit
        result=self.engine.retry(event(),self.decision)
        self.assertEqual(result['state'],'verified_success')

    def test_abandon_uncertain_is_durable_no_refund_no_resubmit(self):
        self.client.error=True
        key=self.engine.retry(event())['action_key']
        with self.assertRaises(PermissionError):
            self.engine.abandon(key,operator='operator-1',reason='reviewed')
        result=self.engine.abandon(key,operator='operator-1',reason='provider outcome unavailable',executor_quiesced=True)
        self.assertEqual(result['state'],'abandoned')
        self.assertTrue(result['budget_retained'])
        self.assertTrue(self.engine.abandon(key,operator='operator-1',reason='reviewed',executor_quiesced=True)['duplicate'])
        self.assertEqual(self.engine.retry(event())['state'],'abandoned')
        self.assertEqual(self.client.calls,1)
        self.agent.close();self.agent=Babysitter(self.path,self.policy)
        self.engine=RemediationEngine(self.agent.store,self.client,self.policy)
        self.assertEqual(self.engine.reconcile(key)['state'],'abandoned')
        self.policy['max_retries']=1
        self.client.current['run_attempt']=2
        self.agent.observe(event(2))
        self.assertEqual(self.engine.retry(event(2))['reason'],'Retry budget exhausted')
        self.assertTrue(self.agent.store.verify())

    def test_abandon_reserved_is_atomic_and_does_not_claim_provider_rejection(self):
        key=self.engine.retry(event())['action_key']
        self.agent.store.db.execute("UPDATE actions SET state='reserved' WHERE action_key=?",(key,))
        original=self.agent.store.audit
        self.agent.store.audit=lambda *args: (_ for _ in ()).throw(OSError('fixture'))
        with self.assertRaises(OSError):
            self.engine.abandon(key,operator='operator-1',reason='reviewed',executor_quiesced=True)
        self.assertFalse(self.engine._abandoned(key))
        self.agent.store.audit=original
        self.engine.abandon(key,operator='operator-1',reason='reviewed',executor_quiesced=True)
        self.assertEqual(self.agent.store.db.execute('SELECT state FROM actions').fetchone()[0],'reserved')
        self.assertEqual(self.engine.reconcile(key)['state'],'abandoned')

    def test_abandon_rejects_known_outcomes_and_missing_identity(self):
        key=self.engine.retry(event())['action_key']
        with self.assertRaises(ValueError):
            self.engine.abandon(key,operator='operator-1',reason='reviewed',executor_quiesced=True)
        with self.assertRaises(ValueError):
            self.engine.abandon(key,operator='',reason='reviewed',executor_quiesced=True)


if __name__=='__main__': unittest.main()
