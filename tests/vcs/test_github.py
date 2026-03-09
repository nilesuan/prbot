"""Tests for GitHub adapter (story-3-7)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from prbot.auth.token import TokenResult
from prbot.vcs.github import GitHubAdapter


def _make_adapter(base_url: str = "https://api.github.com") -> GitHubAdapter:
    token = TokenResult(value="ghp_testtoken", source="test")
    return GitHubAdapter(
        token=token, repo="owner/repo", pr_number=42, base_url=base_url,
    )


class TestGitHubGetPrMetadata:
    """Test PR metadata fetching and normalization."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_open_pr(
        self, github_pr_response: dict[str, Any],
    ) -> None:
        respx.get("https://api.github.com/repos/owner/repo/pulls/42").mock(
            return_value=httpx.Response(200, json=github_pr_response),
        )
        adapter = _make_adapter()
        try:
            meta = await adapter.get_pr_metadata()
            assert meta.state == "open"
            assert meta.title == "Add feature X"
            assert meta.author == "octocat"
            assert meta.number == 42
            assert meta.is_fork is False
            assert meta.is_draft is False
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_merged_pr(
        self, github_pr_merged_response: dict[str, Any],
    ) -> None:
        respx.get("https://api.github.com/repos/owner/repo/pulls/42").mock(
            return_value=httpx.Response(
                200, json=github_pr_merged_response,
            ),
        )
        adapter = _make_adapter()
        try:
            meta = await adapter.get_pr_metadata()
            assert meta.state == "merged"
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_fork_pr(
        self, github_pr_fork_response: dict[str, Any],
    ) -> None:
        respx.get("https://api.github.com/repos/owner/repo/pulls/42").mock(
            return_value=httpx.Response(
                200, json=github_pr_fork_response,
            ),
        )
        adapter = _make_adapter()
        try:
            meta = await adapter.get_pr_metadata()
            assert meta.is_fork is True
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_null_body(
        self, github_pr_response: dict[str, Any],
    ) -> None:
        resp = dict(github_pr_response)
        resp["body"] = None
        resp["title"] = None
        respx.get("https://api.github.com/repos/owner/repo/pulls/42").mock(
            return_value=httpx.Response(200, json=resp),
        )
        adapter = _make_adapter()
        try:
            meta = await adapter.get_pr_metadata()
            assert meta.body == ""
            assert meta.title == ""
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_404_raises(self) -> None:
        from prbot.exceptions import VCSNotFoundError

        respx.get("https://api.github.com/repos/owner/repo/pulls/42").mock(
            return_value=httpx.Response(404),
        )
        adapter = _make_adapter()
        try:
            with pytest.raises(VCSNotFoundError, match="404"):
                await adapter.get_pr_metadata()
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_missing_head_raises(self) -> None:
        from prbot.exceptions import VCSResponseError

        respx.get("https://api.github.com/repos/owner/repo/pulls/42").mock(
            return_value=httpx.Response(200, json={"state": "open"}),
        )
        adapter = _make_adapter()
        try:
            with pytest.raises(VCSResponseError, match=r"head\.sha"):
                await adapter.get_pr_metadata()
        finally:
            await adapter.close()


class TestGitHubGetDiff:
    """Test diff fetching with pagination."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_simple_diff(
        self,
        github_pr_response: dict[str, Any],
        github_diff_response: list[dict[str, Any]],
    ) -> None:
        respx.get(
            "https://api.github.com/repos/owner/repo/pulls/42/files",
        ).mock(
            return_value=httpx.Response(200, json=github_diff_response),
        )
        respx.get(
            "https://api.github.com/repos/owner/repo/pulls/42",
        ).mock(
            return_value=httpx.Response(200, json=github_pr_response),
        )
        adapter = _make_adapter()
        try:
            diff = await adapter.get_diff()
            assert len(diff.files) == 2
            assert diff.files[0].path == "src/main.py"
            assert diff.files[0].status == "modified"
            assert diff.files[1].path == "README.md"
            assert diff.files[1].status == "added"
            assert diff.truncated is False
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_pagination(
        self, github_pr_response: dict[str, Any],
    ) -> None:
        page1 = [{"filename": f"file{i}.py", "status": "modified"} for i in range(100)]
        page2 = [
            {"filename": f"file{i}.py", "status": "modified"}
            for i in range(100, 150)
        ]

        route = respx.get(
            "https://api.github.com/repos/owner/repo/pulls/42/files",
        )
        route.side_effect = [
            httpx.Response(
                200, json=page1,
                headers={"link": '<https://api.github.com/next>; rel="next"'},
            ),
            httpx.Response(200, json=page2),
        ]
        respx.get("https://api.github.com/next").mock(
            return_value=httpx.Response(200, json=page2),
        )
        respx.get(
            "https://api.github.com/repos/owner/repo/pulls/42",
        ).mock(
            return_value=httpx.Response(200, json=github_pr_response),
        )
        adapter = _make_adapter()
        try:
            diff = await adapter.get_diff()
            assert len(diff.files) == 150
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_rate_limited(self) -> None:
        from prbot.exceptions import VCSRateLimitError

        respx.get(
            "https://api.github.com/repos/owner/repo/pulls/42/files",
        ).mock(return_value=httpx.Response(429))
        adapter = _make_adapter()
        try:
            with pytest.raises(VCSRateLimitError, match="rate_limited"):
                await adapter.get_diff()
        finally:
            await adapter.close()


class TestGitHubComments:
    """Test comment operations with bot detection."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_post_comment(self) -> None:
        respx.post(
            "https://api.github.com/repos/owner/repo/issues/42/comments",
        ).mock(return_value=httpx.Response(201, json={"id": 123}))
        adapter = _make_adapter()
        try:
            cid = await adapter.post_comment("Review body")
            assert cid == 123
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_find_bot_comment(self) -> None:
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "prbot[bot]"}),
        )
        comments = [
            {"id": 1, "user": {"login": "human"}, "body": "LGTM"},
            {
                "id": 2,
                "user": {"login": "prbot[bot]"},
                "body": "Review\n<!-- prbot:state:{} -->",
            },
        ]
        respx.get(
            "https://api.github.com/repos/owner/repo/issues/42/comments",
        ).mock(return_value=httpx.Response(200, json=comments))
        adapter = _make_adapter()
        try:
            result = await adapter.find_bot_comment()
            assert result is not None
            assert result[0] == 2
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_find_bot_comment_wrong_author(self) -> None:
        """Comment with marker but wrong author should be ignored."""
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "prbot[bot]"}),
        )
        comments = [
            {
                "id": 1,
                "user": {"login": "impersonator"},
                "body": "<!-- prbot:state:{} -->",
            },
        ]
        respx.get(
            "https://api.github.com/repos/owner/repo/issues/42/comments",
        ).mock(return_value=httpx.Response(200, json=comments))
        adapter = _make_adapter()
        try:
            result = await adapter.find_bot_comment()
            assert result is None
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_post_or_update_creates_new(self) -> None:
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "bot"}),
        )
        respx.get(
            "https://api.github.com/repos/owner/repo/issues/42/comments",
        ).mock(return_value=httpx.Response(200, json=[]))
        respx.post(
            "https://api.github.com/repos/owner/repo/issues/42/comments",
        ).mock(return_value=httpx.Response(201, json={"id": 99}))
        adapter = _make_adapter()
        try:
            cid = await adapter.post_or_update_comment("New review")
            assert cid == 99
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_post_or_update_updates_existing(self) -> None:
        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "bot"}),
        )
        comments = [
            {
                "id": 50,
                "user": {"login": "bot"},
                "body": "Old\n<!-- prbot:state:{} -->",
            },
        ]
        respx.get(
            "https://api.github.com/repos/owner/repo/issues/42/comments",
        ).mock(return_value=httpx.Response(200, json=comments))
        respx.patch(
            "https://api.github.com/repos/owner/repo/issues/comments/50",
        ).mock(return_value=httpx.Response(200, json={"id": 50}))
        adapter = _make_adapter()
        try:
            cid = await adapter.post_or_update_comment("Updated")
            assert cid == 50
        finally:
            await adapter.close()
