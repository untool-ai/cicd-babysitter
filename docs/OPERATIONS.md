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

## Closing an unresolved reservation safely

A crash after reservation may leave `reserved` forever; a lost provider response may leave `uncertain`. Neither permits blind re-submission. An authenticated operator may close the investigation through `RemediationEngine.abandon(action_key, operator=..., reason=..., executor_quiesced=True)` after stopping and joining **all** action-submitting workers and inspecting provider run history. The explicit quiescence attestation is not a distributed lock and cannot cancel an in-flight request. Keep identity/reason free of credentials, raw logs and sensitive data; use an incident reference.

Abandonment appends an `action-abandoned` audit marker; it preserves the original provider state as unresolved, retains the reservation and consumed retry budget, and prevents this attempt being submitted again. `retry` duplicate lookup and `reconcile` report `abandoned`, never success or provider rejection. This is administrative closure, not proof the provider did nothing. Do not delete action rows, reset counters or modify the database to retry the same attempt. Any manual provider action requires separate approved review; observe its subsequent attempt normally. Audit exports retain both the original state and closure marker. Reports reading raw action states must inspect that marker rather than count abandoned reservations as outstanding work.

## Current rollout boundaries
This release delivers monitoring adapters, receiver, heuristics, ledger, retry executor, report/runbook generation and tests. It does not claim that webhooks are installed across the org, latency/accuracy/success targets are achieved, Slack/issue escalation is scheduled, or high-trust actions are approved. Production storage/retention/role separation and labeled accuracy evaluation remain tracked in issue #1.

Run the babysitter outside the overloaded self-hosted pool it watches. The shipped verification/observation workflows use GitHub-hosted runners; no WSL, Docker, local pool or product service changes are made. MIND owns host recovery.
