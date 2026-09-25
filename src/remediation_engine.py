"""Policy-gated remediation. Retry, issue creation, and merge are opt-in and durable.

High-trust operations fail closed unless explicitly implemented, allowed by policy,
and bound to an immutable recorded decision. Uncertain submissions never resubmit.
"""
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
                return {'state': 'abandoned', 'action_key': key, 'duplicate': True, 'verified': False}
            if row['state'] not in ('reserved', 'uncertain'):
                raise ValueError('Only unresolved reserved or uncertain actions may be abandoned')
            self.store.audit('action-abandoned', key, {
                'operator': operator, 'reason': reason, 'prior_state': row['state'],
                'executor_quiesced_attested': True, 'budget_retained': True,
                'outcome': 'unknown; abandonment does not cancel provider work'})
        return {'state': 'abandoned', 'action_key': key, 'verified': False, 'budget_retained': True}

    def _refuse(self, subject, state, reason):
        with self.store.transaction():
            self.store.audit('action-not-executed', subject, {'state': state, 'reason': reason})
        return {'state': state, 'reason': reason, 'executed': False}

    def _reservation_timeout(self):
        reservation_timeout = self.policy.get('reservation_timeout_seconds', 900)
        if type(reservation_timeout) is not int or not 1 <= reservation_timeout <= 86400:
            raise ValueError('Invalid reservation policy')
        return reservation_timeout

    def _recover_or_duplicate(self, key, now):
        prior = self.store.db.execute(
            'SELECT state,created FROM actions WHERE action_key=?', (key,)).fetchone()
        if not prior:
            return None
        if (prior['state'] == 'reserved'
                and now >= prior['created'] + self._reservation_timeout()):
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

    def _insert_action(self, *, key, action_type, incident, repository, event_key,
                       run_id=0, attempt=0, sha='', pr_number=None, detail=None):
        now = self.clock()
        self.store.db.execute(
            'INSERT INTO actions(action_key,action_type,incident,repository,run_id,attempt,sha,'
            'pr_number,event_key,created,state,detail_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (key, action_type, incident, repository, run_id, attempt, sha, pr_number,
             event_key, now, 'reserved', canonical(detail or {})))
        self.store.audit('action-reserved', key, {
            'action': action_type, 'incident': incident, 'event_key': event_key,
            'pr_number': pr_number, 'attempt': attempt})

    def _submit(self, key, mutate):
        try:
            mutate()
            state = 'accepted'
        except Exception as exc:
            state = 'uncertain' if getattr(exc, 'uncertain', True) else 'rejected'
        with self.store.transaction():
            self.store.db.execute(
                "UPDATE actions SET state=? WHERE action_key=? AND state='reserved'", (state, key))
            state = self.store.db.execute(
                'SELECT state FROM actions WHERE action_key=?', (key,)).fetchone()[0]
            self.store.audit('action-submitted', key, {'state': state})
        return {
            'state': state, 'action_key': key,
            'verified': state in ('verified_success', 'verified_failure')}

    def retry(self, event, decision=None):
        event = normalize(event)
        identity = {k: event[k] for k in ('repository', 'run_id', 'attempt', 'sha', 'status', 'conclusion')}
        event_key = hashlib.sha256(canonical(identity).encode()).hexdigest()
        recorded = self.store.db.execute(
            'SELECT event_json,decision_json FROM events WHERE event_key=?', (event_key,)).fetchone()
        if not recorded or normalize(json.loads(recorded['event_json'])) != event:
            return self._refuse(
                f"{event['repository']}:{event['run_id']}", 'denied', 'No matching recorded event')
        # Never trust a caller's proposed category. Bind to immutable classification.
        decision = json.loads(recorded['decision_json'])
        if decision.get('action') != 'retry' or decision.get('category') not in ('transient', 'flaky'):
            return self._refuse(
                f"{event['repository']}:{event['run_id']}", 'denied', 'Not a retry classification')
        if self.policy.get('dry_run', True) is not False:
            return self._refuse(
                f"{event['repository']}:{event['run_id']}", 'dry-run', 'Executor disabled')
        if (event['repository'] not in self.policy.get('allowed_repositories', [])
                or str(event['workflow_id']) not in [
                    str(x) for x in self.policy.get('allowed_workflows', [])]):
            return self._refuse(
                f"{event['repository']}:{event['run_id']}", 'denied', 'Not opted in')
        if event['status'] != 'completed' or event['conclusion'] not in (
                'failure', 'timed_out', 'startup_failure'):
            return self._refuse(
                f"{event['repository']}:{event['run_id']}", 'denied', 'Not a failed completed run')
        cap = self.policy.get('max_retries', 3)
        backoff = self.policy.get('retry_backoff_seconds', 60)
        if (type(cap) is not int or not 0 <= cap <= 3
                or type(backoff) not in (int, float) or not 1 <= backoff <= 86400):
            raise ValueError('Invalid retry policy')
        current = self.client.get_run(event['repository'], event['run_id'])
        if (current.get('status') != 'completed'
                or current.get('conclusion') not in ('failure', 'timed_out', 'startup_failure')
                or current.get('head_sha') != event['sha']
                or current.get('run_attempt') != event['attempt']
                or current.get('workflow_id') != event['workflow_id']):
            return self._refuse(
                f"{event['repository']}:{event['run_id']}", 'denied',
                'Current workflow identity/state changed')
        incident = f"{event['repository']}:{event['run_id']}:{event['sha']}"
        key = hashlib.sha256(f"retry:{incident}:{event['attempt']}".encode()).hexdigest()
        now = self.clock()
        with self.store.transaction():
            recovered = self._recover_or_duplicate(key, now)
            if recovered is not None:
                return recovered
            used = self.store.db.execute(
                "SELECT COUNT(*),MAX(created) FROM actions WHERE incident=? AND action_type='retry'",
                (incident,)).fetchone()
            if used[0] >= cap:
                self.store.audit('action-not-executed', event_key, {'reason': 'Retry budget exhausted'})
                return {'state': 'denied', 'reason': 'Retry budget exhausted'}
            if used[1] is not None and now < used[1] + backoff * 2 ** (used[0] - 1):
                self.store.audit('action-not-executed', event_key, {'reason': 'Backoff not elapsed'})
                return {'state': 'deferred', 'not_before': used[1] + backoff * 2 ** (used[0] - 1)}
            self._insert_action(
                key=key, action_type='retry', incident=incident, repository=event['repository'],
                event_key=event_key, run_id=event['run_id'], attempt=event['attempt'],
                sha=event['sha'])
        return self._submit(key, lambda: self.client.retry_run(event['repository'], event['run_id']))

    def create_issue(self, event, *, title=None, body=None):
        """Open a deduplicated investigation issue bound to a recorded decision."""
        event = normalize(event)
        identity = {k: event[k] for k in ('repository', 'run_id', 'attempt', 'sha', 'status', 'conclusion')}
        event_key = hashlib.sha256(canonical(identity).encode()).hexdigest()
        recorded = self.store.db.execute(
            'SELECT event_json,decision_json FROM events WHERE event_key=?', (event_key,)).fetchone()
        if not recorded or normalize(json.loads(recorded['event_json'])) != event:
            return self._refuse(
                f"{event['repository']}:{event['run_id']}", 'denied', 'No matching recorded event')
        decision = json.loads(recorded['decision_json'])
        if decision.get('action') not in ('investigate', 'escalate') and decision.get('category') not in (
                'config', 'systemic', 'security', 'flaky', 'unknown'):
            return self._refuse(
                f"{event['repository']}:{event['run_id']}", 'denied',
                'Not an investigation classification')
        if self.policy.get('dry_run', True) is not False:
            return self._refuse(
                f"{event['repository']}:{event['run_id']}", 'dry-run', 'Executor disabled')
        if self.policy.get('enable_issues') is not True:
            return self._refuse(
                f"{event['repository']}:{event['run_id']}", 'denied', 'Issue creation disabled')
        if event['repository'] not in self.policy.get('allowed_repositories', []):
            return self._refuse(
                f"{event['repository']}:{event['run_id']}", 'denied', 'Not opted in')
        incident = (
            f"issue:{event['repository']}:{event['run_id']}:{event['sha']}:"
            f"{decision.get('category')}")
        key = hashlib.sha256(incident.encode()).hexdigest()
        now = self.clock()
        issue_title = title or (
            f"[babysitter] {decision.get('category', 'unknown')} failure in "
            f"{event['repository']} run {event['run_id']}")
        issue_body = body or (
            "Automated investigation proposal.\n\n"
            f"- repository: `{event['repository']}`\n"
            f"- run_id: `{event['run_id']}`\n"
            f"- sha: `{event['sha']}`\n"
            f"- category: `{decision.get('category')}`\n"
            f"- rule: `{decision.get('rule')}`\n"
            f"- reason: {decision.get('reason')}\n\n"
            "Raw logs intentionally omitted. Fetch diagnostics from the provider."
        )
        if not isinstance(issue_title, str) or not issue_title.strip() or len(issue_title) > 256:
            raise ValueError('Invalid issue title')
        if not isinstance(issue_body, str) or len(issue_body) > 65536:
            raise ValueError('Invalid issue body')
        with self.store.transaction():
            recovered = self._recover_or_duplicate(key, now)
            if recovered is not None:
                return recovered
            self._insert_action(
                key=key, action_type='create_issue', incident=incident,
                repository=event['repository'], event_key=event_key,
                run_id=event['run_id'], attempt=event['attempt'], sha=event['sha'],
                detail={'title': issue_title})
        result_holder = {}

        def mutate():
            created = self.client.create_issue(event['repository'], issue_title, issue_body)
            result_holder['issue_number'] = created.get('number')

        outcome = self._submit(key, mutate)
        if result_holder.get('issue_number') is not None:
            outcome['issue_number'] = result_holder['issue_number']
            with self.store.transaction():
                self.store.db.execute(
                    "UPDATE actions SET detail_json=?, state=? WHERE action_key=? AND state='accepted'",
                    (canonical({'title': issue_title, 'issue_number': result_holder['issue_number']}),
                     'verified_success', key))
                latest = self.store.db.execute(
                    'SELECT state FROM actions WHERE action_key=?', (key,)).fetchone()[0]
                if latest == 'verified_success':
                    self.store.audit('outcome', key, {
                        'state': 'verified_success',
                        'issue_number': result_holder['issue_number'],
                        'causality': 'provider-returned-issue-number'})
                    outcome['state'] = 'verified_success'
                    outcome['verified'] = True
        return outcome

    def merge(self, pr_event, *, approval=None):
        """Merge a recorded ready PR under explicit trust and approval policy.

        Requires dry_run=false, repository allowlist, enable_merge=true, live
        readiness recheck, trusted author or human approval, and sha binding.
        """
        if not isinstance(pr_event, dict):
            raise ValueError('PR event must be an object')
        repository = pr_event.get('repository')
        number = pr_event.get('number')
        head_sha = pr_event.get('head_sha')
        if not isinstance(repository, str) or type(number) is not int or number <= 0:
            raise ValueError('Invalid pull request identity')
        if not isinstance(head_sha, str) or len(head_sha) != 40:
            raise ValueError('Invalid head sha')
        identity = {
            'kind': 'pull_request', 'repository': repository,
            'number': number, 'head_sha': head_sha,
        }
        event_key = hashlib.sha256(canonical(identity).encode()).hexdigest()
        recorded = self.store.db.execute(
            'SELECT event_json,decision_json FROM events WHERE event_key=?', (event_key,)).fetchone()
        if not recorded:
            return self._refuse(
                f"{repository}:pr-{number}", 'denied', 'No matching recorded PR event')
        stored = json.loads(recorded['event_json'])
        decision = json.loads(recorded['decision_json'])
        if (stored.get('kind') != 'pull_request'
                or stored.get('repository') != repository
                or stored.get('number') != number
                or stored.get('head_sha') != head_sha):
            return self._refuse(
                f"{repository}:pr-{number}", 'denied', 'Recorded PR identity mismatch')
        if decision.get('action') != 'merge':
            return self._refuse(
                f"{repository}:pr-{number}", 'denied', 'Not a merge classification')
        if self.policy.get('dry_run', True) is not False:
            return self._refuse(f"{repository}:pr-{number}", 'dry-run', 'Executor disabled')
        if self.policy.get('enable_merge') is not True:
            return self._refuse(f"{repository}:pr-{number}", 'denied', 'Merge disabled')
        allowed = self.policy.get('allowed_merge_repositories') or self.policy.get(
            'allowed_repositories', [])
        if repository not in allowed:
            return self._refuse(
                f"{repository}:pr-{number}", 'denied', 'Repository not merge-opted-in')

        merge_method = self.policy.get('merge_method', 'squash')
        if merge_method not in ('merge', 'squash', 'rebase'):
            raise ValueError('Invalid merge method policy')
        required_approvals = self.policy.get('merge_required_approvals', 0)
        if type(required_approvals) is not int or not 0 <= required_approvals <= 10:
            raise ValueError('Invalid merge approval policy')
        trusted_authors = self.policy.get('trusted_merge_authors', [])
        if (not isinstance(trusted_authors, list)
                or not all(isinstance(a, str) and a.strip() for a in trusted_authors)):
            raise ValueError('Invalid trusted merge authors')
        require_human_approval = self.policy.get('merge_require_human_approval', True) is not False

        incident = f"merge:{repository}:{number}:{head_sha}"
        key = hashlib.sha256(incident.encode()).hexdigest()
        now = self.clock()
        # Idempotent before live recheck: never re-evaluate a reserved/submitted merge head.
        with self.store.transaction():
            recovered = self._recover_or_duplicate(key, now)
            if recovered is not None:
                return recovered

        # Live recheck — never trust the recorded snapshot alone.
        from .pr_readiness import evaluate_pull
        live = evaluate_pull(self.client, repository, number)
        if live.get('merged'):
            return self._refuse(f"{repository}:pr-{number}", 'denied', 'Already merged')
        if not live.get('ready'):
            return self._refuse(
                f"{repository}:pr-{number}", 'denied',
                'Not ready: ' + ','.join(live.get('blockers') or ['unknown']))
        if live.get('head_sha') != head_sha:
            return self._refuse(f"{repository}:pr-{number}", 'denied', 'Head sha changed')
        if live.get('approvals', 0) < required_approvals:
            return self._refuse(f"{repository}:pr-{number}", 'denied', 'Insufficient approvals')

        author = live.get('author')
        trusted = isinstance(author, str) and author in trusted_authors
        human_ok = False
        if isinstance(approval, dict):
            operator = approval.get('operator')
            reason = approval.get('reason')
            if (isinstance(operator, str) and operator.strip() and len(operator) <= 256
                    and isinstance(reason, str) and reason.strip() and len(reason) <= 256):
                human_ok = True
        if require_human_approval and not human_ok and not trusted:
            return self._refuse(
                f"{repository}:pr-{number}", 'denied',
                'Human approval or trusted author required')
        if not require_human_approval and not trusted and not human_ok:
            return self._refuse(
                f"{repository}:pr-{number}", 'denied',
                'Trusted author or approval required')

        detail = {
            'number': number, 'head_sha': head_sha, 'merge_method': merge_method,
            'author': author, 'trusted_author': trusted,
            'human_approval': bool(human_ok),
            'operator': (approval or {}).get('operator') if human_ok else None,
        }
        with self.store.transaction():
            recovered = self._recover_or_duplicate(key, now)
            if recovered is not None:
                return recovered
            self._insert_action(
                key=key, action_type='merge', incident=incident, repository=repository,
                event_key=event_key, sha=head_sha, pr_number=number, detail=detail)

        def mutate():
            self.client.merge_pull(
                repository, number, head_sha=head_sha, merge_method=merge_method)

        return self._submit(key, mutate)

    def reconcile(self, key):
        row = self.store.db.execute('SELECT * FROM actions WHERE action_key=?', (key,)).fetchone()
        if not row:
            raise ValueError('Unknown action')
        if self._abandoned(key):
            return {'state': 'abandoned', 'verified': False, 'budget_retained': True}
        if row['state'] in ('verified_success', 'verified_failure', 'rejected'):
            return {'state': row['state']}
        action_type = row['action_type'] if 'action_type' in row.keys() else 'retry'

        if action_type == 'retry':
            current = self.client.get_run(row['repository'], row['run_id'])
            if (current.get('head_sha') != row['sha']
                    or current.get('run_attempt') != row['attempt'] + 1
                    or current.get('status') != 'completed'):
                return {'state': row['state'], 'verified': False}
            conclusion = current.get('conclusion')
            if conclusion not in ('success', 'failure', 'timed_out', 'startup_failure'):
                return {'state': row['state'], 'verified': False}
            state = 'verified_success' if conclusion == 'success' else 'verified_failure'
            evidence = {
                'state': state, 'run_attempt': current['run_attempt'],
                'causality': 'subsequent-run-observed; external reruns may contribute'}
        elif action_type == 'merge':
            number = row['pr_number']
            if type(number) is not int or number <= 0:
                return {'state': row['state'], 'verified': False}
            pull = self.client.get_pull(row['repository'], number)
            if pull.get('merged') is True:
                state = 'verified_success'
                evidence = {
                    'state': state, 'pr_number': number,
                    'merge_commit_sha': pull.get('merge_commit_sha'),
                    'causality': 'provider-reported-merged'}
            elif pull.get('state') == 'closed' and pull.get('merged') is not True:
                state = 'verified_failure'
                evidence = {
                    'state': state, 'pr_number': number,
                    'causality': 'closed-without-merge'}
            else:
                return {'state': row['state'], 'verified': False}
        elif action_type == 'create_issue':
            return {
                'state': row['state'],
                'verified': row['state'] in ('verified_success', 'verified_failure')}
        else:
            return {'state': row['state'], 'verified': False}

        with self.store.transaction():
            latest = self.store.db.execute(
                'SELECT state FROM actions WHERE action_key=?', (key,)).fetchone()[0]
            if latest not in ('verified_success', 'verified_failure'):
                self.store.db.execute(
                    'UPDATE actions SET state=? WHERE action_key=?', (state, key))
                self.store.audit('outcome', key, evidence)
        return {'state': state, 'verified': True}

    def execute(self, action, *args, **kwargs):
        handlers = {
            'retry': self.retry,
            'create_issue': self.create_issue,
            'merge': self.merge,
        }
        handler = handlers.get(action)
        if handler is None:
            raise PermissionError(
                'High-trust action requires separately approved implementation and policy: '
                + str(action))
        return handler(*args, **kwargs)
