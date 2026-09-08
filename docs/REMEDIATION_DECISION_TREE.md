# Remediation decision tree

```mermaid
flowchart TD
  A[Signed webhook or authenticated polling] --> B{Schema and repository opt-in valid?}
  B -- no --> C[Reject]
  B -- yes --> D[Persist deduplicated observation]
  D --> E{Security evidence?}
  E -- yes --> F[Escalation proposal; no retry]
  E -- no --> G[Config then systemic then flaky then transient]
  G --> H{Retry proposed?}
  H -- no --> I[Investigate or no action]
  H -- yes --> J{Explicit executor policy enabled?}
  J -- no --> K[Dry run]
  J -- yes --> L[Recheck provider state and exact identity]
  L --> M[Atomically reserve budget; max three]
  M --> N[Submit once]
  N --> O{Outcome known?}
  O -- no --> P[Uncertain; reconcile, do not resubmit]
  O -- yes --> Q[Accepted is not successful]
  Q --> R[Observe next run and record outcome]
```

Quarantine means a proposed reviewed change, never automatic removal of required checks. Merge/revert/dependency updates have no executable path in this release. Statistical classification thresholds require enough historical samples; a single failure cannot establish systemic or flaky behavior.
