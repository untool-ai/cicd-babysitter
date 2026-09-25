"""Read-only pull request readiness evaluation. Never mutates GitHub state.

Produces a structured readiness receipt used by observe_pr and the merge
executor. Merge eligibility is a proposal until RemediationEngine.merge()
re-checks live state under explicit policy.
"""
from __future__ import annotations

from typing import Any

from .github_client import GitHubClient, GitHubError

_MERGEABLE_STATES = frozenset({"clean", "has_hooks", "unstable", "blocked", "behind", "dirty", "draft", "unknown"})
_SUCCESS = frozenset({"success", "neutral", "skipped"})
_PENDING = frozenset({"pending", "queued", "in_progress", "expected", "waiting", "requested"})
_FAILURE = frozenset({"failure", "error", "timed_out", "cancelled", "startup_failure", "action_required", "stale"})


def _author_login(pull: dict[str, Any]) -> str | None:
    user = pull.get("user")
    if isinstance(user, dict) and isinstance(user.get("login"), str):
        return user["login"]
    return None


def _head_sha(pull: dict[str, Any]) -> str | None:
    head = pull.get("head")
    if isinstance(head, dict) and isinstance(head.get("sha"), str):
        return head["sha"]
    return None


def _base_ref(pull: dict[str, Any]) -> str | None:
    base = pull.get("base")
    if isinstance(base, dict) and isinstance(base.get("ref"), str):
        return base["ref"]
    return None


def summarize_checks(statuses: list[dict[str, Any]], check_runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Collapse commit statuses and check-runs into a single readiness signal."""
    names: dict[str, str] = {}
    for status in statuses:
        if not isinstance(status, dict):
            continue
        context, state = status.get("context"), status.get("state")
        if isinstance(context, str) and isinstance(state, str):
            names[context] = state.casefold()
    for run in check_runs:
        if not isinstance(run, dict):
            continue
        name = run.get("name")
        if not isinstance(name, str):
            continue
        conclusion = run.get("conclusion")
        status = run.get("status")
        if isinstance(conclusion, str) and conclusion:
            names[name] = conclusion.casefold()
        elif isinstance(status, str):
            names[name] = status.casefold()
    pending = sorted(name for name, state in names.items() if state in _PENDING)
    failed = sorted(name for name, state in names.items() if state in _FAILURE)
    successful = sorted(name for name, state in names.items() if state in _SUCCESS)
    unknown = sorted(name for name, state in names.items()
                     if state not in _PENDING | _FAILURE | _SUCCESS)
    if failed:
        overall = "failure"
    elif pending or unknown:
        overall = "pending"
    elif names:
        overall = "success"
    else:
        overall = "empty"
    return {
        "overall": overall,
        "checks": names,
        "successful": successful,
        "pending": pending,
        "failed": failed,
        "unknown": unknown,
    }


def evaluate_pull(client: GitHubClient, repository: str, number: int,
                  *, required_contexts: list[str] | None = None) -> dict[str, Any]:
    """Fetch live PR + check state and return a merge-readiness receipt."""
    if type(number) is not int or number <= 0:
        raise ValueError("Positive pull request number required")
    pull = client.get_pull(repository, number)
    sha = _head_sha(pull)
    if not sha:
        raise GitHubError()
    try:
        status_payload = client.get_combined_status(repository, sha)
    except GitHubError:
        status_payload = {"statuses": [], "state": "pending"}
    try:
        check_payload = client.list_check_runs(repository, sha)
    except GitHubError:
        check_payload = {"check_runs": []}
    statuses = status_payload.get("statuses") if isinstance(status_payload, dict) else []
    check_runs = check_payload.get("check_runs") if isinstance(check_payload, dict) else []
    if not isinstance(statuses, list):
        statuses = []
    if not isinstance(check_runs, list):
        check_runs = []
    check_summary = summarize_checks(statuses, check_runs)

    base = _base_ref(pull) or ""
    if required_contexts is None and base:
        try:
            required_contexts = client.get_required_status_contexts(repository, base)
        except GitHubError:
            required_contexts = []
    required = list(required_contexts or [])
    missing_required = sorted(
        context for context in required
        if context not in check_summary["checks"]
        or check_summary["checks"][context] not in _SUCCESS
    )

    draft = bool(pull.get("draft"))
    merged = bool(pull.get("merged"))
    state = pull.get("state") if isinstance(pull.get("state"), str) else "unknown"
    mergeable = pull.get("mergeable")
    mergeable_state = pull.get("mergeable_state") if isinstance(pull.get("mergeable_state"), str) else "unknown"
    author = _author_login(pull)
    approvals = client.get_pull_approval_count(repository, number)

    blockers: list[str] = []
    if merged:
        blockers.append("already-merged")
    if state != "open":
        blockers.append("not-open")
    if draft:
        blockers.append("draft")
    if mergeable is False:
        blockers.append("merge-conflicts")
    if mergeable_state in ("dirty", "draft"):
        blockers.append(f"mergeable_state:{mergeable_state}")
    if check_summary["overall"] == "failure":
        blockers.append("checks-failed")
    elif check_summary["overall"] in ("pending", "empty"):
        blockers.append("checks-incomplete")
    if missing_required:
        blockers.append("required-checks-missing")
    if mergeable is None and not merged:
        blockers.append("mergeable-unknown")

    ready = not blockers
    return {
        "kind": "pull_request",
        "repository": repository,
        "number": number,
        "title": pull.get("title") if isinstance(pull.get("title"), str) else "",
        "state": state,
        "draft": draft,
        "merged": merged,
        "mergeable": mergeable,
        "mergeable_state": mergeable_state,
        "head_sha": sha,
        "base_ref": base,
        "author": author,
        "html_url": pull.get("html_url") if isinstance(pull.get("html_url"), str) else None,
        "approvals": approvals,
        "required_contexts": required,
        "missing_required_contexts": missing_required,
        "checks": check_summary,
        "blockers": blockers,
        "ready": ready,
        "proposed_action": "merge" if ready else "wait",
    }


def scan_repository_pulls(client: GitHubClient, repository: str) -> dict[str, Any]:
    """Read-only readiness scan of every open PR in one repository."""
    result = client.list_pull_requests(repository, state="open")
    receipts = []
    for pull in result.items:
        if not isinstance(pull, dict) or type(pull.get("number")) is not int:
            continue
        try:
            receipts.append(evaluate_pull(client, repository, pull["number"]))
        except (GitHubError, ValueError):
            receipts.append({
                "kind": "pull_request",
                "repository": repository,
                "number": pull.get("number"),
                "ready": False,
                "blockers": ["evaluation-failed"],
                "proposed_action": "investigate",
            })
    ready = [row for row in receipts if row.get("ready")]
    return {
        "mode": "read-only-pr-scan",
        "repository": repository,
        "open_pull_requests": len(result.items),
        "truncated": result.truncated,
        "ready_count": len(ready),
        "receipts": receipts,
    }


def scan_organization_pulls(client: GitHubClient, repositories: list[str] | None = None) -> dict[str, Any]:
    """Scan open PRs across opted-in repos, or every active org repo when unrestricted."""
    truncated: list[str] = []
    receipts: list[dict[str, Any]] = []
    if repositories is None:
        discovery = client.list_repositories(include_archived=False)
        repos = [
            repo["full_name"] for repo in discovery.items
            if isinstance(repo.get("full_name"), str)
        ]
        if discovery.truncated:
            truncated.append("*org-repo-list*")
    else:
        repos = list(repositories)
    if len(repos) > 200:
        raise ValueError("At most 200 repositories supported per PR scan")
    for repository in repos:
        result = scan_repository_pulls(client, repository)
        if result["truncated"]:
            truncated.append(repository)
        receipts.extend(result["receipts"])
    receipts.sort(key=lambda row: (0 if row.get("ready") else 1, row.get("repository") or "", row.get("number") or 0))
    return {
        "mode": "read-only-org-pr-scan",
        "repositories_scanned": len(repos),
        "truncated_repositories": truncated,
        "coverage_complete": bool(repos) and not truncated,
        "ready_count": sum(1 for row in receipts if row.get("ready")),
        "receipts": receipts,
    }
