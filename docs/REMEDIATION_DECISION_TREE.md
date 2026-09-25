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
  H -- no --> I{Config, systemic or unknown category?}
  I -- no --> I2[Investigate or no action]
  I -- yes --> S{Cloud dispatch enabled, repo opted in, PR present?}
  S -- no --> I2
  S -- yes --> T{Monthly agent budget remaining?}
  T -- no --> I2
  T -- yes --> U[Reserve dispatch budget; route by PR size tier]
  U --> V[dry_run true: record only]
  U --> W[dry_run false: label/comment to hand off to Copilot, Jules or Codex]
  W --> X[Cloud agent opens/updates a PR; required checks and branch protection still gate merge]
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

Cloud-agent dispatch (`config`/`systemic`/`unknown` categories only; `security` and `healthy` are never dispatched, and `transient`/`flaky` stay on the existing retry path) hands a classified failure to an already-trusted GitHub cloud coding agent by labeling or commenting on the associated pull request. It never merges, reverts or edits code itself — the cloud agent produces its own commits, and the existing required-status-check and branch-protection path governs merge. Dispatch is disabled and `dry_run:true` by default; a repository must be explicitly opted in.
