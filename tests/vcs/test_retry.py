"""Tests for VCS retry, rate-limit classification and pagination safety (D3)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from prbot.auth.token import TokenResult
from prbot.exceptions import (
    VCSAuthError,
    VCSError,
    VCSRateLimitError,
    VCSServerError,
)
from prbot.vcs.github import GitHubAdapter
from prbot.vcs.gitlab import GitLabAdapter
from prbot.vcs.retry import backoff_seconds, parse_retry_after

# Backoff is zeroed for the whole suite by tests/conftest.py.


def _github() -> GitHubAdapter:
    return GitHubAdapter(
        token=TokenResult(value="ghp_t", source="test"),
        repo="owner/repo",
        pr_number=42,
    )


def _gitlab() -> GitLabAdapter:
    return GitLabAdapter(
        token=TokenResult(value="glpat-t", source="test"),
        repo="owner/repo",
        pr_number=99,
    )


class TestRetryAfterParsing:
    def test_integer_seconds(self) -> None:
        assert parse_retry_after("30") == 30.0

    def test_http_date(self) -> None:
        value = parse_retry_after("Wed, 21 Oct 2099 07:28:00 GMT")
        assert value is not None and value > 0

    def test_garbage_is_ignored(self) -> None:
        assert parse_retry_after("soon") is None

    def test_absent_is_ignored(self) -> None:
        assert parse_retry_after(None) is None

    def test_backoff_grows_with_attempts(self) -> None:
        import prbot.vcs.retry as retry_module

        original = retry_module.RETRY_BASE_SECONDS
        retry_module.RETRY_BASE_SECONDS = 1.0
        retry_module.MAX_RETRY_AFTER_SECONDS = 600.0
        try:
            assert backoff_seconds(0) < backoff_seconds(3)
        finally:
            retry_module.RETRY_BASE_SECONDS = original


class TestTransientFailuresAreRetried:
    """A single 429 or 503 previously aborted the whole run."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_rate_limit_then_success(
        self, github_pr_response: dict[str, Any],
    ) -> None:
        route = respx.get(
            "https://api.github.com/repos/owner/repo/pulls/42",
        )
        route.side_effect = [
            httpx.Response(429, headers={"retry-after": "0"}),
            httpx.Response(200, json=github_pr_response),
        ]
        adapter = _github()
        try:
            assert (await adapter.get_pr_metadata()).number == 42
            assert route.call_count == 2
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_server_error_then_success(
        self, github_pr_response: dict[str, Any],
    ) -> None:
        route = respx.get("https://api.github.com/repos/owner/repo/pulls/42")
        route.side_effect = [
            httpx.Response(503),
            httpx.Response(200, json=github_pr_response),
        ]
        adapter = _github()
        try:
            await adapter.get_pr_metadata()
            assert route.call_count == 2
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_timeout_then_success(
        self, github_pr_response: dict[str, Any],
    ) -> None:
        route = respx.get("https://api.github.com/repos/owner/repo/pulls/42")
        route.side_effect = [
            httpx.ReadTimeout("slow"),
            httpx.Response(200, json=github_pr_response),
        ]
        adapter = _github()
        try:
            await adapter.get_pr_metadata()
            assert route.call_count == 2
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_persistent_rate_limit_eventually_raises(self) -> None:
        route = respx.get("https://api.github.com/repos/owner/repo/pulls/42")
        route.mock(return_value=httpx.Response(429))
        adapter = _github()
        try:
            with pytest.raises(VCSRateLimitError):
                await adapter.get_pr_metadata()
            assert route.call_count == 4
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_persistent_server_error_eventually_raises(self) -> None:
        route = respx.get("https://api.github.com/repos/owner/repo/pulls/42")
        route.mock(return_value=httpx.Response(500))
        adapter = _github()
        try:
            with pytest.raises(VCSServerError):
                await adapter.get_pr_metadata()
            assert route.call_count == 4
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_auth_failure_is_not_retried(self) -> None:
        """A 401 will not start working; retrying it burns the budget."""
        route = respx.get("https://api.github.com/repos/owner/repo/pulls/42")
        route.mock(return_value=httpx.Response(401))
        adapter = _github()
        try:
            with pytest.raises(VCSAuthError):
                await adapter.get_pr_metadata()
            assert route.call_count == 1
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_not_found_is_not_retried(self) -> None:
        route = respx.get("https://api.github.com/repos/owner/repo/pulls/42")
        route.mock(return_value=httpx.Response(404))
        adapter = _github()
        try:
            with pytest.raises(VCSError):
                await adapter.get_pr_metadata()
            assert route.call_count == 1
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_gitlab_retries_too(self) -> None:
        route = respx.get("https://gitlab.com/api/v4/user")
        route.side_effect = [
            httpx.Response(429),
            httpx.Response(200, json={"username": "bot"}),
        ]
        adapter = _gitlab()
        try:
            assert await adapter.get_authenticated_user() == "bot"
            assert route.call_count == 2
        finally:
            await adapter.close()


class TestGitHubRateLimitClassification:
    """GitHub signals its primary rate limit with 403, not 429."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_403_with_exhausted_quota_is_a_rate_limit(self) -> None:
        route = respx.get("https://api.github.com/repos/owner/repo/pulls/42")
        route.mock(
            return_value=httpx.Response(
                403,
                headers={"x-ratelimit-remaining": "0"},
                json={"message": "API rate limit exceeded"},
            ),
        )
        adapter = _github()
        try:
            with pytest.raises(VCSRateLimitError):
                await adapter.get_pr_metadata()
            # Retried, because it will clear
            assert route.call_count == 4
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_403_with_retry_after_is_a_rate_limit(self) -> None:
        """Secondary rate limits send Retry-After with a 403."""
        route = respx.get("https://api.github.com/repos/owner/repo/pulls/42")
        route.mock(
            return_value=httpx.Response(403, headers={"retry-after": "0"}),
        )
        adapter = _github()
        try:
            with pytest.raises(VCSRateLimitError):
                await adapter.get_pr_metadata()
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_403_without_quota_headers_is_still_auth(self) -> None:
        route = respx.get("https://api.github.com/repos/owner/repo/pulls/42")
        route.mock(
            return_value=httpx.Response(
                403,
                headers={"x-ratelimit-remaining": "4999"},
                json={"message": "Resource not accessible by integration"},
            ),
        )
        adapter = _github()
        try:
            with pytest.raises(VCSAuthError):
                await adapter.get_pr_metadata()
            assert route.call_count == 1
        finally:
            await adapter.close()


class TestPaginationIsBounded:
    """An unbounded loop over a server-supplied URL was two problems."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_cyclic_link_header_terminates(self) -> None:
        url = "https://api.github.com/repos/owner/repo/pulls/42/files"
        respx.get(url__startswith=url).mock(
            return_value=httpx.Response(
                200,
                json=[{"filename": "a.py", "status": "modified", "patch": "+x"}],
                headers={"link": f'<{url}?page=2>; rel="next"'},
            ),
        )
        adapter = _github()
        try:
            with pytest.raises(VCSError, match="pages"):
                await adapter.get_diff()
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_link_to_another_host_is_refused(self) -> None:
        """The Authorization header would follow the token off-site."""
        url = "https://api.github.com/repos/owner/repo/pulls/42/files"
        respx.get(url).mock(
            return_value=httpx.Response(
                200,
                json=[{"filename": "a.py", "status": "modified", "patch": "+x"}],
                headers={
                    "link": '<https://evil.example.com/steal>; rel="next"',
                },
            ),
        )
        adapter = _github()
        try:
            with pytest.raises(VCSError, match="host"):
                await adapter.get_diff()
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_gitlab_pagination_is_bounded(self) -> None:
        respx.get("https://gitlab.com/api/v4/user").mock(
            return_value=httpx.Response(200, json={"username": "bot"}),
        )
        respx.get(
            "https://gitlab.com/api/v4/projects/owner%2Frepo"
            "/merge_requests/99/notes",
        ).mock(
            return_value=httpx.Response(
                200,
                json=[{"id": 1, "author": {"username": "x"}, "body": ""}],
                headers={"x-next-page": "2"},
            ),
        )
        adapter = _gitlab()
        try:
            with pytest.raises(VCSError, match="pages"):
                await adapter.find_bot_comment()
        finally:
            await adapter.close()
