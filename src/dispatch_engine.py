"""Cloud-agent dispatch is opt-in, budget-bounded and durable.

Dispatch never merges, reverts or edits code itself: it labels or comments on a pull
request to hand a classified failure to an already-trusted cloud coding agent (GitHub
Copilot coding agent, Jules, Codex), so a human-reviewed PR is produced through the
existing branch-protection and required-status-check path. Security and healthy
classifications are never dispatched; that boundary is enforced here, never trusted
from caller input. Tokenomics/FinOps: every dispatch reserves an explicit estimated
cost against a monthly budget cap before submission, fail-closed on unknown pricing.
"""
import hashlib
import json
import time
from datetime import datetime, timezone

from .babysitter_agent import normalize
from .store import canonical

# transient/flaky are handled by RemediationEngine.retry; security/healthy are never
# handed to an autonomous coding agent.
_DISPATCHABLE_CATEGORIES = frozenset({'config', 'systemic', 'unknown'})


def size_tier(event, policy):
    """Deterministic size tier from a literal changed-file count; None if unmeasured.

    Mirrors failure_classifier's threshold style: explicit configured cutoffs, no
    invented default, and an unmeasured value is never silently coerced to a tier.
    """
    changed_files = event.get('changed_files')
    if not isinstance(changed_files, int) or isinstance(changed_files, bool) or changed_files < 0:
        return None
    tiers = policy.get('size_tiers', {})
    xs_max, s_max, m_max = (tiers.get('xs_max_changed_files'), tiers.get('s_max_changed_files'),
                            tiers.get('m_max_changed_files'))
    if (not all(isinstance(value, int) and not isinstance(value, bool) and value > 0
                for value in (xs_max, s_max, m_max))
            or not xs_max < s_max < m_max):
        raise ValueError('Invalid size tier thresholds')
    if changed_files <= xs_max:
        return 'xs'
    if changed_files <= s_max:
        return 's'
    if changed_files <= m_max:
        return 'm'
    return 'l'


class DispatchEngine:
    def __init__(self, store, client, policy, clock=time.time):
        self.store, self.client, self.policy, self.clock = store, client, policy, clock

    def _refuse(self, event, reason):
        with self.store.transaction():
            self.store.audit('dispatch-not-executed', f"{event['repository']}:{event['run_id']}", {'reason': reason})
        return {'state': 'denied', 'reason': reason, 'executed': False}

    @staticmethod
    def _budget_period(now):
        return datetime.fromtimestamp(now, tz=timezone.utc).strftime('%Y-%m')

    def _spent(self, period):
        row = self.store.db.execute(
            "SELECT COALESCE(SUM(estimated_cost_usd),0) FROM dispatches WHERE budget_period=? AND state IN ('reserved','accepted','uncertain')",
            (period,)).fetchone()
        return row[0]

    def dispatch(self, event, decision=None):
        event = normalize(event)
        identity = {key: event[key] for key in ('repository', 'run_id', 'attempt', 'sha', 'status', 'conclusion')}
        event_key = hashlib.sha256(canonical(identity).encode()).hexdigest()
        recorded = self.store.db.execute('SELECT event_json,decision_json FROM events WHERE event_key=?', (event_key,)).fetchone()
        if not recorded or normalize(json.loads(recorded['event_json'])) != event:
            return self._refuse(event, 'No matching recorded event')
        # Never trust a caller-supplied category. Bind to the immutable recorded classification.
        decision = json.loads(recorded['decision_json'])
        category = decision.get('category')
        if category not in _DISPATCHABLE_CATEGORIES:
            return self._refuse(event, 'Category is not eligible for cloud-agent dispatch')
        policy = self.policy.get('dispatch') or {}
        if policy.get('enabled') is not True:
            return self._refuse(event, 'Dispatch not enabled')
        if event['repository'] not in policy.get('allowed_repositories', []):
            return self._refuse(event, 'Repository not opted into dispatch')
        pull_number = event.get('pull_number')
        if not isinstance(pull_number, int) or isinstance(pull_number, bool) or pull_number <= 0:
            return self._refuse(event, 'No associated pull request to dispatch')
        tier = size_tier(event, policy)
        routing = policy.get('routing', {})
        agent = routing.get(tier) if tier is not None else routing.get('unsized')
        if not isinstance(agent, str) or not agent:
            return self._refuse(event, 'No agent routed for this size tier')
        cost = policy.get('estimated_cost_usd', {}).get(agent)
        if not isinstance(cost, (int, float)) or isinstance(cost, bool) or cost < 0:
            return self._refuse(event, 'Unknown agent cost; refuse rather than assume free')
        cap = policy.get('monthly_budget_usd')
        if not isinstance(cap, (int, float)) or isinstance(cap, bool) or cap < 0:
            return self._refuse(event, 'Invalid monthly budget policy')
        now = self.clock()
        period = self._budget_period(now)
        incident = f"{event['repository']}:pull:{pull_number}"
        key = hashlib.sha256(f"dispatch:{incident}:{event['run_id']}:{event['attempt']}".encode()).hexdigest()
        with self.store.transaction():
            prior = self.store.db.execute('SELECT state FROM dispatches WHERE dispatch_key=?', (key,)).fetchone()
            if prior:
                return {'state': prior['state'], 'duplicate': True, 'dispatch_key': key}
            spent = self._spent(period)
            if spent + cost > cap:
                self.store.audit('dispatch-not-executed', event_key, {
                    'reason': 'Monthly agent budget exhausted', 'period': period,
                    'spent_usd': spent, 'estimated_cost_usd': cost, 'monthly_budget_usd': cap})
                return {'state': 'denied', 'reason': 'Monthly agent budget exhausted'}
            self.store.db.execute('INSERT INTO dispatches VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (key, incident, event['repository'], 'pull_request', pull_number, event_key,
                 category, tier or 'unsized', agent, cost, period, now, 'reserved'))
            self.store.audit('dispatch-reserved', key, {
                'agent': agent, 'size_tier': tier, 'category': category, 'pull_number': pull_number,
                'estimated_cost_usd': cost, 'budget_period': period})
        # Reservation survives crashes; a crash after this boundary requires operator
        # reconciliation, never another blind submission. Acceptance is NOT proof the
        # cloud agent produced a merged fix.
        if policy.get('dry_run', True) is not False:
            with self.store.transaction():
                self.store.db.execute("UPDATE dispatches SET state='rejected' WHERE dispatch_key=? AND state='reserved'", (key,))
                self.store.audit('dispatch-not-executed', key, {'reason': 'Dispatch executor disabled (dry_run)'})
            return {'state': 'dry-run', 'dispatch_key': key, 'agent': agent, 'executed': False}
        try:
            label = policy.get('agent_labels', {}).get(agent)
            if isinstance(label, str) and label:
                self.client.add_labels(event['repository'], pull_number, [label])
            mention = policy.get('agent_review_mentions', {}).get(agent)
            if isinstance(mention, str) and mention:
                self.client.create_comment(event['repository'], pull_number, mention)
            state = 'accepted'
        except Exception as exc:
            state = 'uncertain' if getattr(exc, 'uncertain', True) else 'rejected'
        with self.store.transaction():
            self.store.db.execute("UPDATE dispatches SET state=? WHERE dispatch_key=? AND state='reserved'", (state, key))
            state = self.store.db.execute('SELECT state FROM dispatches WHERE dispatch_key=?', (key,)).fetchone()[0]
            self.store.audit('dispatch-submitted', key, {'state': state, 'agent': agent})
        return {'state': state, 'dispatch_key': key, 'agent': agent, 'executed': state == 'accepted'}
