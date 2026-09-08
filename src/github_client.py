"""Bounded GitHub adapter; credentials never enter diagnostics or URLs."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.error import HTTPError, URLError
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

    def list_repositories(self) -> PageResult:
        return self._pages(f"/orgs/{self.org}/repos?type=all")

    def list_workflow_runs(self, repository: str, *, days: int = 30,
                           now: datetime | None = None) -> PageResult:
        if not 1 <= days <= 30:
            raise ValueError("Backfill bounded to 1–30 days")
        cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return self._pages(f"/repos/{self._repo(repository)}/actions/runs?created=%3E%3D{cutoff}", "workflow_runs")

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
