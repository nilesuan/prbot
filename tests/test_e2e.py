"""FakeVCSAdapter E2E tests (story-4-6).

Verifies the FakeVCSAdapter correctly implements VCSAdapter Protocol
with call recording and comment tracking.
"""

from __future__ import annotations

import pytest

from prbot.vcs.protocol import VCSAdapter
from tests.conftest import FakeVCSAdapter


class TestFakeVCSAdapter:
    """Verify FakeVCSAdapter satisfies VCSAdapter Protocol."""

    def test_implements_protocol(self) -> None:
        adapter = FakeVCSAdapter()
        assert isinstance(adapter, VCSAdapter)

    @pytest.mark.asyncio
    async def test_get_pr_metadata_records_call(
        self, fake_vcs: FakeVCSAdapter,
    ) -> None:
        metadata = await fake_vcs.get_pr_metadata()
        assert metadata.number == 42
        assert metadata.state == "open"
        assert "get_pr_metadata" in fake_vcs.calls

    @pytest.mark.asyncio
    async def test_get_diff_records_call(
        self, fake_vcs: FakeVCSAdapter,
    ) -> None:
        diff = await fake_vcs.get_diff()
        assert len(diff.files) == 1
        assert diff.files[0].path == "src/example.py"
        assert "get_diff" in fake_vcs.calls

    @pytest.mark.asyncio
    async def test_get_authenticated_user(
        self, fake_vcs: FakeVCSAdapter,
    ) -> None:
        user = await fake_vcs.get_authenticated_user()
        assert user == "prbot[bot]"
        assert "get_authenticated_user" in fake_vcs.calls

    @pytest.mark.asyncio
    async def test_find_bot_comment_none(
        self, fake_vcs: FakeVCSAdapter,
    ) -> None:
        result = await fake_vcs.find_bot_comment()
        assert result is None
        assert "find_bot_comment" in fake_vcs.calls

    @pytest.mark.asyncio
    async def test_find_bot_comment_configured(self) -> None:
        adapter = FakeVCSAdapter(bot_comment=(99, "existing comment"))
        result = await adapter.find_bot_comment()
        assert result == (99, "existing comment")

    @pytest.mark.asyncio
    async def test_post_comment_tracking(
        self, fake_vcs: FakeVCSAdapter,
    ) -> None:
        comment_id = await fake_vcs.post_comment("Hello world")
        assert comment_id == 100
        assert fake_vcs.posted_comments == ["Hello world"]
        assert "post_comment" in fake_vcs.calls

    @pytest.mark.asyncio
    async def test_update_comment_tracking(
        self, fake_vcs: FakeVCSAdapter,
    ) -> None:
        await fake_vcs.update_comment(42, "Updated body")
        assert fake_vcs.updated_comments == [(42, "Updated body")]
        assert "update_comment" in fake_vcs.calls

    @pytest.mark.asyncio
    async def test_post_or_update_creates_new(
        self, fake_vcs: FakeVCSAdapter,
    ) -> None:
        comment_id = await fake_vcs.post_or_update_comment("New comment")
        assert comment_id == 100
        assert "post_or_update_comment" in fake_vcs.calls
        assert "post_comment" in fake_vcs.calls

    @pytest.mark.asyncio
    async def test_post_or_update_updates_existing(self) -> None:
        adapter = FakeVCSAdapter(bot_comment=(55, "old"))
        comment_id = await adapter.post_or_update_comment("Updated")
        assert comment_id == 55
        assert adapter.updated_comments == [(55, "Updated")]

    @pytest.mark.asyncio
    async def test_close_records_call(
        self, fake_vcs: FakeVCSAdapter,
    ) -> None:
        await fake_vcs.close()
        assert "close" in fake_vcs.calls

    @pytest.mark.asyncio
    async def test_call_order_tracking(
        self, fake_vcs: FakeVCSAdapter,
    ) -> None:
        """Calls are recorded in invocation order."""
        await fake_vcs.get_pr_metadata()
        await fake_vcs.get_diff()
        await fake_vcs.get_authenticated_user()
        await fake_vcs.find_bot_comment()
        await fake_vcs.post_comment("body")
        await fake_vcs.close()
        assert fake_vcs.calls == [
            "get_pr_metadata",
            "get_diff",
            "get_authenticated_user",
            "find_bot_comment",
            "post_comment",
            "close",
        ]

    @pytest.mark.asyncio
    async def test_multiple_comments_tracked(
        self, fake_vcs: FakeVCSAdapter,
    ) -> None:
        id1 = await fake_vcs.post_comment("first")
        id2 = await fake_vcs.post_comment("second")
        assert id1 != id2
        assert fake_vcs.posted_comments == ["first", "second"]
