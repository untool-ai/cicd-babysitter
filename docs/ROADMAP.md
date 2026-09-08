# ML5 DevSecOps CI/CD Babysitter Loop - Implementation Roadmap

## Objective
Build the `untool-ai/cicd-babysitter` control plane for intelligent CI/CD monitoring, classification, remediation, auditability, and self-healing across the `untool-ai` org.

## TIER 0: Central Control Repo Foundation
- [ ] README with architecture, tiers, operating model, remediation decision trees
- [ ] Scheduled `babysitter-monitor.yml` org reconciliation
- [ ] `babysitter-webhook.yml` real-time ingestion consumer (signed HTTP receiver is separate)
- [ ] `config/org-ruleset.yaml`: transient, flaky, systemic, config, security
- [ ] `src/babysitter_agent.py` orchestrator with privileged boundaries and local controls
- [ ] `src/failure_classifier.py` heuristic/ML categorization (heuristic first; no untrained ML claim)
- [ ] `src/remediation_engine.py` safe retry, merge, revert, notifications execution layer
- [ ] `database/schema.sql` audit/event ledger with production immutability controls
- [ ] `tests/test_classifier.py`
- [ ] `docs/REMEDIATION_DECISION_TREE.md`

## TIER 1: Workflow Monitoring
- [ ] GitHub App vs fine-grained token least-privilege requirements
- [ ] Status, duration, retry count, flakiness, repo/workflow/branch metrics
- [ ] Historical 30-day backfill
- [ ] Real-time workflow events
- [ ] Dashboard query templates
- [ ] Explicit per-repo opt-in/out

## TIER 2: Intelligent Routing
- [ ] Transient -> bounded backoff retry
- [ ] Flaky 50–80% success -> reviewed quarantine proposal and investigation; never bypass required checks
- [ ] Systemic reproducible failure -> block/investigate with diagnostics, minimum sample evidence
- [ ] Config -> safe proposal and owner notification
- [ ] Security -> immediate human escalation, no destructive remediation
- [ ] Deterministic overlapping-rule precedence

## TIER 3: Remediation Actions
- [ ] Maximum three automatic retries per incident, durable across restarts
- [ ] Slack notifications with repository/run/commit/owner/context
- [ ] Deduplicated investigation issues with sanitized evidence
- [ ] Trusted-bot green PR merging: localhost execution and human-approved trust policy
- [ ] Broken-main revert: explicit safeguards and operator confirmation
- [ ] Dependency updates: validation and approval policy

## TIER 4: Compliance & Audit
- [ ] Immutable production event/decision/action/outcome ledger and retention
- [ ] Role enforcement by repository, branch and action class
- [ ] Human-reviewable SOC 2 evidence export (not automatic certification)
- [ ] Replay for forensics
- [ ] Event -> decision -> remediation -> verified outcome traceability

## TIER 5: Self-Healing Loop
- [ ] Organization/repository reliability trends
- [ ] Feedback-based retry recommendations with hard policy ceilings
- [ ] Runner sizing and timeout recommendations
- [ ] Workflow/retry cost and avoided-toil analysis with explicit assumptions
- [ ] Evidence-derived runbooks and governed publishing

## Implementation Phases
1. Cloud-compatible scaffold, monitoring, classifier, schema, baseline tests.
2. Localhost runtime, safe remediation, notification wiring, tests and config hardening.
3. Localhost + manual approval: merge/revert/dependency action runners, approval/rollback/trust validation.
4. Localhost + manual approval: dashboards, signal tuning, cost/ROI and incident-synchronized runbooks.

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
- [ ] Tier 0 artifacts committed and reviewed
- [ ] Monitoring operational over target repositories
- [ ] Classification/remediation policies documented and testable
- [ ] Privileged local paths explicitly handed off
- [ ] Audit/compliance outputs reviewed by humans
- [ ] Efficacy, cost and safety measurements available

This is the umbrella tracker, not a completion assertion. Destructive actions stay disabled until separately approved controls are proven. Follow-on PRs must report implemented vs activated vs measured status separately.
