"""Read-only org-wide CI health scan: full pagination, zero mutation, ever.

Enumerates every active (non-archived) repository in the organization, then
paginates workflow runs and open pull requests per repository. Receipts
prioritize a current failure on the default branch or on an open PR's head
commit above a historical failure that a later run on the same
branch/workflow has already superseded. Nothing here calls a mutating
GitHub endpoint (no rerun, no merge, no issue creation); it only reads and
reports.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .github_client import GitHubClient, GitHubError

_FAILURE_CONCLUSIONS = frozenset({"failure", "timed_out", "startup_failure"})
_PRIORITY_ORDER = {
    "default_branch_regression": 0,
    "pull_request_regression": 1,
    "branch_failure": 2,
    "superseded_historical_failure": 3,
}
_MAX_REPOSITORIES = 200


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed
        except ValueError:
            pass
    return datetime.min.replace(tzinfo=timezone.utc)


def discover_active_repositories(client: GitHubClient) -> dict[str, Any]:
    """Enumerate every non-archived org repository across all pages."""
    result = client.list_repositories(include_archived=False)
    repositories = [
        {"repository": repo["full_name"], "default_branch": repo["default_branch"]}
        for repo in result.items
        if isinstance(repo.get("full_name"), str) and isinstance(repo.get("default_branch"), str)
    ]
    return {"repositories": repositories, "truncated": result.truncated}


def _group_runs(runs: list[dict[str, Any]]) -> dict[tuple, list[dict[str, Any]]]:
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for run in runs:
        groups.setdefault((run.get("workflow_id"), run.get("head_branch")), []).append(run)
    for group in groups.values():
        group.sort(key=lambda run: _parse_timestamp(run.get("created_at")))
    return groups


def _receipt(repository: str, run: dict[str, Any], *, priority: str, superseded: bool, required_check: bool) -> dict[str, Any]:
    return {
        "repository": repository, "priority": priority, "superseded": superseded,
        "required_check": required_check, "workflow_id": run.get("workflow_id"),
        "branch": run.get("head_branch"), "run_id": run.get("id"), "attempt": run.get("run_attempt"),
        "sha": run.get("head_sha"), "conclusion": run.get("conclusion"),
        "created_at": run.get("created_at"), "updated_at": run.get("updated_at"),
    }


def scan_repository(client: GitHubClient, repository: str, default_branch: str, *, days: int = 30) -> dict[str, Any]:
    """Read-only per-repository regression scan; never mutates anything."""
    runs_result = client.list_workflow_runs(repository, days=days)
    runs = [run for run in runs_result.items if isinstance(run, dict)]

    pr_result = client.list_pull_requests(repository, state="open")
    open_prs = [pr for pr in pr_result.items if isinstance(pr, dict)]
    pr_head_shas = {
        pr["head"]["sha"] for pr in open_prs
        if isinstance(pr.get("head"), dict) and isinstance(pr["head"].get("sha"), str)
    }

    try:
        required_contexts = {name.casefold() for name in client.get_required_status_contexts(repository, default_branch)}
    except GitHubError:
        # Branch protection is unreadable (permissions/outage); treat as unknown, never as "required".
        required_contexts = set()

    receipts = []
    for (_workflow_id, branch), group in _group_runs(runs).items():
        latest = group[-1]
        for run in group:
            if run.get("conclusion") not in _FAILURE_CONCLUSIONS:
                continue
            name = run.get("name")
            required = bool(required_contexts) and isinstance(name, str) and name.casefold() in required_contexts
            if run is not latest:
                receipts.append(_receipt(repository, run, priority="superseded_historical_failure",
                                          superseded=True, required_check=False))
            elif branch == default_branch:
                receipts.append(_receipt(repository, run, priority="default_branch_regression",
                                          superseded=False, required_check=required))
            elif run.get("head_sha") in pr_head_shas:
                receipts.append(_receipt(repository, run, priority="pull_request_regression",
                                          superseded=False, required_check=required))
            else:
                receipts.append(_receipt(repository, run, priority="branch_failure",
                                          superseded=False, required_check=required))

    return {
        "repository": repository, "default_branch": default_branch,
        "runs_retrieved": len(runs), "runs_truncated": runs_result.truncated,
        "open_pull_requests": len(open_prs), "pull_requests_truncated": pr_result.truncated,
        "required_status_contexts": sorted(required_contexts), "receipts": receipts,
    }


def scan_organization(client: GitHubClient, *, days: int = 30) -> dict[str, Any]:
    """Enumerate the full org, paginate every repository/run/PR, emit read-only receipts.

    Coverage is honest: any repository-list or per-repository pagination
    truncation flips coverage_complete to False rather than silently
    reporting a partial scan as complete.
    """
    discovery = discover_active_repositories(client)
    repositories = discovery["repositories"]
    if len(repositories) > _MAX_REPOSITORIES:
        raise ValueError("At most 200 active repositories supported per scan")

    truncated_repositories: list[str] = []
    receipts: list[dict[str, Any]] = []
    total_runs = total_prs = 0
    for entry in repositories:
        repository, default_branch = entry["repository"], entry["default_branch"]
        result = scan_repository(client, repository, default_branch, days=days)
        total_runs += result["runs_retrieved"]
        total_prs += result["open_pull_requests"]
        if result["runs_truncated"] or result["pull_requests_truncated"]:
            truncated_repositories.append(repository)
        receipts.extend(result["receipts"])

    receipts.sort(key=lambda receipt: (_PRIORITY_ORDER.get(receipt["priority"], 99), receipt["repository"]))
    coverage_complete = bool(repositories) and not discovery["truncated"] and not truncated_repositories
    return {
        "mode": "read-only-org-scan",
        "scope": "active non-archived repositories only; no mutation performed",
        "repositories_discovered": len(repositories),
        "repository_discovery_truncated": discovery["truncated"],
        "workflow_runs_retrieved": total_runs,
        "open_pull_requests_retrieved": total_prs,
        "truncated_repositories": truncated_repositories,
        "coverage_complete": coverage_complete,
        "receipts": receipts,
    }
