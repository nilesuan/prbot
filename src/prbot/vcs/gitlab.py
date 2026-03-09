"""GitLab REST API adapter (story-3-5).

Implements VCSAdapter protocol for GitLab.com and self-hosted GitLab.
Uses httpx async client with PRIVATE-TOKEN authentication.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

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

_STATE_MARKER = "<!-- prbot:state:"


class GitLabAdapter:
    """GitLab REST API adapter implementing VCSAdapter protocol."""

    def __init__(
        self,
        token: TokenResult,
        repo: str,
        pr_number: int,
        base_url: str = "https://gitlab.com",
    ) -> None:
        self._repo = repo
        self._encoded_repo = _encode_project_path(repo)
        self._pr_number = pr_number
        self._base_url = base_url.rstrip("/")
        self._authenticated_user: str | None = None
        self._client = httpx.AsyncClient(
            headers={
                "PRIVATE-TOKEN": token.as_private_token(),
            },
            timeout=30.0,
        )

    async def get_pr_metadata(self) -> PRMetadata:
        """Fetch MR metadata with state normalization and null coercion."""
        url = (
            f"{self._base_url}/api/v4/projects/{self._encoded_repo}"
            f"/merge_requests/{self._pr_number}"
        )
        data = await self._request("GET", url)
        validate_response(data, [
            "sha", "diff_refs.base_sha",
            "source_branch", "target_branch",
            "state", "author.username", "iid",
        ])

        # State normalization
        state = _normalize_gitlab_state(data["state"])

        # Fork detection: different project IDs
        source_id = data.get("source_project_id")
        target_id = data.get("target_project_id")
        is_fork = (
            source_id is not None
            and target_id is not None
            and source_id != target_id
        )

        return PRMetadata(
            title=data.get("title") or "",
            body=data.get("description") or "",
            state=state,
            head_sha=data["sha"],
            base_sha=data["diff_refs"]["base_sha"],
            head_ref=data["source_branch"],
            base_ref=data["target_branch"],
            author=data["author"]["username"],
            number=data["iid"],
            is_draft=bool(data.get("draft") or data.get("work_in_progress")),
            is_fork=is_fork,
        )

    async def get_diff(self) -> PRDiff:
        """Fetch MR diff via changes endpoint with overflow detection."""
        url = (
            f"{self._base_url}/api/v4/projects/{self._encoded_repo}"
            f"/merge_requests/{self._pr_number}/changes"
        )
        data = await self._request("GET", url)
        changes = data.get("changes", [])
        overflow = data.get("overflow", False)

        files = [
            FileDiff(
                path=c.get("new_path", ""),
                status=_normalize_gitlab_file_status(c),
                patch=c.get("diff", ""),
                additions=_count_additions(c.get("diff", "")),
                deletions=_count_deletions(c.get("diff", "")),
                previous_path=(
                    c.get("old_path")
                    if c.get("renamed_file")
                    else None
                ),
            )
            for c in changes
        ]

        head_sha = data.get("sha", "")
        diff_refs = data.get("diff_refs", {})
        base_sha = diff_refs.get("base_sha", "")

        return PRDiff(
            files=files,
            head_sha=head_sha,
            base_sha=base_sha,
            truncated=overflow,
        )

    async def get_authenticated_user(self) -> str:
        """Get authenticated user username, cached after first call."""
        if self._authenticated_user is None:
            data = await self._request(
                "GET", f"{self._base_url}/api/v4/user",
            )
            self._authenticated_user = data.get("username", "")
        return self._authenticated_user

    async def find_bot_comment(self) -> tuple[int, str] | None:
        """Find existing bot comment in MR notes with early-exit."""
        bot_user = await self.get_authenticated_user()
        url = (
            f"{self._base_url}/api/v4/projects/{self._encoded_repo}"
            f"/merge_requests/{self._pr_number}/notes"
        )

        result: tuple[int, str] | None = None

        def _check_notes(items: list[dict[str, Any]]) -> bool:
            nonlocal result
            for note in items:
                author = note.get("author", {}).get("username", "")
                body = note.get("body", "")
                if author == bot_user and _STATE_MARKER in body:
                    result = (note["id"], body)
                    return True
            return False

        await self._paginate(url, _check_notes)
        return result

    async def post_comment(self, body: str) -> int:
        """Post a new note on the MR."""
        url = (
            f"{self._base_url}/api/v4/projects/{self._encoded_repo}"
            f"/merge_requests/{self._pr_number}/notes"
        )
        data = await self._request("POST", url, json={"body": body})
        return data["id"]

    async def update_comment(self, comment_id: int, body: str) -> None:
        """Update an existing MR note by ID."""
        url = (
            f"{self._base_url}/api/v4/projects/{self._encoded_repo}"
            f"/merge_requests/{self._pr_number}/notes/{comment_id}"
        )
        await self._request("PUT", url, json={"body": body})

    async def post_or_update_comment(self, body: str) -> int:
        """Idempotent: find existing bot note → update, or create new."""
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
                f"GitLab API timed out: {method} {url}"
            ) from e
        except httpx.ConnectError as e:
            raise VCSError(
                f"GitLab API unreachable: {method} {url}"
            ) from e

        _classify_response(response, method, url)

        # Some GitLab endpoints return 204 No Content
        if response.status_code == 204:
            return {}
        return response.json()

    async def _paginate(
        self,
        url: str,
        callback: Callable[[list[dict[str, Any]]], bool | None],
    ) -> None:
        """Paginate GitLab API using X-Next-Page header.

        Args:
            url: Initial page URL.
            callback: Called with each page's items. Return True for early exit.
        """
        params: dict[str, Any] = {"per_page": 100}
        page = 1

        while True:
            params["page"] = page
            try:
                response = await self._client.get(url, params=params)
            except httpx.TimeoutException as e:
                raise VCSError(
                    f"GitLab API timed out during pagination: {url}"
                ) from e
            except httpx.ConnectError as e:
                raise VCSError(
                    f"GitLab API unreachable during pagination: {url}"
                ) from e

            _classify_response(response, "GET", url)
            items = response.json()

            if not items:
                return

            if callback(items):
                return  # Early exit

            # Check X-Next-Page header
            next_page = response.headers.get("x-next-page", "")
            if not next_page:
                return
            page = int(next_page)


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
            f"GitLab API authentication failed (401): {method} {url}"
        )
    if status == 403:
        raise VCSAuthError(
            f"GitLab API forbidden (403): {method} {url}. {body_text}"
        )
    if status == 404:
        raise VCSNotFoundError(
            f"GitLab API not found (404): {method} {url}"
        )
    if status == 429:
        raise VCSRateLimitError(
            f"GitLab API rate_limited (429): {method} {url}"
        )
    if status >= 500:
        raise VCSServerError(
            f"GitLab API server error ({status}): {method} {url}"
        )
    raise VCSResponseError(
        f"GitLab API unexpected status ({status}): {method} {url}. "
        f"{body_text}"
    )


def _encode_project_path(repo: str) -> str:
    """URL-encode project path for GitLab API (owner/repo → owner%2Frepo)."""
    return quote(repo, safe="")


def _normalize_gitlab_state(state: str) -> str:
    """Normalize GitLab MR state to standard values."""
    mapping = {
        "opened": "open",
        "closed": "closed",
        "merged": "merged",
        "locked": "closed",
    }
    return mapping.get(state, state)


def _normalize_gitlab_file_status(change: dict[str, Any]) -> str:
    """Derive file status from GitLab change flags."""
    if change.get("new_file"):
        return "added"
    if change.get("deleted_file"):
        return "removed"
    if change.get("renamed_file"):
        return "renamed"
    return "modified"


def _count_additions(diff: str) -> int:
    """Count added lines in a unified diff patch."""
    return sum(
        1 for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )


def _count_deletions(diff: str) -> int:
    """Count deleted lines in a unified diff patch."""
    return sum(
        1 for line in diff.splitlines()
        if line.startswith("-") and not line.startswith("---")
    )
