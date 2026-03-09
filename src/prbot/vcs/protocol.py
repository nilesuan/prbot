"""VCS adapter protocol (story-3-2).

Defines the structural interface that GitHub and GitLab adapters must satisfy.
Uses Protocol (PEP 544) for structural subtyping — no inheritance required.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from prbot.vcs.models import PRDiff, PRMetadata


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

    async def close(self) -> None:
        """Close the HTTP client and release resources."""
        ...
