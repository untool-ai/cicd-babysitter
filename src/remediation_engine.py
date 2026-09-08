"""Retry is opt-in, bounded and durable. High-trust operations fail closed."""
import hashlib
import json
import time

from .babysitter_agent import normalize
from .store import canonical


class RemediationEngine:
    def __init__(self, store, client, policy, clock=time.time):
        self.store, self.client, self.policy, self.clock = store, client, policy, clock

    def _abandoned(self, key):
        return self.store.db.execute(
            "SELECT 1 FROM audit WHERE kind='action-abandoned' AND subject=? LIMIT 1",
            (key,)).fetchone() is not None

    def abandon(self, key, *, operator, reason, executor_quiesced=False):
        """Administrative closure only: never cancel, refund, retry or assert outcome.

        Caller must authenticate the operator and stop/join submitting workers first.
        The quiesced flag is an explicit attestation, not a distributed lock.
        """
        if executor_quiesced is not True:
            raise PermissionError('Stop and join action executors before abandonment')
        if any(not isinstance(v, str) or not v.strip() or len(v) > 256
               for v in (operator, reason)):
            raise ValueError('Bounded operator identity and non-secret reason required')
        with self.store.transaction():
            row = self.store.db.execute('SELECT state FROM actions WHERE action_key=?', (key,)).fetchone()
            if not row:
                raise ValueError('Unknown action')
            if self._abandoned(key):
                return {'state':'abandoned','action_key':key,'duplicate':True,'verified':False}
            if row['state'] not in ('reserved','uncertain'):
                raise ValueError('Only unresolved reserved or uncertain actions may be abandoned')
            self.store.audit('action-abandoned', key, {
                'operator':operator, 'reason':reason, 'prior_state':row['state'],
                'executor_quiesced_attested':True, 'budget_retained':True,
                'outcome':'unknown; abandonment does not cancel provider work'})
        return {'state':'abandoned','action_key':key,'verified':False,'budget_retained':True}

    def _refuse(self, event, state, reason):
        with self.store.transaction():
            self.store.audit('action-not-executed', f"{event['repository']}:{event['run_id']}", {'state':state,'reason':reason})
        return {'state':state,'reason':reason,'executed':False}

    def retry(self, event, decision=None):
        event = normalize(event)
        identity = {k:event[k] for k in ('repository','run_id','attempt','sha','status','conclusion')}
        event_key = hashlib.sha256(canonical(identity).encode()).hexdigest()
        recorded = self.store.db.execute('SELECT event_json,decision_json FROM events WHERE event_key=?',(event_key,)).fetchone()
        if not recorded or normalize(json.loads(recorded['event_json'])) != event:
            return self._refuse(event,'denied','No matching recorded event')
        # Never trust a caller's proposed category. Bind to immutable classification.
        decision = json.loads(recorded['decision_json'])
        if decision.get('action') != 'retry' or decision.get('category') not in ('transient','flaky'):
            return self._refuse(event,'denied','Not a retry classification')
        if self.policy.get('dry_run', True) is not False:
            return self._refuse(event,'dry-run','Executor disabled')
        if event['repository'] not in self.policy.get('allowed_repositories', []) or str(event['workflow_id']) not in [str(x) for x in self.policy.get('allowed_workflows', [])]:
            return self._refuse(event,'denied','Not opted in')
        if event['status'] != 'completed' or event['conclusion'] not in ('failure','timed_out','startup_failure'):
            return self._refuse(event,'denied','Not a failed completed run')
        cap = self.policy.get('max_retries', 3)
        backoff = self.policy.get('retry_backoff_seconds', 60)
        reservation_timeout = self.policy.get('reservation_timeout_seconds', 900)
        if type(cap) is not int or not 0 <= cap <= 3 or type(backoff) not in (int,float) or not 1 <= backoff <= 86400:
            raise ValueError('Invalid retry policy')
        if type(reservation_timeout) is not int or not 1 <= reservation_timeout <= 86400:
            raise ValueError('Invalid reservation policy')
        current = self.client.get_run(event['repository'], event['run_id'])
        if current.get('status') != 'completed' or current.get('conclusion') not in ('failure','timed_out','startup_failure') or current.get('head_sha') != event['sha'] or current.get('run_attempt') != event['attempt'] or current.get('workflow_id') != event['workflow_id']:
            return self._refuse(event,'denied','Current workflow identity/state changed')
        incident = f"{event['repository']}:{event['run_id']}:{event['sha']}"
        key = hashlib.sha256(f"retry:{incident}:{event['attempt']}".encode()).hexdigest()
        now = self.clock()
        with self.store.transaction():
            prior = self.store.db.execute('SELECT state,created FROM actions WHERE action_key=?', (key,)).fetchone()
            if prior:
                if (prior['state'] == 'reserved'
                        and now >= prior['created'] + reservation_timeout):
                    # A crash may have occurred before the provider call or after it.
                    # Mark the reservation uncertain rather than retrying blindly.
                    self.store.db.execute(
                        "UPDATE actions SET state='uncertain' WHERE action_key=? AND state='reserved'",
                        (key,))
                    self.store.audit('action-reservation-recovered', key, {
                        'prior_state': 'reserved', 'state': 'uncertain',
                        'reservation_age_seconds': now - prior['created'],
                        'reason': 'reservation lease expired; provider outcome unknown'})
                    return {'state': 'uncertain', 'duplicate': True,
                            'recovered': True, 'action_key': key}
                return {'state': 'abandoned' if self._abandoned(key) else prior['state'],
                        'duplicate': True, 'action_key': key}
            used = self.store.db.execute('SELECT COUNT(*),MAX(created) FROM actions WHERE incident=?', (incident,)).fetchone()
            if used[0] >= cap:
                self.store.audit('action-not-executed',event_key,{'reason':'Retry budget exhausted'})
                return {'state': 'denied', 'reason': 'Retry budget exhausted'}
            if used[1] is not None and now < used[1] + backoff * 2 ** (used[0] - 1):
                self.store.audit('action-not-executed',event_key,{'reason':'Backoff not elapsed'})
                return {'state': 'deferred', 'not_before': used[1] + backoff * 2 ** (used[0] - 1)}
            self.store.db.execute('INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,?)',
                (key,incident,event['repository'],event['run_id'],event['attempt'],event['sha'],event_key,now,'reserved'))
            self.store.audit('action-reserved', key, {'action':'retry','incident':incident,'attempt':event['attempt'],'event_key':event_key})
        # Reservation survives crashes. A crash after this boundary requires reconciliation,
        # never another blind submission. Acceptance is NOT successful remediation.
        try:
            self.client.retry_run(event['repository'], event['run_id'])
            state = 'accepted'
        except Exception as exc:
            state = 'uncertain' if getattr(exc, 'uncertain', True) else 'rejected'
        with self.store.transaction():
            self.store.db.execute("UPDATE actions SET state=? WHERE action_key=? AND state='reserved'", (state,key))
            state = self.store.db.execute('SELECT state FROM actions WHERE action_key=?',(key,)).fetchone()[0]
            self.store.audit('action-submitted', key, {'state':state})
        return {'state':state,'action_key':key,'verified':state in ('verified_success','verified_failure')}

    def reconcile(self, key):
        row = self.store.db.execute('SELECT * FROM actions WHERE action_key=?', (key,)).fetchone()
        if not row: raise ValueError('Unknown action')
        if self._abandoned(key):
            return {'state':'abandoned','verified':False,'budget_retained':True}
        if row['state'] in ('verified_success','verified_failure','rejected'):
            return {'state':row['state']}
        current = self.client.get_run(row['repository'], row['run_id'])
        if current.get('head_sha') != row['sha'] or current.get('run_attempt') != row['attempt'] + 1 or current.get('status') != 'completed':
            return {'state':row['state'],'verified':False}
        conclusion = current.get('conclusion')
        if conclusion not in ('success','failure','timed_out','startup_failure'):
            return {'state':row['state'],'verified':False}
        state = 'verified_success' if conclusion == 'success' else 'verified_failure'
        with self.store.transaction():
            latest = self.store.db.execute('SELECT state FROM actions WHERE action_key=?',(key,)).fetchone()[0]
            if latest not in ('verified_success','verified_failure'):
                self.store.db.execute('UPDATE actions SET state=? WHERE action_key=?',(state,key))
                self.store.audit('outcome',key,{'state':state,'run_attempt':current['run_attempt'], 'causality':'subsequent-run-observed; external reruns may contribute'})
        return {'state':state,'verified':True}

    def execute(self, action, *args, **kwargs):
        if action != 'retry':
            raise PermissionError('High-trust actions require separately approved implementation and policy')
        return self.retry(*args, **kwargs)
