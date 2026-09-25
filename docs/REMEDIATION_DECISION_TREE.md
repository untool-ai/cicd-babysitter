# Remediation decision tree

```mermaid
flowchart TD
  A[Signed webhook or authenticated polling] --> B{Schema and repository opt-in valid?}
  B -- no --> C[Reject]
  B -- yes --> D[Persist deduplicated observation]
  D --> E{Security evidence?}
  E -- yes --> F[Escalation proposal; issue optional; no retry/merge]
  E -- no --> G[Config then systemic then flaky then transient]
  G --> H{Retry proposed?}
  H -- no --> I[Investigate / create_issue under policy]
  H -- yes --> J{Explicit executor policy enabled?}
  J -- no --> K[Dry run]
  J -- yes --> L[Recheck provider state and exact identity]
  L --> M[Atomically reserve budget; max three]
  M --> N[Submit once]
  N --> O{Outcome known?}
  O -- no --> P[Uncertain; reconcile, do not resubmit]
  O -- yes --> Q[Accepted is not successful]
  Q --> R[Observe next run and record outcome]

  S[PR readiness scan] --> T{Ready + recorded merge proposal?}
  T -- no --> U[Wait / investigate]
  T -- yes --> V{enable_merge and dry_run false?}
  V -- no --> W[Dry run / denied]
  V -- yes --> X[Idempotent key check]
  X --> Y[Live recheck checks/approvals/sha]
  Y --> Z{Trusted author or human approval?}
  Z -- no --> AA[Denied]
  Z -- yes --> AB[Reserve then merge once]
  AB --> AC[Reconcile provider merged state]
```

Quarantine means a proposed reviewed change, never automatic removal of required checks. Revert, dependency-update, and open_pr action types remain unimplemented and fail closed. Merge and create_issue are implemented behind explicit policy flags (`enable_merge`, `enable_issues`), repository allowlists, dry-run default, durable reservation, live recheck, and uncertain-submission reconciliation. Statistical classification thresholds require enough historical samples; a single failure cannot establish systemic or flaky behavior.
