# ML5 DevSecOps CI/CD Babysitter Loop — implementation roadmap

## Objective
Build the central org control plane: monitor → classify → authorize → remediate → verify → audit → improve. Initial baseline e3c3b305 contains README and MIT license only. This implements the operator's Tier 0–5 roadmap; production rollout and measured success are separate acceptance gates.

## Non-goals / safety
No host restart, runner scaling, WSL changes or interference with MIND. No secret values in source. Default observe-only. Merge/revert/dependency-update/quarantine executors remain denied until human policy approval, tested trust boundaries and rollback procedure exist. No claims of SOC 2 compliance or immutable administrator-proof local SQLite.

## Ownership and interfaces
Root owns src/store.py, src/remediation_engine.py, src/babysitter_agent.py, database/schema.sql, integration tests, workflows and docs. Classifier specialist owns src/failure_classifier.py, src/analytics.py, config/org-ruleset.yaml and corresponding tests. Adapter specialist owns src/github_client.py, src/webhook_server.py, src/monitor.py and corresponding tests. Review specialist is read-only.

Canonical normalized event: repository, run_id, attempt, sha, branch, workflow_id, status, conclusion, created_at, updated_at, evidence(list[str]), history(list[bool]). Classifier returns category/action/rule/reason; action is a proposal only. GitHub client request methods must reject cross-org and unsafe paths, use finite timeouts, paginate, never expose response-body secrets. Remediation client methods get_run(repository,run_id), retry_run(repository,run_id), create_issue(repository,title,body), notify(payload). External changes only behind root policy and durable action ledger.

## Work sequence
1. Verify repository/issues; create umbrella tracker without duplicates.
2. Implement authenticated durable webhook ingestion, polling/backfill, deterministic classification, append-only audit/export and analytics.
3. Implement opt-in bounded retry with durable incident budget, backoff, exact run/commit check and uncertain-outcome reconciliation. Deny high-trust operations by default.
4. Add workflow validation and scheduled observation, deployment guide, decision tree, credentials/role model, rollback and runbooks.
5. Run tests, security review, deliver PR; validate actual CI before merge.
6. Deploy only with explicit configuration/credentials, measure live latency/false positives/action efficacy before expanding scope. Production 30-second/95%/zero-miss targets are not established by unit tests.

## Remaining production acceptance
- Tier 0 artifacts and tests committed and reviewed.
- Tier 1 opted-in org monitoring, webhook endpoint installation, 30-day backfill and coverage reconciliation evidenced.
- Tier 2 classification evaluated with labeled data, minimum sample/window rules.
- Tier 3 safe retry/notification/issue integration proven; high-trust actions separately authorized, never silently enabled.
- Tier 4 production storage roles, retention anchors, audit export/replay reviewed by humans.
- Tier 5 trends/cost/recommendations/runbooks generated from measured events; policy changes require approval.

## Validation
Standard-library Python unittest suite; mocked HTTP/clock tests; replay/dedup/tamper/auth/concurrency failures; exact run-state verification; no live mutating tests. Workflow syntax validation and real hosted check readback after PR. Local test database only: no new platform service or corpus processing.

## Delivery and review receipts
PR #3, child #2, umbrella #1. Initial hosted validation passed and a read-only live probe persisted three observations with valid audit chain and no actions. Review follow-up fixes selected-policy classification, verified action report denominators, consistent payload limits and explicit operator abandonment of uncertain reservations without budget refunds or resubmission. Local suite: 65 tests passed. Production rollout and measured targets remain open; Bugbot was unable to run due spend limit, not a passed review.
