"""Tests for GitLab adapter (story-3-7)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from prbot.auth.token import TokenResult
from prbot.vcs.gitlab import GitLabAdapter, _encode_project_path


def _make_adapter(
    base_url: str = "https://gitlab.com",
) -> GitLabAdapter:
    token = TokenResult(value="glpat-testtoken", source="test")
    return GitLabAdapter(
        token=token, repo="owner/repo", pr_number=99,
        base_url=base_url,
    )


_MR_URL = "https://gitlab.com/api/v4/projects/owner%2Frepo/merge_requests/99"


class TestGitLabGetPrMetadata:
    """Test MR metadata fetching and normalization."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_open_mr(
        self, gitlab_mr_response: dict[str, Any],
    ) -> None:
        respx.get(_MR_URL).mock(
            return_value=httpx.Response(200, json=gitlab_mr_response),
        )
        adapter = _make_adapter()
        try:
            meta = await adapter.get_pr_metadata()
            assert meta.state == "open"
            assert meta.title == "Add feature Y"
            assert meta.author == "gitlab_user"
            assert meta.number == 99
            assert meta.is_fork is False
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_merged_mr(
        self, gitlab_mr_response: dict[str, Any],
    ) -> None:
        resp = dict(gitlab_mr_response)
        resp["state"] = "merged"
        respx.get(_MR_URL).mock(
            return_value=httpx.Response(200, json=resp),
        )
        adapter = _make_adapter()
        try:
            meta = await adapter.get_pr_metadata()
            assert meta.state == "merged"
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_fork_mr(
        self, gitlab_mr_fork_response: dict[str, Any],
    ) -> None:
        respx.get(_MR_URL).mock(
            return_value=httpx.Response(
                200, json=gitlab_mr_fork_response,
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
    async def test_null_description(
        self, gitlab_mr_response: dict[str, Any],
    ) -> None:
        resp = dict(gitlab_mr_response)
        resp["description"] = None
        resp["title"] = None
        respx.get(_MR_URL).mock(
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
    async def test_locked_state(
        self, gitlab_mr_response: dict[str, Any],
    ) -> None:
        resp = dict(gitlab_mr_response)
        resp["state"] = "locked"
        respx.get(_MR_URL).mock(
            return_value=httpx.Response(200, json=resp),
        )
        adapter = _make_adapter()
        try:
            meta = await adapter.get_pr_metadata()
            assert meta.state == "closed"
        finally:
            await adapter.close()


class TestGitLabGetDiff:
    """Test diff fetching."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_simple_diff(
        self, gitlab_mr_response: dict[str, Any],
    ) -> None:
        changes_resp = dict(gitlab_mr_response)
        changes_resp["changes"] = [
            {
                "new_path": "src/main.py",
                "old_path": "src/main.py",
                "new_file": False,
                "deleted_file": False,
                "renamed_file": False,
                "diff": "@@ -1,3 +1,5 @@\n+import os\n import sys\n",
            },
        ]
        changes_resp["overflow"] = False
        respx.get(f"{_MR_URL}/changes").mock(
            return_value=httpx.Response(200, json=changes_resp),
        )
        adapter = _make_adapter()
        try:
            diff = await adapter.get_diff()
            assert len(diff.files) == 1
            assert diff.files[0].path == "src/main.py"
            assert diff.files[0].status == "modified"
            assert diff.truncated is False
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_overflow_detected(
        self, gitlab_mr_response: dict[str, Any],
    ) -> None:
        changes_resp = dict(gitlab_mr_response)
        changes_resp["changes"] = []
        changes_resp["overflow"] = True
        respx.get(f"{_MR_URL}/changes").mock(
            return_value=httpx.Response(200, json=changes_resp),
        )
        adapter = _make_adapter()
        try:
            diff = await adapter.get_diff()
            assert diff.truncated is True
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_rate_limited(self) -> None:
        from prbot.exceptions import VCSRateLimitError

        respx.get(f"{_MR_URL}/changes").mock(
            return_value=httpx.Response(429),
        )
        adapter = _make_adapter()
        try:
            with pytest.raises(VCSRateLimitError, match="rate_limited"):
                await adapter.get_diff()
        finally:
            await adapter.close()


class TestGitLabComments:
    """Test comment operations."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_post_comment(self) -> None:
        respx.post(f"{_MR_URL}/notes").mock(
            return_value=httpx.Response(201, json={"id": 456}),
        )
        adapter = _make_adapter()
        try:
            cid = await adapter.post_comment("Review")
            assert cid == 456
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_find_bot_comment(self) -> None:
        respx.get("https://gitlab.com/api/v4/user").mock(
            return_value=httpx.Response(
                200, json={"username": "prbot"},
            ),
        )
        notes = [
            {
                "id": 10,
                "author": {"username": "prbot"},
                "body": "Review\n<!-- prbot:state:{} -->",
            },
        ]
        respx.get(f"{_MR_URL}/notes").mock(
            return_value=httpx.Response(200, json=notes),
        )
        adapter = _make_adapter()
        try:
            result = await adapter.find_bot_comment()
            assert result is not None
            assert result[0] == 10
        finally:
            await adapter.close()


class TestProjectPathEncoding:
    """Test URL encoding of project paths."""

    def test_encodes_slash(self) -> None:
        assert _encode_project_path("owner/repo") == "owner%2Frepo"

    def test_encodes_special_chars(self) -> None:
        result = _encode_project_path("group/sub-group/project")
        assert "%2F" in result
        assert "/" not in result
