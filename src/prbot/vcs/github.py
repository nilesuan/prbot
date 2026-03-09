"""GitHub REST API adapter (stories 3-3, 3-4).

Implements VCSAdapter protocol for GitHub.com and GitHub Enterprise.
Uses httpx async client with Bearer token authentication.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import httpx

from prbot.auth.token import TokenResult
from prbot.exceptions import (
    VCSAuthError,
    VCSError,
    VCSNotFoundError,
    VCSRateLimitError,
    VCSResponseError,
    VCSServerError,
)
from prbot.vcs.models import FileDiff, PRDiff, PRMetadata, validate_response

logger = logging.getLogger(__name__)

# State marker for bot comments
_STATE_MARKER = "<!-- prbot:state:"

# GitHub API file limit before truncation
_GITHUB_FILE_LIMIT = 3000


class GitHubAdapter:
    """GitHub REST API adapter implementing VCSAdapter protocol."""

    def __init__(
        self,
        token: TokenResult,
        repo: str,
        pr_number: int,
        base_url: str = "https://api.github.com",
    ) -> None:
        self._repo = repo
        self._pr_number = pr_number
        self._base_url = base_url.rstrip("/")
        self._authenticated_user: str | None = None
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": token.as_bearer_header(),
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )

    async def get_pr_metadata(self) -> PRMetadata:
        """Fetch PR metadata with state normalization and null coercion."""
        url = (
            f"{self._base_url}/repos/{self._repo}"
            f"/pulls/{self._pr_number}"
        )
        data = await self._request("GET", url)
        validate_response(data, [
            "head.sha", "base.sha", "head.ref", "base.ref",
            "state", "user.login", "number",
        ])

        # State normalization: GitHub uses "closed" for both closed and merged
        state = data["state"]
        if state == "closed" and data.get("merged"):
            state = "merged"

        # Fork detection
        head_repo = data.get("head", {}).get("repo") or {}
        base_repo = data.get("base", {}).get("repo") or {}
        is_fork = head_repo.get("full_name") != base_repo.get("full_name")

        return PRMetadata(
            title=data.get("title") or "",
            body=data.get("body") or "",
            state=state,
            head_sha=data["head"]["sha"],
            base_sha=data["base"]["sha"],
            head_ref=data["head"]["ref"],
            base_ref=data["base"]["ref"],
            author=data["user"]["login"],
            number=data["number"],
            is_draft=bool(data.get("draft")),
            is_fork=is_fork,
        )

    async def get_diff(self) -> PRDiff:
        """Fetch PR diff via paginated List PR Files endpoint."""
        url = (
            f"{self._base_url}/repos/{self._repo}"
            f"/pulls/{self._pr_number}/files"
        )
        all_files: list[dict[str, Any]] = []
        await self._paginate(url, all_files.extend)

        files = [
            FileDiff(
                path=f.get("filename", ""),
                status=_normalize_file_status(f.get("status", "modified")),
                patch=f.get("patch", ""),
                additions=f.get("additions", 0),
                deletions=f.get("deletions", 0),
                previous_path=f.get("previous_filename"),
            )
            for f in all_files
        ]

        # Get SHAs from PR metadata for the diff
        pr_url = (
            f"{self._base_url}/repos/{self._repo}"
            f"/pulls/{self._pr_number}"
        )
        pr_data = await self._request("GET", pr_url)
        head_sha = pr_data.get("head", {}).get("sha", "")
        base_sha = pr_data.get("base", {}).get("sha", "")

        return PRDiff(
            files=files,
            head_sha=head_sha,
            base_sha=base_sha,
            truncated=len(files) >= _GITHUB_FILE_LIMIT,
        )

    async def get_authenticated_user(self) -> str:
        """Get authenticated user login, cached after first call."""
        if self._authenticated_user is None:
            data = await self._request("GET", f"{self._base_url}/user")
            self._authenticated_user = data.get("login", "")
        return self._authenticated_user

    async def find_bot_comment(self) -> tuple[int, str] | None:
        """Find existing bot comment with early-exit pagination."""
        bot_user = await self.get_authenticated_user()
        url = (
            f"{self._base_url}/repos/{self._repo}"
            f"/issues/{self._pr_number}/comments"
        )

        result: tuple[int, str] | None = None

        def _check_comments(items: list[dict[str, Any]]) -> bool:
            """Process comment page. Returns True to stop pagination."""
            nonlocal result
            for comment in items:
                author = comment.get("user", {}).get("login", "")
                body = comment.get("body", "")
                if author == bot_user and _STATE_MARKER in body:
                    result = (comment["id"], body)
                    return True  # Early exit
            return False

        await self._paginate(url, _check_comments)
        return result

    async def post_comment(self, body: str) -> int:
        """Post a new comment on the PR."""
        url = (
            f"{self._base_url}/repos/{self._repo}"
            f"/issues/{self._pr_number}/comments"
        )
        data = await self._request("POST", url, json={"body": body})
        return data["id"]

    async def update_comment(self, comment_id: int, body: str) -> None:
        """Update an existing comment by ID."""
        url = (
            f"{self._base_url}/repos/{self._repo}"
            f"/issues/comments/{comment_id}"
        )
        await self._request("PATCH", url, json={"body": body})

    async def post_or_update_comment(self, body: str) -> int:
        """Idempotent: find existing bot comment → update, or create new."""
        existing = await self.find_bot_comment()
        if existing:
            comment_id, _ = existing
            await self.update_comment(comment_id, body)
            return comment_id
        return await self.post_comment(body)

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Make an HTTP request with error classification (G4-15)."""
        try:
            response = await self._client.request(method, url, **kwargs)
        except httpx.TimeoutException as e:
            raise VCSError(
                f"GitHub API timed out: {method} {url}"
            ) from e
        except httpx.ConnectError as e:
            raise VCSError(
                f"GitHub API unreachable: {method} {url}"
            ) from e

        _classify_response(response, method, url)
        return response.json()

    async def _paginate(
        self,
        url: str,
        callback: Callable[[list[dict[str, Any]]], bool | None],
    ) -> None:
        """Paginate GitHub API using Link: rel="next" header.

        Args:
            url: Initial page URL.
            callback: Called with each page's items. Return True for early exit.
        """
        params: dict[str, Any] = {"per_page": 100}
        current_url: str | None = url

        while current_url:
            try:
                response = await self._client.get(
                    current_url, params=params,
                )
            except httpx.TimeoutException as e:
                raise VCSError(
                    f"GitHub API timed out during pagination: {current_url}"
                ) from e
            except httpx.ConnectError as e:
                raise VCSError(
                    f"GitHub API unreachable during pagination: {current_url}"
                ) from e

            _classify_response(response, "GET", current_url)
            items = response.json()

            if callback(items):
                return  # Early exit requested

            # Clear params after first request (Link URL includes them)
            params = {}

            # Follow Link: rel="next"
            current_url = _parse_next_link(
                response.headers.get("link", ""),
            )


def _classify_response(
    response: httpx.Response, method: str, url: str,
) -> None:
    """Classify HTTP response status into typed exceptions (G4-15)."""
    status = response.status_code
    if 200 <= status < 300:
        return

    body_text = response.text[:200]

    if status == 401:
        raise VCSAuthError(
            f"GitHub API authentication failed (401): {method} {url}"
        )
    if status == 403:
        raise VCSAuthError(
            f"GitHub API forbidden (403): {method} {url}. {body_text}"
        )
    if status == 404:
        raise VCSNotFoundError(
            f"GitHub API not found (404): {method} {url}"
        )
    if status == 429:
        raise VCSRateLimitError(
            f"GitHub API rate_limited (429): {method} {url}"
        )
    if status >= 500:
        raise VCSServerError(
            f"GitHub API server error ({status}): {method} {url}"
        )
    raise VCSResponseError(
        f"GitHub API unexpected status ({status}): {method} {url}. "
        f"{body_text}"
    )


def _parse_next_link(link_header: str) -> str | None:
    """Extract next page URL from GitHub Link header."""
    for part in link_header.split(","):
        if 'rel="next"' in part:
            # Extract URL between < and >
            start = part.find("<")
            end = part.find(">")
            if start != -1 and end != -1:
                return part[start + 1 : end]
    return None


def _normalize_file_status(status: str) -> str:
    """Normalize GitHub file status to standard values."""
    mapping = {
        "added": "added",
        "removed": "removed",
        "modified": "modified",
        "renamed": "renamed",
        "copied": "added",
        "changed": "modified",
    }
    return mapping.get(status, "modified")
