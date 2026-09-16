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
from prbot.vcs.models import (
    FileDiff,
    InlineComment,
    PRDiff,
    PRMetadata,
    ReviewThread,
    validate_response,
)
from prbot.vcs.retry import send_with_retry

logger = logging.getLogger(__name__)

_STATE_MARKER = "<!-- prbot:state:"

# SEC-INPUT-04: an upper bound on a file fetched for context. The sizes are
# chosen by the contributor whose branch is under review, every non-removed
# file in the diff is fetched, and build_context_excerpt only ever uses a
# window around the hunks, so anything past this is retained for nothing.
# 2 MiB is far above any file a human reads in review and far below what
# would hurt a runner.
MAX_CONTEXT_BYTES = 2 * 1024 * 1024


# Hard cap on pages followed (D3). See the note in github.py.
_MAX_PAGES = 100


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

    async def get_file_content(self, path: str, ref: str) -> str | None:
        """Fetch a file's text at a revision (B8)."""
        url = (
            f"{self._base_url}/api/v4/projects/{self._encoded_repo}"
            f"/repository/files/{quote(path, safe='')}/raw"
        )
        try:
            response = await send_with_retry(
                self._client, "GET", url,
                classify=_classify_response, label="GitLab",
                params={"ref": ref},
                # SEC-INPUT-04: bound the body at the request
                # rather than after it has been buffered.
                headers={
                    "Range": f"bytes=0-{MAX_CONTEXT_BYTES - 1}",
                },
            )
        except VCSError as e:
            logger.info("context.unavailable path=%s: %s", path, e)
            return None

        # 206 means the server honoured the Range and had more to give, so
        # the file is over the bound. 200 with an oversized body means it
        # ignored the Range; the length check still catches that.
        if response.status_code == 206 or (
            len(response.content) > MAX_CONTEXT_BYTES
        ):
            logger.info(
                "context.too_large path=%s bytes=%d limit=%d status=%d",
                path, len(response.content), MAX_CONTEXT_BYTES,
                response.status_code,
            )
            return None
        return response.text

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

    async def submit_review(
        self,
        body: str,
        event: str,
        comments: list[InlineComment],
        *,
        head_sha: str,
        base_sha: str,
    ) -> int:
        """Post the positioned discussions and the verdict.

        GitLab has no review object, so there is nothing here for `body` to
        go in and it is deliberately unused. The caller posts the summary as
        its own comment, which carries the state record and is rewritten in
        place on the next run. Posting the body as a note as well left two
        notes on every merge request, and that one had no state marker for
        find_bot_comment to match, so a fresh copy accumulated every run.

        Returns 0: there is no review object to identify.
        """
        for comment in comments:
            position: dict[str, Any] = {
                "position_type": "text",
                "base_sha": base_sha,
                "start_sha": base_sha,
                "head_sha": head_sha,
                "new_path": comment.path,
                "old_path": comment.path,
                "new_line": comment.line,
            }
            # A line that was not added exists on both sides, and GitLab
            # cannot build a line code for it from the new side alone. An
            # added line has no old side, and naming one is equally invalid,
            # so the key is present exactly when the line has one.
            if comment.old_line is not None:
                position["old_line"] = comment.old_line
            try:
                await self._request(
                    "POST",
                    f"{self._base_url}/api/v4/projects/{self._encoded_repo}"
                    f"/merge_requests/{self._pr_number}/discussions",
                    json={"body": comment.body, "position": position},
                )
            except VCSError as e:
                # Almost always a line that has moved since the diff was
                # taken. Losing one anchor is acceptable; losing the review
                # is not.
                logger.warning(
                    "GitLab rejected an inline position for %s:%d: %s",
                    comment.path, comment.line, e,
                )

        if event in ("APPROVE", "REQUEST_CHANGES"):
            action = "approve" if event == "APPROVE" else "unapprove"
            try:
                await self._request(
                    "POST",
                    f"{self._base_url}/api/v4/projects/{self._encoded_repo}"
                    f"/merge_requests/{self._pr_number}/{action}",
                )
            except VCSError as e:
                # Approval rules can forbid this, and that is the project's
                # decision, not a review failure.
                logger.warning("GitLab %s was refused: %s", action, e)

        return 0

    async def list_review_threads(self) -> list[ReviewThread]:
        """List positioned discussions as review threads (C8).

        GitLab reports resolution state over REST, so unlike GitHub no second
        API is needed. Discussions without a position are plain notes rather
        than review threads and are skipped.
        """
        url = (
            f"{self._base_url}/api/v4/projects/{self._encoded_repo}"
            f"/merge_requests/{self._pr_number}/discussions"
        )
        collected: list[dict[str, Any]] = []
        await self._paginate(url, collected.extend)

        threads: list[ReviewThread] = []
        for discussion in collected:
            notes = discussion.get("notes") or []
            if not notes:
                continue
            first = notes[0]
            position = first.get("position") or {}
            if not position:
                continue
            threads.append(ReviewThread(
                id=str(discussion.get("id", "")),
                comment_id=first.get("id", 0),
                body=first.get("body", ""),
                resolved=bool(first.get("resolved")),
                path=position.get("new_path"),
                line=position.get("new_line"),
                author=(first.get("author") or {}).get("username", ""),
            ))
        return threads

    async def reply_to_thread(
        self, thread: ReviewThread, body: str,
    ) -> None:
        """Reply into an existing discussion."""
        url = (
            f"{self._base_url}/api/v4/projects/{self._encoded_repo}"
            f"/merge_requests/{self._pr_number}/discussions/{thread.id}/notes"
        )
        await self._request("POST", url, json={"body": body})

    async def resolve_thread(self, thread: ReviewThread) -> bool:
        """Mark a discussion resolved."""
        url = (
            f"{self._base_url}/api/v4/projects/{self._encoded_repo}"
            f"/merge_requests/{self._pr_number}/discussions/{thread.id}"
        )
        try:
            await self._request("PUT", url, json={"resolved": True})
        except VCSError as e:
            logger.warning(
                "Could not resolve discussion %s: %s", thread.id, e,
            )
            return False
        return True

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Make an HTTP request with retry and error classification (G4-15)."""
        response = await send_with_retry(
            self._client, method, url,
            classify=_classify_response, label="GitLab", **kwargs,
        )

        # Some GitLab endpoints return 204 No Content
        if response.status_code == 204:
            return {}
        return _decode_json(response, method, url)

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
        pages = 0

        while True:
            pages += 1
            if pages > _MAX_PAGES:
                raise VCSError(
                    f"GitLab API pagination exceeded {_MAX_PAGES} pages "
                    f"for {url}; refusing to follow further"
                )

            params["page"] = page
            response = await send_with_retry(
                self._client, "GET", url,
                classify=_classify_response, label="GitLab", params=params,
            )
            items = _expect_list(
                _decode_json(response, "GET", url), "GET", url,
            )

            if not items:
                return

            if callback(items):
                return  # Early exit

            # Check X-Next-Page header
            next_page = response.headers.get("x-next-page", "")
            if not next_page:
                return
            try:
                page = int(next_page)
            except ValueError as e:
                raise VCSResponseError(
                    f"GitLab API returned a non-numeric x-next-page header: "
                    f"{next_page!r} for {url}"
                ) from e


def _decode_json(
    response: httpx.Response, method: str, url: str,
) -> Any:
    """Decode a response body, classifying a non-JSON body (D4).

    A json.JSONDecodeError is not a PrBotError, so letting it escape exits
    the process with code 1 and reports an upstream HTML error page to CI
    as a blocking review.
    """
    try:
        return response.json()
    except ValueError as e:
        raise VCSResponseError(
            f"GitLab API returned a body that is not valid JSON: "
            f"{method} {url}. {response.text[:200]}"
        ) from e


def _expect_list(
    payload: Any, method: str, url: str,
) -> list[dict[str, Any]]:
    """Require a JSON array where the API contract promises one (D4)."""
    if not isinstance(payload, list):
        raise VCSResponseError(
            f"GitLab API returned {type(payload).__name__} where a list "
            f"was expected: {method} {url}"
        )
    return payload


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
