# CI/CD Babysitter

Central observation and policy-gated remediation for `untool-ai` workflows. Roadmap: [issue #1](https://github.com/untool-ai/cicd-babysitter/issues/1).

## Architecture

Signed workflow webhook / bounded API polling → repository opt-in → normalized event → duplicate-safe ledger → versioned heuristic classification → proposal → approved retry policy → durable budget reservation → single submission → provider outcome reconciliation → audit/report/runbook.

The always-on webhook receiver commits before acknowledging. Scheduled Actions reconcile observations; they cannot guarantee 30-second latency. Hosted observation workflows never mutate monitored repositories. Local retry execution is explicit and defaults to disabled. Cloud-agent dispatch (label/comment only, never merge/revert/edit) is likewise explicit, category-restricted and defaults to disabled; when an operator opts a repository in, that dispatch loop runs entirely on GitHub-hosted infrastructure — no home/office machine or GCP node needs to be online for it to complete. MIND's host recovery is separate: this repository changes no Docker/WSL capacity or fleet services.

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
- Explicitly enabled cloud-agent dispatch: hands `config`/`systemic`/`unknown` failures on an opted-in repository's pull request to GitHub Copilot coding agent, Jules or Codex via a label/`@`-mention (never merge/revert/edit); GitHub-hosted end to end, no local/localhost machine required. Category-restricted (never `security`/`healthy`), size-tiered routing from an optional changed-file count, durable crash-safe reservation, and a monthly USD budget cap that fails closed on unknown per-agent cost.
- Low-level Slack and investigation-issue adapters; no unattended notification loop enabled.
- Known/unknown metrics, explicit-price cost estimates, recommendation-only runbooks.
- GitHub-hosted tests and observation workflows with read-only permissions and pinned actions.
- Category-restricted (config/systemic/unknown only, never security/healthy), budget-capped cloud-agent dispatch: labels or comments a PR to hand a classified failure to an already-trusted GitHub cloud coding agent (Copilot coding agent, Jules, Codex), gated by `dispatch.enabled`, per-repository opt-in and `dispatch.dry_run`, with a durable reserve-before-submit budget reservation (`dispatches` table) and fail-closed denial on unknown per-agent estimated cost. Runs entirely on GitHub-hosted infrastructure; no home/office machine or GCP node is required for this loop.

## What is not yet enabled or proven

No automatic merge/revert/dependency-update/quarantine executor. No production webhook installation or organization-wide token provisioning. No 30-second/95%/zero-security-miss measurement claim. SQLite triggers/hash chaining are local tamper evidence, not administrator-proof immutability or SOC 2 certification. Production role separation, external retention anchors and measured rollout acceptance remain open in the [roadmap](docs/ROADMAP.md). Cloud-agent dispatch is disabled and `dry_run:true` by default; no repository is scheduled for automatic dispatch in production, and `estimated_cost_usd`/`monthly_budget_usd` are operator-supplied figures, not measured provider billing.

No cloud-agent dispatch is scheduled or active in production yet: `dispatch.enabled` is `false` and `dispatch.dry_run` is `true` by default, with an empty `dispatch.allowed_repositories` allowlist, so nothing labels or comments on a PR until an operator explicitly opts in. `estimated_cost_usd` and `monthly_budget_usd` are operator-supplied configuration, not measured provider billing; no cost/ROI figure for dispatch is a verified number. Cloud-agent completion of a dispatched PR (i.e. the agent producing a green, mergeable update) is not measured or guaranteed by this repository — dispatch only labels/comments; the required-check and merge path is unchanged and outside this repository's control.

The retry engine never accepts a caller-supplied decision as authority: it reads the recorded decision. Unknown submissions require reconciliation. Successful subsequent runs are observations, not proof the retry caused recovery.

## Guides
- [Decision tree](docs/REMEDIATION_DECISION_TREE.md)
- [Security and credential boundaries](docs/SECURITY.md)
- [Deployment, rollback, reports and backfill](docs/OPERATIONS.md)
- [Read-only dashboard queries](docs/dashboard-queries.sql)
- [Implementation plan and ownership](.agent/plans/implementation.md)

MIT licensed; existing license preserved.
