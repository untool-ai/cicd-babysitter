"""Bounded GitHub adapter; credentials never enter diagnostics or URLs."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, build_opener, HTTPRedirectHandler


class GitHubError(RuntimeError):
    """Sanitized remote failure. Uncertain mutations must be reconciled."""

    def __init__(self, status: int | None = None, *, uncertain: bool = False):
        self.status = status
        self.uncertain = uncertain
        super().__init__(f"GitHub request failed (status={status or 'unavailable'}, uncertain={uncertain})")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class PageResult:
    items: list[dict[str, Any]]
    truncated: bool
    pages: int


class GitHubClient:
    """Fixed-origin client with explicit pagination and no automatic mutation retry."""

    def __init__(self, token: str, org: str = "untool-ai", *, timeout: float = 15,
                 max_pages: int = 10, opener: Callable[..., Any] | None = None):
        if not token or not re.fullmatch(r"[A-Za-z0-9-]+", org):
            raise ValueError("Token and valid organization required")
        if not 0 < timeout <= 60 or not 1 <= max_pages <= 100:
            raise ValueError("Invalid request bounds")
        self._token, self.org = token, org
        self.timeout, self.max_pages = timeout, max_pages
        self._open = opener or build_opener(_NoRedirect()).open

    def _repo(self, repository: str) -> str:
        if not re.fullmatch(re.escape(self.org) + r"/[A-Za-z0-9_.-]+", repository) or repository.rsplit("/", 1)[-1] in {".", ".."}:
            raise ValueError("Repository outside allowed organization")
        return repository

    @staticmethod
    def _id(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("Positive run identifier required")
        return value

    def _request(self, path: str, method: str = "GET", payload: dict | None = None) -> Any:
        request = Request("https://api.github.com" + path,
                          data=json.dumps(payload).encode() if payload is not None else None,
                          method=method, headers={"Authorization": "Bearer " + self._token,
                          "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                          "Content-Type": "application/json", "User-Agent": "untool-babysitter"})
        mutation = method != "GET"
        try:
            with self._open(request, timeout=self.timeout) as response:
                status = response.status
                if not 200 <= status < 300:
                    raise GitHubError(status, uncertain=mutation and status >= 500)
                raw = response.read(4_000_001)
                if len(raw) > 4_000_000:
                    raise GitHubError(status, uncertain=mutation)
                return json.loads(raw) if raw else {}
        except HTTPError as error:
            raise GitHubError(error.code, uncertain=mutation and error.code >= 500) from None
        except (URLError, OSError, TimeoutError, ValueError):
            raise GitHubError(uncertain=mutation) from None

    def _pages(self, path: str, key: str | None = None) -> PageResult:
        items: list[dict[str, Any]] = []
        for page in range(1, self.max_pages + 1):
            payload = self._request(path + ("&" if "?" in path else "?") + f"per_page=100&page={page}")
            batch = payload.get(key) if key and isinstance(payload, dict) else payload
            if not isinstance(batch, list) or any(not isinstance(row, dict) for row in batch):
                raise GitHubError()
            items.extend(batch)
            if len(batch) < 100:
                return PageResult(items, False, page)
        return PageResult(items, True, self.max_pages)

    def list_repositories(self, *, include_archived: bool = False) -> PageResult:
        """Enumerate every org repository across all pages (no first-page-only truncation)."""
        result = self._pages(f"/orgs/{self.org}/repos?type=all")
        if include_archived:
            return result
        active = [repo for repo in result.items if not repo.get("archived")]
        return PageResult(active, result.truncated, result.pages)

    def list_workflow_runs(self, repository: str, *, days: int = 30,
                           now: datetime | None = None) -> PageResult:
        if not 1 <= days <= 30:
            raise ValueError("Backfill bounded to 1–30 days")
        cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return self._pages(f"/repos/{self._repo(repository)}/actions/runs?created=%3E%3D{cutoff}", "workflow_runs")

    def list_pull_requests(self, repository: str, *, state: str = "open") -> PageResult:
        """Paginate every pull request page; never assume a single page is complete."""
        if state not in ("open", "closed", "all"):
            raise ValueError("Invalid pull request state filter")
        return self._pages(f"/repos/{self._repo(repository)}/pulls?state={state}")

    def get_required_status_contexts(self, repository: str, branch: str) -> list[str]:
        """Read-only required-check contexts for a protected branch; [] if unprotected."""
        if not isinstance(branch, str) or not 1 <= len(branch) <= 255:
            raise ValueError("Invalid branch")
        try:
            payload = self._request(
                f"/repos/{self._repo(repository)}/branches/{quote(branch, safe='')}/protection/required_status_checks")
        except GitHubError as error:
            if error.status == 404:
                return []
            raise
        if not isinstance(payload, dict):
            raise GitHubError()
        names = {name for name in payload.get("contexts") or [] if isinstance(name, str)}
        names |= {check.get("context") for check in payload.get("checks") or []
                  if isinstance(check, dict) and isinstance(check.get("context"), str)}
        return sorted(names)

    def get_run(self, repository: str, run_id: int) -> dict[str, Any]:
        result = self._request(f"/repos/{self._repo(repository)}/actions/runs/{self._id(run_id)}")
        if not isinstance(result, dict) or result.get("id") != run_id:
            raise GitHubError()
        return result

    def retry_run(self, repository: str, run_id: int) -> None:
        self._request(f"/repos/{self._repo(repository)}/actions/runs/{self._id(run_id)}/rerun-failed-jobs", "POST", {})

    def create_issue(self, repository: str, title: str, body: str) -> dict[str, Any]:
        if not title.strip() or len(title) > 256 or len(body) > 65536:
            raise ValueError("Invalid issue content")
        result = self._request(f"/repos/{self._repo(repository)}/issues", "POST", {"title": title, "body": body})
        if not isinstance(result, dict) or not isinstance(result.get("number"), int):
            raise GitHubError(uncertain=True)
        return result

    def get_pull(self, repository: str, number: int) -> dict[str, Any]:
        """Read a single pull request; number must be a positive int."""
        result = self._request(f"/repos/{self._repo(repository)}/pulls/{self._id(number)}")
        if not isinstance(result, dict) or result.get("number") != number:
            raise GitHubError()
        return result

    def get_combined_status(self, repository: str, sha: str) -> dict[str, Any]:
        """Combined commit status for a head SHA (legacy status API)."""
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("Invalid commit sha")
        result = self._request(f"/repos/{self._repo(repository)}/commits/{sha}/status")
        if not isinstance(result, dict):
            raise GitHubError()
        return result

    def list_check_runs(self, repository: str, sha: str) -> dict[str, Any]:
        """Check-runs for a head SHA; single page capped by API default bounds."""
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("Invalid commit sha")
        result = self._request(f"/repos/{self._repo(repository)}/commits/{sha}/check-runs?per_page=100")
        if not isinstance(result, dict) or not isinstance(result.get("check_runs"), list):
            raise GitHubError()
        return result

    def get_pull_approval_count(self, repository: str, number: int) -> int:
        """Count latest APPROVED reviews per unique user (dismissals reduce count)."""
        result = self._pages(f"/repos/{self._repo(repository)}/pulls/{self._id(number)}/reviews")
        latest: dict[str, str] = {}
        for review in result.items:
            user = review.get("user")
            state = review.get("state")
            if not isinstance(user, dict) or not isinstance(user.get("login"), str):
                continue
            if not isinstance(state, str):
                continue
            latest[user["login"]] = state.upper()
        return sum(1 for state in latest.values() if state == "APPROVED")

    def merge_pull(self, repository: str, number: int, *, head_sha: str,
                   merge_method: str = "squash", commit_title: str | None = None) -> dict[str, Any]:
        """Merge a pull request only when head_sha still matches; no automatic retry."""
        if merge_method not in ("merge", "squash", "rebase"):
            raise ValueError("Invalid merge method")
        if not isinstance(head_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", head_sha):
            raise ValueError("Invalid commit sha")
        payload: dict[str, Any] = {"merge_method": merge_method, "sha": head_sha}
        if commit_title is not None:
            if not isinstance(commit_title, str) or not 1 <= len(commit_title) <= 256:
                raise ValueError("Invalid commit title")
            payload["commit_title"] = commit_title
        result = self._request(
            f"/repos/{self._repo(repository)}/pulls/{self._id(number)}/merge", "PUT", payload)
        if not isinstance(result, dict):
            raise GitHubError(uncertain=True)
        return result

    def update_pull_branch(self, repository: str, number: int) -> dict[str, Any]:
        """Update a PR branch with the base branch; uncertain on transport failure."""
        result = self._request(
            f"/repos/{self._repo(repository)}/pulls/{self._id(number)}/update-branch", "PUT", {})
        if not isinstance(result, dict):
            raise GitHubError(uncertain=True)
        return result

    def notify(self, payload: dict[str, Any], *, webhook_url: str | None = None) -> None:
        """Send only operator-configured Slack webhook notifications, never follow redirects."""
        import os
        url = webhook_url or os.environ.get("SLACK_WEBHOOK_URL", "")
        if not re.fullmatch(r"https://hooks\.slack\.com/services/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+", url):
            raise ValueError("Approved Slack webhook required")
        data = json.dumps(payload).encode()
        if len(data) > 40_000:
            raise ValueError("Notification too large")
        request = Request(url, data=data, method="POST", headers={"Content-Type":"application/json"})
        try:
            with self._open(request, timeout=self.timeout) as response:
                if response.status != 200 or response.read(32).strip() != b"ok":
                    raise GitHubError(uncertain=True)
        except (HTTPError, URLError, OSError, TimeoutError):
            raise GitHubError(uncertain=True) from None
