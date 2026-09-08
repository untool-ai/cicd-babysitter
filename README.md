# CI/CD Babysitter

Central observation and policy-gated remediation for `untool-ai` workflows. Roadmap: [issue #1](https://github.com/untool-ai/cicd-babysitter/issues/1).

## Architecture

Signed workflow webhook / bounded API polling → repository opt-in → normalized event → duplicate-safe ledger → versioned heuristic classification → proposal → approved retry policy → durable budget reservation → single submission → provider outcome reconciliation → audit/report/runbook.

The always-on webhook receiver commits before acknowledging. Scheduled Actions reconcile observations; they cannot guarantee 30-second latency. Hosted workflows never mutate monitored repositories. Local retry execution is explicit and defaults to disabled. MIND's host recovery is separate: this repository changes no Docker/WSL capacity or fleet services.

## Quick start

Python 3.11+; no third-party runtime dependencies.

```powershell
python -m unittest discover -s tests -v
python -m src.cli --database state/babysitter.db export
```

Populate `config/org-ruleset.yaml` repository allowlist before monitoring; retain `dry_run:true`. Inject `GITHUB_TOKEN` from an approved secret provider, then:

```powershell
python -m src.cli monitor --days 30
python -m src.cli report
python -m src.cli report --runbook
```

For real-time ingress, inject `GITHUB_WEBHOOK_SECRET` and run `python -m src.cli serve` behind an approved TLS proxy. Defaults to loopback. See [operations](docs/OPERATIONS.md).

## What is implemented

- GitHub repository/run pagination, bounded 30-day backfill, coverage/truncation reporting.
- Signed webhook receiver, normalized identities, restart-safe deduplication, rejection of altered-header replays.
- Deterministic security/config/systemic/flaky/transient classification with sample thresholds and unknown-state handling.
- Transactional append-only events/audit and hash-chain verification/export.
- Explicitly enabled retry executor: current-state recheck, durable max-three budget, exponential backoff, uncertain-submission handling, next-attempt outcome observation.
- Low-level Slack and investigation-issue adapters; no unattended notification loop enabled.
- Known/unknown metrics, explicit-price cost estimates, recommendation-only runbooks.
- GitHub-hosted tests and observation workflows with read-only permissions and pinned actions.

## What is not yet enabled or proven

No automatic merge/revert/dependency-update/quarantine executor. No production webhook installation or organization-wide token provisioning. No 30-second/95%/zero-security-miss measurement claim. SQLite triggers/hash chaining are local tamper evidence, not administrator-proof immutability or SOC 2 certification. Production role separation, external retention anchors and measured rollout acceptance remain open in the [roadmap](docs/ROADMAP.md).

The retry engine never accepts a caller-supplied decision as authority: it reads the recorded decision. Unknown submissions require reconciliation. Successful subsequent runs are observations, not proof the retry caused recovery.

## Guides
- [Decision tree](docs/REMEDIATION_DECISION_TREE.md)
- [Security and credential boundaries](docs/SECURITY.md)
- [Deployment, rollback, reports and backfill](docs/OPERATIONS.md)
- [Read-only dashboard queries](docs/dashboard-queries.sql)
- [Implementation plan and ownership](.agent/plans/implementation.md)

MIT licensed; existing license preserved.
