"""Bounded observation and reconciliation; never executes remediation."""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Callable
from .github_client import GitHubClient


def normalize_run(repository: str, run: dict[str, Any]) -> dict[str, Any]:
    """Normalize API evidence without pretending it is an authenticated webhook."""
    run_id = run.get("id")
    attempt = run.get("run_attempt", 1)
    if type(run_id) is not int or run_id <= 0 or type(attempt) is not int or attempt <= 0:
        raise ValueError("Invalid run identity")
    return {"repository": repository, "run_id": run_id, "attempt": attempt,
            "sha": run.get("head_sha"), "branch": run.get("head_branch"),
            "workflow_id": run.get("workflow_id"), "status": run.get("status"),
            "conclusion": run.get("conclusion"), "created_at": run.get("created_at"),
            "updated_at": run.get("updated_at"), "evidence": [], "history": []}


def observe(client: GitHubClient, repositories: list[str], *, days: int = 30,
            handler: Callable[[dict[str, Any]], Any] | None = None) -> dict[str, Any]:
    """Fetch only opted-in repositories. Handler must persist observations separately."""
    if len(repositories) > 100 or len(set(repositories)) != len(repositories):
        raise ValueError("At most 100 unique repositories allowed")
    report: dict[str, Any] = {"events": 0, "retrieved": 0, "persisted": 0, "truncated_repositories": [], "mode": "observation-only"}
    for repository in repositories:
        result = client.list_workflow_runs(repository, days=days)
        if result.truncated:
            report["truncated_repositories"].append(repository)
        for run in result.items:
            event = normalize_run(repository, run)
            if handler:
                handler(event)
                report["persisted"] += 1
            report["events"] += 1
            report["retrieved"] += 1
    report["coverage_complete"] = bool(repositories) and handler is not None and not report["truncated_repositories"]
    report["scope"] = "opted-in repositories and requested time window only"
    return report


def main() -> None:
    """Compatibility entrypoint delegates to the durable, policy-controlled CLI."""
    from .cli import main as durable_main
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/org-ruleset.yaml")
    parser.add_argument("--database", default="state/babysitter.db")
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()
    raise SystemExit(durable_main(["--config", args.config, "--database", args.database,
                                  "monitor", "--days", str(args.days)]))


if __name__ == "__main__":
    main()