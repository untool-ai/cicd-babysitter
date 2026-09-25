# Deployment and rollout

## Local validation
`python -m unittest discover -s tests -v`

## Dedicated observation service
Use Python 3.11 or later. No third-party Python packages are needed. Copy the versioned config and explicitly populate `allowed_repositories`; keep `dry_run:true`. Store the database on persistent local storage with service-only permissions. Inject the webhook secret from the approved secret provider. Start `python -m src.cli --database state/babysitter.db serve` under the chosen service supervisor. Default is loopback port 8788. A TLS reverse proxy is required for GitHub delivery; do not expose the plain HTTP listener publicly.

Register the GitHub App/webhook for `workflow_run` events at `/webhook`. A GitHub Actions workflow is not a webhook receiver. `babysitter-webhook.yml` processes already-authenticated dispatches by re-fetching provider metadata rather than trusting dispatch content. Queued Actions cannot promise a 30-second response; the always-on receiver handles that target.

## Reconciliation / backfill
Inject a read-only installation token, then `python -m src.cli --database state/babysitter.db monitor --days 30`. Up to 100 repositories per configured cohort; API pages are bounded, and truncation returns a failing coverage result rather than a complete claim. Split cohorts/time ranges or increase reviewed page limits when coverage is partial. Polling collects metadata, not full logs; absent diagnosis stays unknown. Raw workflow text is not automatically trusted as permission to remediate.

## Reports and replay
`python -m src.cli --database state/babysitter.db export`

`python -m src.cli --database state/babysitter.db report`

`python -m src.cli --database state/babysitter.db report --runbook`

Audit exports include event, decision, action and outcome state plus chain verification. Replay classifications over sanitized historical inputs into a separate database; do not replay external actions. Compare versioned ruleset decisions before promoting config changes. Keep measured unknown/missing fields in denominator reports. Cost estimates require explicit pricing and known duration; no invented saved dollars or human-toil savings.

## Enabling retries later

Review exact repository/workflow IDs and set dry_run false only after a dedicated write-scoped token is configured. Submit a persisted event with `python -m src.cli retry --event-key <key>` and reconcile with `python -m src.cli reconcile --action-key <key>`. There is deliberately no unattended mutation loop enabled by deployment. A future dispatcher must use this same durable reservation boundary rather than bypass it. Retry counters survive restarts and uncertain actions consume their budget. Raising configured max_retries above three is rejected.

## Enabling dispatch later

Cloud-agent dispatch is disabled and `dry_run:true` by default (`config/org-ruleset.yaml`'s `dispatch` object). To opt a repository in: populate `dispatch.allowed_repositories`, set `dispatch.enabled:true`, review `dispatch.routing`/`dispatch.agent_labels`/`dispatch.agent_review_mentions` against the actual GitHub labels and mention conventions configured for that repository (see `docs/agent-routing.md`-style conventions in the target org's own docs; this repo does not assume any particular label exists), and only then set `dispatch.dry_run:false`. Submit a persisted event with `python -m src.cli dispatch --event-key <key>`; it re-derives the immutably recorded classification and refuses anything other than `config`/`systemic`/`unknown`.

`dispatch.monthly_budget_usd` and `dispatch.estimated_cost_usd` are **operator-supplied estimates**, not a call to any billing API — this CLI never queries GitHub Copilot/Jules/Codex pricing. The cap is a **denial mode**: once the configured month's reserved/accepted/uncertain dispatches would exceed the cap, further dispatches for that month are refused; it does not stop, cancel or refund any external agent invocation already submitted, and it is not a hard spending guarantee against actual provider billing. An agent with no configured `estimated_cost_usd` entry is refused rather than treated as free. `python -m src.cli report` surfaces `dispatches`, `dispatches_by_agent` and `dispatch_budget_spent_usd_by_period` for review before raising the cap or adding a new agent.

Dispatch mutations are label/comment only (`issues:write` scope) — there is no executable path in this release for the cloud agent's resulting pull request to be merged, reverted or otherwise mutated by the babysitter itself; existing required-status-checks and branch-protection review that PR exactly as any other.

## Closing an unresolved reservation safely

A crash after reservation may leave `reserved` forever; a lost provider response may leave `uncertain`. Neither permits blind re-submission. An authenticated operator may close the investigation through `RemediationEngine.abandon(action_key, operator=..., reason=..., executor_quiesced=True)` after stopping and joining **all** action-submitting workers and inspecting provider run history. The explicit quiescence attestation is not a distributed lock and cannot cancel an in-flight request. Keep identity/reason free of credentials, raw logs and sensitive data; use an incident reference.

Abandonment appends an `action-abandoned` audit marker; it preserves the original provider state as unresolved, retains the reservation and consumed retry budget, and prevents this attempt being submitted again. `retry` duplicate lookup and `reconcile` report `abandoned`, never success or provider rejection. This is administrative closure, not proof the provider did nothing. Do not delete action rows, reset counters or modify the database to retry the same attempt. Any manual provider action requires separate approved review; observe its subsequent attempt normally. Audit exports retain both the original state and closure marker. Reports reading raw action states must inspect that marker rather than count abandoned reservations as outstanding work.

## Current rollout boundaries
This release delivers monitoring adapters, receiver, heuristics, ledger, retry executor, report/runbook generation and tests. It does not claim that webhooks are installed across the org, latency/accuracy/success targets are achieved, Slack/issue escalation is scheduled, or high-trust actions are approved. Production storage/retention/role separation and labeled accuracy evaluation remain tracked in issue #1.

Run the babysitter outside the overloaded self-hosted pool it watches. The shipped verification/observation workflows use GitHub-hosted runners; no WSL, Docker, local pool or product service changes are made. MIND owns host recovery.
