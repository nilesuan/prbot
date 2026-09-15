"""VCS adapter protocol (story-3-2).

Defines the structural interface that GitHub and GitLab adapters must satisfy.
Uses Protocol (PEP 544) for structural subtyping — no inheritance required.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from prbot.vcs.models import InlineComment, PRDiff, PRMetadata


@runtime_checkable
class VCSAdapter(Protocol):
    """Protocol for VCS platform adapters.

    Both GitHubAdapter and GitLabAdapter must implement all methods.
    Uses structural subtyping — classes don't need to explicitly inherit.
    """

    async def get_pr_metadata(self) -> PRMetadata:
        """Fetch PR/MR metadata (title, body, state, SHAs, author).

        Normalizes platform-specific states to: open, closed, merged.
        Coerces null title/body to empty strings.
        """
        ...

    async def get_diff(self) -> PRDiff:
        """Fetch the PR/MR diff with all file changes.

        Handles pagination for large diffs. Detects truncation.
        """
        ...

    async def get_file_content(self, path: str, ref: str) -> str | None:
        """Fetch a file's text at a revision, or None if unavailable (B8).

        Returns None rather than raising for a file that does not exist at
        that revision, is binary, or is too large: expanded context is an
        improvement to the prompt, not a precondition for reviewing.
        """
        ...

    async def get_authenticated_user(self) -> str:
        """Get the login/username of the authenticated token holder.

        Result is cached after the first call.
        """
        ...

    async def find_bot_comment(self) -> tuple[int, str] | None:
        """Find an existing bot comment on the PR/MR.

        Searches for the prbot state marker in comments authored
        by the authenticated user. Uses early-exit pagination.

        Returns (comment_id, body) if found, None otherwise.
        """
        ...

    async def post_comment(self, body: str) -> int:
        """Post a new comment on the PR/MR.

        Returns the comment ID.
        """
        ...

    async def update_comment(self, comment_id: int, body: str) -> None:
        """Update an existing comment by ID."""
        ...

    async def post_or_update_comment(self, body: str) -> int:
        """Idempotent comment: find existing bot comment and update, or create new.

        Returns the comment ID (new or existing).
        """
        ...

    async def submit_review(
        self,
        body: str,
        event: str,
        comments: list[InlineComment],
        *,
        head_sha: str,
        base_sha: str,
    ) -> int:
        """Submit a platform review with inline comments (C1, C2).

        `event` is one of APPROVE, REQUEST_CHANGES or COMMENT. GitHub has a
        review object that carries all three plus the comments; GitLab has
        discussions for the comments and approve/unapprove for the verdict,
        so the adapters differ in how they satisfy this, not in what it means.

        An inline comment whose position the platform rejects is dropped with
        a warning rather than failing the submission: a stale line must not
        take the whole review down with it.

        Returns an identifier for the submitted review or summary note.
        """
        ...

    async def close(self) -> None:
        """Close the HTTP client and release resources."""
        ...
