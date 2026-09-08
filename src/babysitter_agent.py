"""Durable signed webhook/poll intake; never performs mutations in request handling."""
import hashlib
import hmac
import json
import re

from .failure_classifier import classify
from .store import Store, canonical

CONCLUSIONS = ('success','failure','neutral','cancelled','skipped','timed_out','action_required','stale','startup_failure')
STATUSES = ('queued','in_progress','completed','waiting','pending','requested')


def normalize(event):
    if not isinstance(event, dict):
        raise ValueError('Event must be an object')
    repository = event.get('repository')
    if not isinstance(repository, str) or not re.fullmatch(r'untool-ai/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Invalid repository')
    for field in ('run_id', 'attempt', 'workflow_id'):
        if type(event.get(field)) is not int or event[field] <= 0:
            raise ValueError('Invalid run identity')
    if not isinstance(event.get('sha'), str) or not re.fullmatch(r'[a-f0-9]{40}', event['sha']):
        raise ValueError('Invalid commit')
    status, conclusion = event.get('status'), event.get('conclusion')
    if status not in STATUSES or (status == 'completed' and conclusion not in CONCLUSIONS) or (status != 'completed' and conclusion is not None):
        raise ValueError('Invalid lifecycle')
    branch = event.get('branch')
    if not isinstance(branch, str) or not 1 <= len(branch) <= 255:
        raise ValueError('Invalid branch')
    result = {key: event.get(key) for key in ('repository','run_id','attempt','workflow_id','sha','branch','status','conclusion')}
    # Preserve only bounded metadata, never raw logs, actor objects or tokens.
    for key in ('created_at', 'updated_at'):
        value = event.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > 40):
            raise ValueError('Invalid timestamp')
        result[key] = value
    return result


class Babysitter:
    def __init__(self, database, policy):
        self.store = Store(database)
        self.policy = policy

    def close(self):
        self.store.close()

    def observe(self, event, delivery_id=None, payload_sha=None):
        clean = normalize(event)
        if clean['repository'] not in self.policy.get('allowed_repositories', []):
            raise PermissionError('Repository not opted in')
        identity = {k: clean[k] for k in ('repository','run_id','attempt','sha','status','conclusion')}
        key = hashlib.sha256(canonical(identity).encode()).hexdigest()
        decision = classify({**clean, 'evidence': event.get('evidence', []), 'history': event.get('history', [])}, policy=self.policy)
        decision.update(mode='proposal', executed=False)
        return self.store.record(key, delivery_id or 'poll-' + key, payload_sha or hashlib.sha256(canonical(clean).encode()).hexdigest(), clean, decision)

    def ingest(self, raw, signature, delivery_id, event_type, secret):
        if not isinstance(secret, bytes) or not secret:
            raise PermissionError('Webhook verification unavailable')
        if not isinstance(raw, bytes) or len(raw) > 1_000_000:
            raise ValueError('Invalid payload size')
        expected = 'sha256=' + hmac.new(secret, raw, hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
            raise PermissionError('Invalid signature')
        if event_type != 'workflow_run' or not isinstance(delivery_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', delivery_id):
            raise ValueError('Unsupported delivery')
        payload = json.loads(raw)
        if not isinstance(payload, dict) or not isinstance(payload.get('repository'), dict) or not isinstance(payload.get('workflow_run'), dict):
            raise ValueError('Invalid event')
        run = payload['workflow_run']
        event = {'repository': payload['repository'].get('full_name'), 'run_id': run.get('id'),
                 'attempt': run.get('run_attempt'), 'workflow_id': run.get('workflow_id'), 'sha': run.get('head_sha'),
                 'branch': run.get('head_branch'), 'status': run.get('status'), 'conclusion': run.get('conclusion'),
                 'created_at': run.get('created_at'), 'updated_at': run.get('updated_at'), 'evidence': [], 'history': []}
        # Payload text is not trusted diagnostic evidence. Fetch diagnostics separately.
        return self.observe(event, delivery_id, hashlib.sha256(raw).hexdigest())
