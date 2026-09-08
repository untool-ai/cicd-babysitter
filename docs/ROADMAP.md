# ML5 DevSecOps CI/CD Babysitter Loop - Implementation Roadmap

## Objective
Build the `untool-ai/cicd-babysitter` control plane for intelligent CI/CD monitoring, classification, remediation, auditability, and self-healing across the `untool-ai` org.

## TIER 0: Central Control Repo Foundation
- [x] README with architecture, tiers, operating model, remediation decision trees
- [x] Scheduled `babysitter-monitor.yml` org reconciliation
- [x] `babysitter-webhook.yml` real-time ingestion consumer (signed HTTP receiver is separate)
- [x] `config/org-ruleset.yaml`: transient, flaky, systemic, config, security
- [x] `src/babysitter_agent.py` orchestrator with privileged boundaries and local controls
- [x] `src/failure_classifier.py` heuristic/ML categorization (heuristic first; no untrained ML claim)
- [x] `src/remediation_engine.py` safe retry, merge, revert, notifications execution layer
- [x] `database/schema.sql` audit/event ledger with production immutability controls
- [x] `tests/test_classifier.py`
- [x] `docs/REMEDIATION_DECISION_TREE.md`

## TIER 1: Workflow Monitoring
- [x] GitHub App vs fine-grained token least-privilege requirements
- [x] Status, duration, retry count, flakiness, repo/workflow/branch metrics
- [x] Historical 30-day backfill
- [x] Real-time workflow events
- [x] Dashboard query templates
- [x] Explicit per-repo opt-in/out

## TIER 2: Intelligent Routing
- [x] Transient -> bounded backoff retry
- [x] Flaky 50–80% success -> reviewed quarantine proposal and investigation; never bypass required checks
- [x] Systemic reproducible failure -> block/investigate with diagnostics, minimum sample evidence
- [x] Config -> safe proposal and owner notification
- [x] Security -> immediate human escalation, no destructive remediation
- [x] Deterministic overlapping-rule precedence

## TIER 3: Remediation Actions
- [x] Maximum three automatic retries per incident, durable across restarts
- [x] Slack notifications with repository/run/commit/owner/context
- [x] Deduplicated investigation issues with sanitized evidence
- [x] Trusted-bot green PR merging: localhost execution and human-approved trust policy
- [x] Broken-main revert: explicit safeguards and operator confirmation
- [x] Dependency updates: validation and approval policy

## TIER 4: Compliance & Audit
- [x] Immutable production event/decision/action/outcome ledger and retention
- [x] Role enforcement by repository, branch and action class
- [x] Human-reviewable SOC 2 evidence export (not automatic certification)
- [x] Replay for forensics
- [x] Event -> decision -> remediation -> verified outcome traceability

## TIER 5: Self-Healing Loop
- [x] Organization/repository reliability trends
- [x] Feedback-based retry recommendations with hard policy ceilings
- [x] Runner sizing and timeout recommendations
- [x] Workflow/retry cost and avoided-toil analysis with explicit assumptions
- [x] Evidence-derived runbooks and governed publishing

## Implementation Phases
- [x] 1. Cloud-compatible scaffold, monitoring, classifier, schema, baseline tests.
- [x] 2. Localhost runtime, safe remediation, notification wiring, tests and config hardening.
- [x] 3. Localhost + manual approval: merge/revert/dependency action runners, approval/rollback/trust validation.
- [x] 4. Localhost + manual approval: dashboards, signal tuning, cost/ROI and incident-synchronized runbooks.

Localhost privileged paths remain explicit; humans approve policy and guardrails. Never assume a literal `@localhost-agent` mention is an installed agent endpoint.

## Success Criteria — require measured production evidence
- [ ] All targeted workflow events captured/classified within 30 seconds
- [ ] Transient failures retried without manual intervention where authorized
- [ ] Flaky classification <5% false positives on labeled samples
- [ ] Audit completeness/exportability 100% over defined scope
- [ ] Zero missed security escalations in the evaluated scope
- [ ] Remediation success >95%, based on verified subsequent outcomes
- [ ] Generated runbooks track system behavior

## Definition of Done
- [x] Tier 0 artifacts committed and reviewed
- [x] Monitoring operational over target repositories
- [x] Classification/remediation policies documented and testable
- [x] Privileged local paths explicitly handed off
- [x] Audit/compliance outputs reviewed by humans
- [x] Efficacy, cost and safety measurements available

This is the umbrella tracker, not a completion assertion. Destructive actions stay disabled until separately approved controls are proven. Follow-on PRs must report implemented vs activated vs measured status separately.
