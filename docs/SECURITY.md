# Trust and credential boundaries

Use a GitHub App installation token for production. Observation needs repository metadata and Actions read; retries additionally need Actions write; issue notifications need Issues write. Neither retry nor monitoring needs Contents write or Administration. Fine-grained tokens should be restricted to opted-in repositories and these exact permissions. Do not reuse an operator admin token in the deployed service. Secrets are injected by the fleet credential broker or the deployment secret provider, never committed or printed.

Environment inputs: `GITHUB_TOKEN`, `GITHUB_WEBHOOK_SECRET`, optional `SLACK_WEBHOOK_URL`. The webhook secret is independent of the API token. The standalone service does not fetch secrets from raw vaults. Adapter requests have fixed origins, refuse redirects and suppress raw provider errors.

## Roles
Observer: read workflow metadata and export/report. Retry executor: explicit repository/workflow allowlist and `dry_run:false`, maximum three retry reservations per incident; failures/unknown submissions consume budget. Operator: reviewed configuration and deployment. No merge/revert/update role is implemented or granted. Database filesystem ACLs must isolate the service identity from untrusted agents. Separate OS/deployment identities enforce these roles; this CLI is not a multi-user authentication server.

## Webhook ingress
TLS termination and network access controls are required outside loopback. Verify HMAC SHA-256 over exact bounded bytes, require a unique delivery identifier and reject malformed identities/lifecycle. Commit the observation before HTTP acknowledgment. Signed byte replay with a different header is deduplicated using semantic identity/payload digest. Unsigned headers cannot authorize actions. GitHub `ping` is not ingested as a workflow event. Only `workflow_run` events are accepted; scheduled reconciliation covers delivery gaps.

Webhook bodies have a total read deadline. Standard-library HTTP header parsing and outbound provider calls have socket inactivity timeouts, not strict end-to-end deadlines; a slow stream can last longer. Require a trusted proxy with total header/request deadlines and a process supervisor with an execution budget before production exposure. Do not interpret a configured socket timeout as a latency guarantee.

## Fail-closed policy
Unknown evidence -> review, not healthy. Only failed completed runs may be proposed for retry. Before submission, refresh provider state and match run/commit/attempt/workflow. Persist reservation before remote execution; never retry an uncertain submission automatically. A subsequent matching run outcome is observed evidence, not absolute causality proof. Reserved records after process death require operator/provider reconciliation.

## Audit limitations
SQLite transactions and append-only triggers plus a hash chain provide local tamper evidence. A privileged administrator can replace the file or recompute the chain. Production immutability requires external append-only anchors/write-once retention, restricted writer roles, encrypted backups and restoration exercises. Exports are evidence for a compliance assessment; they do not certify SOC 2 compliance. Retention/export access must follow data policy; normalized metadata may still identify private repositories.

## Rollback / emergency stop
Set `dry_run:true` or revoke the executor token to prevent new retries. Stop the dedicated babysitter deployment, not the product fleet. Preserve the ledger and outstanding reservations. Roll back code by reviewed release revision; do not reset the ledger or retry budget. Reconcile uncertain actions before re-enabling. High-trust actions remain unimplemented until operator-approved design and tests are accepted.
