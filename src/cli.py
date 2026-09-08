"""Operator CLI. Observation is default; high-trust actions have no executable path."""
import argparse
import json
import os
from pathlib import Path

from .analytics import summarize, generate_runbook
from .babysitter_agent import Babysitter
from .github_client import GitHubClient
from .monitor import observe
from .remediation_engine import RemediationEngine
from .webhook_server import make_server


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', default='config/org-ruleset.yaml')
    p.add_argument('--database', default='state/babysitter.db')
    sub = p.add_subparsers(dest='command', required=True)
    poll = sub.add_parser('monitor'); poll.add_argument('--days', type=int, default=1)
    server = sub.add_parser('serve'); server.add_argument('--host', default='127.0.0.1'); server.add_argument('--port', type=int, default=8788)
    sub.add_parser('export')
    report = sub.add_parser('report'); report.add_argument('--runbook', action='store_true')
    retry = sub.add_parser('retry'); retry.add_argument('--event-key', required=True)
    reconcile = sub.add_parser('reconcile'); reconcile.add_argument('--action-key', required=True)
    abandon = sub.add_parser('abandon'); abandon.add_argument('--action-key', required=True); abandon.add_argument('--operator', required=True); abandon.add_argument('--reason', required=True); abandon.add_argument('--executor-quiesced', action='store_true')
    return p


def ledger_report(exported):
    records = [{'event': json.loads(row['event_json']), 'decision': json.loads(row['decision_json']), 'received_at': row['received_at']} for row in exported['events']]
    report = summarize(records)
    # Separate denominators: actions are not additional workflow observations.
    outcomes = [{'event': {}, 'outcome': {'verified': True, 'success': row['state'] == 'verified_success'}}
                if row['state'] in ('verified_success', 'verified_failure') else {'event': {}}
                for row in exported['actions']]
    action_metrics = summarize(outcomes)
    report['verified_remediation'] = action_metrics['verified_remediation']
    report['remediation_latency'] = action_metrics['remediation_latency']
    report['remediation_sample_basis'] = 'durable-action-records; subsequent-run-observed-not-exclusive-causality'
    abandoned = {row['subject'] for row in exported.get('audit', []) if row['kind'] == 'action-abandoned'}
    report['administratively_abandoned_actions'] = len(abandoned)
    report['pending_actions'] = sum(row['state'] in ('reserved', 'accepted', 'uncertain') and row.get('action_key') not in abandoned for row in exported['actions'])
    report['audit_chain_valid'] = exported['chain_valid']
    report['actions'] = {state: sum(row['state'] == state for row in exported['actions']) for state in ('reserved','accepted','uncertain','rejected','verified_success','verified_failure')}
    return report


def main(argv=None):
    args = parser().parse_args(argv)
    policy = json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    database = Path(args.database)
    database.parent.mkdir(parents=True, exist_ok=True)
    service = Babysitter(database, policy)
    try:
        if args.command == 'serve':
            secret = os.environ.get('GITHUB_WEBHOOK_SECRET', '').encode()
            server = make_server(service, secret, host=args.host, port=args.port)
            try: server.serve_forever()
            finally: server.server_close()
            return 0
        if args.command == 'export':
            result = service.store.export()
        elif args.command == 'abandon':
            result = RemediationEngine(service.store, None, policy).abandon(args.action_key, operator=args.operator, reason=args.reason, executor_quiesced=args.executor_quiesced)
        elif args.command == 'report':
            exported = service.store.export()
            report = ledger_report(exported)
            result = generate_runbook(report) if args.runbook else report
        else:
            client = GitHubClient(os.environ.get('GITHUB_TOKEN', ''))
            if args.command == 'monitor':
                repositories = policy.get('allowed_repositories', [])
                if not repositories: raise ValueError('No repositories opted in')
                result = observe(client, repositories, days=args.days, handler=service.observe)
                with service.store.transaction(): service.store.audit('reconciliation','poll',result)
                print(json.dumps(result))
                return 0 if result['coverage_complete'] else 2
            engine = RemediationEngine(service.store,client,policy)
            if args.command == 'retry':
                row = service.store.db.execute('SELECT * FROM events WHERE event_key=?',(args.event_key,)).fetchone()
                if not row: raise ValueError('Unknown event')
                result = engine.retry(json.loads(row['event_json']),json.loads(row['decision_json']))
            else: result = engine.reconcile(args.action_key)
        print(result if isinstance(result,str) else json.dumps(result,sort_keys=True))
        return 0
    except Exception:
        # Upstream exceptions may contain request credentials or raw evidence.
        print(json.dumps({'ok':False,'error':'Operation failed; verify policy, input, storage and upstream status'}))
        return 1
    finally:
        service.close()


if __name__ == '__main__': raise SystemExit(main())
