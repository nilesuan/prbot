"""Tests for pre-flight checks and bot detection (story-7-5)."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prbot.cli import EXIT_PASS, _is_bot_author, run_pipeline
from prbot.config import PrBotConfig
from prbot.vcs.models import FileDiff, PRDiff, PRMetadata

_HEAD_SHA = "abcdef1234567890abcdef1234567890abcdef12"
_BASE_SHA = "1234567890abcdef1234567890abcdef12345678"


def _make_config(**overrides: object) -> PrBotConfig:
    defaults: dict[str, Any] = {
        "platform": "github",
        "repo": "owner/repo",
        "pr_number": 42,
        "dry_run": True,
        "draft_behavior": "skip",
        # Use non-prefixed model IDs to avoid residency checks
        "general_model_id": "anthropic.claude-sonnet-4-20250514",
        "security_model_id": "anthropic.claude-opus-4-0-20250514",
    }
    defaults.update(overrides)
    return PrBotConfig(**defaults)


def _make_metadata(**overrides: object) -> PRMetadata:
    defaults: dict[str, Any] = {
        "title": "Test PR",
        "body": "Test body",
        "state": "open",
        "head_sha": _HEAD_SHA,
        "base_sha": _BASE_SHA,
        "head_ref": "feature/test",
        "base_ref": "main",
        "author": "testuser",
        "number": 42,
    }
    defaults.update(overrides)
    return PRMetadata(**defaults)


def _make_diff(
    files: list[FileDiff] | None = None,
) -> PRDiff:
    if files is None:
        files = [
            FileDiff(
                path="src/example.py",
                status="modified",
                patch="@@ -1,3 +1,4 @@\n import os\n+import sys\n",
                additions=1,
                deletions=0,
            ),
        ]
    return PRDiff(files=files, head_sha=_HEAD_SHA, base_sha=_BASE_SHA)


def _mock_token() -> MagicMock:
    token = MagicMock()
    token.mask_in_ci = lambda: None
    token.source = "env"
    token.redacted = "***"
    return token


@contextmanager
def _pipeline_patches(
    adapter: AsyncMock,
    **extra: Any,
) -> Generator[AsyncMock, None, None]:
    """Patch auth + VCS adapter creation for pipeline tests."""
    resolve = "prbot.auth.resolve_token"
    validate = "prbot.auth.validate_aws_session_credentials"
    scopes = "prbot.auth.validate_token_scopes"
    create = "prbot.vcs.create_vcs_adapter"
    with (
        patch(resolve, new_callable=AsyncMock) as mock_r,
        patch(validate),
        patch(scopes, new_callable=AsyncMock),
        patch(create, return_value=adapter),
    ):
        mock_r.return_value = _mock_token()
        for key, val in extra.items():
            patch(key, val)
        yield mock_r


class TestPreFlightChecks:
    """Tests for pre-flight short-circuits in run_pipeline."""

    @pytest.mark.asyncio
    async def test_closed_pr_skips_review(self) -> None:
        """S81: Closed PR exits immediately."""
        config = _make_config()
        adapter = AsyncMock()
        adapter.get_pr_metadata.return_value = _make_metadata(
            state="closed",
        )

        with _pipeline_patches(adapter):
            result = await run_pipeline(config)

        assert result == EXIT_PASS
        adapter.get_diff.assert_not_called()

    @pytest.mark.asyncio
    async def test_merged_pr_skips_review(self) -> None:
        """S81: Merged PR exits immediately."""
        config = _make_config()
        adapter = AsyncMock()
        adapter.get_pr_metadata.return_value = _make_metadata(
            state="merged",
        )

        with _pipeline_patches(adapter):
            result = await run_pipeline(config)

        assert result == EXIT_PASS
        adapter.get_diff.assert_not_called()

    @pytest.mark.asyncio
    async def test_draft_pr_skip_behavior(self) -> None:
        """S34: Draft + skip exits immediately."""
        config = _make_config(draft_behavior="skip")
        adapter = AsyncMock()
        adapter.get_pr_metadata.return_value = _make_metadata(
            is_draft=True,
        )

        with _pipeline_patches(adapter):
            result = await run_pipeline(config)

        assert result == EXIT_PASS
        adapter.get_diff.assert_not_called()

    @pytest.mark.asyncio
    async def test_draft_pr_review_behavior(self) -> None:
        """S34: Draft + review proceeds past draft check."""
        config = _make_config(draft_behavior="review")
        adapter = AsyncMock()
        adapter.get_pr_metadata.return_value = _make_metadata(
            is_draft=True,
        )
        adapter.get_authenticated_user.return_value = "prbot[bot]"
        adapter.get_diff.return_value = _make_diff()

        empty = PRDiff(
            files=[], head_sha=_HEAD_SHA, base_sha=_BASE_SHA,
        )

        filter_path = "prbot.security.diff_filter.filter_diff"
        with _pipeline_patches(adapter), \
             patch(filter_path, return_value=empty):
            result = await run_pipeline(config)

        adapter.get_diff.assert_called_once()
        assert result == EXIT_PASS

    @pytest.mark.asyncio
    async def test_bot_author_skips_review(self) -> None:
        """S71: Known bot author triggers skip."""
        config = _make_config()
        adapter = AsyncMock()
        adapter.get_pr_metadata.return_value = _make_metadata(
            author="dependabot[bot]",
        )
        adapter.get_authenticated_user.return_value = "prbot[bot]"

        with _pipeline_patches(adapter):
            result = await run_pipeline(config)

        assert result == EXIT_PASS
        adapter.get_diff.assert_not_called()

    @pytest.mark.asyncio
    async def test_self_review_dynamic_check(self) -> None:
        """G4-03: Dynamic self-review detection."""
        config = _make_config()
        adapter = AsyncMock()
        adapter.get_pr_metadata.return_value = _make_metadata(
            author="my-custom-bot",
        )
        adapter.get_authenticated_user.return_value = "my-custom-bot"

        with _pipeline_patches(adapter):
            result = await run_pipeline(config)

        assert result == EXIT_PASS
        adapter.get_diff.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_diff_after_filtering(self) -> None:
        """S32: Empty diff after filtering exits."""
        config = _make_config()
        adapter = AsyncMock()
        adapter.get_pr_metadata.return_value = _make_metadata()
        adapter.get_diff.return_value = _make_diff()
        adapter.get_authenticated_user.return_value = "prbot[bot]"

        empty = PRDiff(
            files=[], head_sha=_HEAD_SHA, base_sha=_BASE_SHA,
        )
        filter_path = "prbot.security.diff_filter.filter_diff"
        with _pipeline_patches(adapter), \
             patch(filter_path, return_value=empty):
            result = await run_pipeline(config)

        assert result == EXIT_PASS


class TestIsBotAuthor:
    """Tests for _is_bot_author helper."""

    def test_dependabot(self) -> None:
        assert _is_bot_author(
            "dependabot[bot]", authenticated_user="prbot[bot]",
        )

    def test_renovate(self) -> None:
        assert _is_bot_author(
            "renovate[bot]", authenticated_user="prbot[bot]",
        )

    def test_github_actions(self) -> None:
        assert _is_bot_author(
            "github-actions[bot]",
            authenticated_user="prbot[bot]",
        )

    def test_custom_bot_suffix(self) -> None:
        assert _is_bot_author(
            "my-app[bot]", authenticated_user="prbot[bot]",
        )

    def test_human_author(self) -> None:
        assert not _is_bot_author(
            "alice", authenticated_user="prbot[bot]",
        )

    def test_bot_prefix(self) -> None:
        assert _is_bot_author(
            "bot-reviewer", authenticated_user="prbot[bot]",
        )

    def test_dynamic_self_match(self) -> None:
        """G4-03: Author matches authenticated user."""
        assert _is_bot_author(
            "my-custom-bot", authenticated_user="my-custom-bot",
        )

    def test_case_insensitive(self) -> None:
        assert _is_bot_author(
            "Dependabot[bot]", authenticated_user="prbot[bot]",
        )


class TestScopeValidationRuns:
    """A9: the check must actually be reached by the pipeline."""

    @pytest.mark.asyncio
    async def test_pipeline_validates_token_scopes(self) -> None:
        config = _make_config()
        adapter = AsyncMock()
        adapter.get_pr_metadata.return_value = _make_metadata(state="closed")

        # _pipeline_patches also patches the scope validator, so this patch
        # has to be entered last to be the one in effect.
        with _pipeline_patches(adapter), patch(
            "prbot.auth.validate_token_scopes", new_callable=AsyncMock,
        ) as mock_scopes:
            await run_pipeline(config)

        mock_scopes.assert_awaited_once()
        args = mock_scopes.await_args.args
        assert args[1] == "github"

    @pytest.mark.asyncio
    async def test_insufficient_scopes_stops_before_any_api_call(self) -> None:
        from prbot.exceptions import InsufficientScopesError

        config = _make_config()
        adapter = AsyncMock()

        failing = AsyncMock(
            side_effect=InsufficientScopesError("missing repo"),
        )
        with _pipeline_patches(adapter), \
                patch("prbot.auth.validate_token_scopes", failing), \
                pytest.raises(InsufficientScopesError):
            await run_pipeline(config)

        adapter.get_pr_metadata.assert_not_called()


class TestReviewContextIsAlwaysCleared:
    """GEN-ERR-03: bind ran before the try/finally that clears it.

    Several fallible steps ran between bind_review_context and the try:
    line, so a ConfigError or AuthError from any of them left repo, pr_number
    and review_id bound to the contextvars for the life of the process.
    """

    @pytest.mark.asyncio
    async def test_context_is_cleared_when_scope_validation_fails(
        self,
    ) -> None:
        import structlog

        from prbot.exceptions import InsufficientScopesError

        config = _make_config()
        adapter = AsyncMock()
        failing = AsyncMock(
            side_effect=InsufficientScopesError("missing repo"),
        )
        with _pipeline_patches(adapter), \
                patch("prbot.auth.validate_token_scopes", failing), \
                pytest.raises(InsufficientScopesError):
            await run_pipeline(config)

        assert structlog.contextvars.get_contextvars() == {}

    @pytest.mark.asyncio
    async def test_context_is_cleared_when_residency_fails(self) -> None:
        import structlog

        from prbot.exceptions import ConfigError

        config = _make_config(
            general_model_id="us.anthropic.claude-sonnet-4-6",
            aws_region="ap-southeast-2",
            allowed_regions=[],
        )
        adapter = AsyncMock()
        with _pipeline_patches(adapter), pytest.raises(ConfigError):
            await run_pipeline(config)

        assert structlog.contextvars.get_contextvars() == {}

    @pytest.mark.asyncio
    async def test_context_is_cleared_on_the_happy_path(self) -> None:
        import structlog

        config = _make_config()
        adapter = AsyncMock()
        adapter.get_pr_metadata.return_value = _make_metadata(state="closed")
        with _pipeline_patches(adapter):
            await run_pipeline(config)

        assert structlog.contextvars.get_contextvars() == {}
