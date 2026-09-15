"""Tests for reading, replying to and resolving review threads (C8)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from prbot.auth.token import TokenResult
from prbot.vcs.github import GitHubAdapter
from prbot.vcs.gitlab import GitLabAdapter
from prbot.vcs.models import ReviewThread

_GQL = "https://api.github.com/graphql"
_GL = "https://gitlab.com/api/v4/projects/owner%2Frepo/merge_requests/99"


def _github(base_url: str = "https://api.github.com") -> GitHubAdapter:
    return GitHubAdapter(
        token=TokenResult(value="ghp_t", source="test"),
        repo="owner/repo", pr_number=42, base_url=base_url,
    )


def _gitlab() -> GitLabAdapter:
    return GitLabAdapter(
        token=TokenResult(value="glpat-t", source="test"),
        repo="owner/repo", pr_number=99,
    )


def _gql_threads(*nodes: dict) -> dict:
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": list(nodes),
                    },
                },
            },
        },
    }


def _node(tid: str, body: str, resolved: bool = False, db_id: int = 1) -> dict:
    return {
        "id": tid,
        "isResolved": resolved,
        "isOutdated": False,
        "path": "src/app.py",
        "line": 12,
        "comments": {"nodes": [{"databaseId": db_id, "body": body}]},
    }


class TestGitHubListsThreads:
    @respx.mock
    @pytest.mark.asyncio
    async def test_threads_come_back_with_resolution_state(self) -> None:
        respx.post(_GQL).mock(
            return_value=httpx.Response(
                200,
                json=_gql_threads(
                    _node("T_kw1", "finding one", resolved=False, db_id=11),
                    _node("T_kw2", "finding two", resolved=True, db_id=22),
                ),
            ),
        )
        adapter = _github()
        try:
            threads = await adapter.list_review_threads()
        finally:
            await adapter.close()

        assert len(threads) == 2
        assert threads[0].id == "T_kw1"
        assert threads[0].comment_id == 11
        assert threads[0].resolved is False
        assert threads[1].resolved is True

    @respx.mock
    @pytest.mark.asyncio
    async def test_pagination_is_followed(self) -> None:
        page_one = _gql_threads(_node("T_a", "one", db_id=1))
        page_one["data"]["repository"]["pullRequest"]["reviewThreads"][
            "pageInfo"
        ] = {"hasNextPage": True, "endCursor": "cur"}
        respx.post(_GQL).mock(
            side_effect=[
                httpx.Response(200, json=page_one),
                httpx.Response(200, json=_gql_threads(_node("T_b", "two", db_id=2))),
            ],
        )
        adapter = _github()
        try:
            threads = await adapter.list_review_threads()
        finally:
            await adapter.close()
        assert [t.id for t in threads] == ["T_a", "T_b"]

    @respx.mock
    @pytest.mark.asyncio
    async def test_graphql_errors_fall_back_to_rest(self) -> None:
        """A token without GraphQL access must not break the review."""
        respx.post(_GQL).mock(return_value=httpx.Response(403))
        respx.get(
            "https://api.github.com/repos/owner/repo/pulls/42/comments",
        ).mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "id": 7, "body": "finding", "path": "src/app.py",
                        "line": 12, "in_reply_to_id": None,
                    },
                ],
            ),
        )
        adapter = _github()
        try:
            threads = await adapter.list_review_threads()
        finally:
            await adapter.close()

        assert len(threads) == 1
        assert threads[0].comment_id == 7
        # Resolution state is not in REST, so it is reported as unknown
        assert threads[0].resolved is False

    @respx.mock
    @pytest.mark.asyncio
    async def test_replies_are_not_treated_as_threads(self) -> None:
        respx.post(_GQL).mock(return_value=httpx.Response(500))
        respx.get(
            "https://api.github.com/repos/owner/repo/pulls/42/comments",
        ).mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"id": 7, "body": "root", "path": "a.py", "line": 1,
                     "in_reply_to_id": None},
                    {"id": 8, "body": "reply", "path": "a.py", "line": 1,
                     "in_reply_to_id": 7},
                ],
            ),
        )
        adapter = _github()
        try:
            threads = await adapter.list_review_threads()
        finally:
            await adapter.close()
        assert [t.comment_id for t in threads] == [7]

    @respx.mock
    @pytest.mark.asyncio
    async def test_enterprise_graphql_path(self) -> None:
        route = respx.post("https://ghe.example.com/api/graphql").mock(
            return_value=httpx.Response(200, json=_gql_threads()),
        )
        adapter = _github(base_url="https://ghe.example.com/api/v3")
        try:
            await adapter.list_review_threads()
        finally:
            await adapter.close()
        assert route.call_count == 1


class TestGitHubRepliesAndResolves:
    @respx.mock
    @pytest.mark.asyncio
    async def test_a_reply_goes_into_the_thread(self) -> None:
        route = respx.post(
            "https://api.github.com/repos/owner/repo/pulls/42"
            "/comments/11/replies",
        ).mock(return_value=httpx.Response(201, json={"id": 99}))
        adapter = _github()
        thread = ReviewThread(id="T_kw1", comment_id=11, body="b")
        try:
            await adapter.reply_to_thread(thread, "Fixed in abc1234.")
        finally:
            await adapter.close()
        assert json.loads(route.calls[0].request.content)["body"] == (
            "Fixed in abc1234."
        )

    @respx.mock
    @pytest.mark.asyncio
    async def test_resolving_uses_the_thread_node_id(self) -> None:
        route = respx.post(_GQL).mock(
            return_value=httpx.Response(
                200,
                json={"data": {"resolveReviewThread": {"thread": {"id": "T_kw1"}}}},
            ),
        )
        adapter = _github()
        try:
            assert await adapter.resolve_thread(
                ReviewThread(id="T_kw1", comment_id=11, body="b"),
            )
        finally:
            await adapter.close()
        payload = json.loads(route.calls[0].request.content)
        assert "resolveReviewThread" in payload["query"]
        assert payload["variables"]["threadId"] == "T_kw1"

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_failed_resolve_is_reported_not_raised(self) -> None:
        respx.post(_GQL).mock(return_value=httpx.Response(403))
        adapter = _github()
        try:
            assert not await adapter.resolve_thread(
                ReviewThread(id="T_kw1", comment_id=11, body="b"),
            )
        finally:
            await adapter.close()


class TestGitLabThreads:
    @respx.mock
    @pytest.mark.asyncio
    async def test_discussions_become_threads(self) -> None:
        respx.get(f"{_GL}/discussions").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "id": "d1",
                        "notes": [
                            {
                                "id": 5, "body": "finding", "resolved": False,
                                "position": {"new_path": "src/app.py",
                                             "new_line": 12},
                            },
                        ],
                    },
                    {
                        "id": "d2",
                        "notes": [
                            {"id": 6, "body": "done", "resolved": True,
                             "position": {"new_path": "a.py", "new_line": 1}},
                        ],
                    },
                ],
            ),
        )
        adapter = _gitlab()
        try:
            threads = await adapter.list_review_threads()
        finally:
            await adapter.close()

        assert [t.id for t in threads] == ["d1", "d2"]
        assert threads[0].resolved is False
        assert threads[1].resolved is True
        assert threads[0].path == "src/app.py"

    @respx.mock
    @pytest.mark.asyncio
    async def test_unpositioned_discussions_are_skipped(self) -> None:
        """A plain MR note is not a review thread."""
        respx.get(f"{_GL}/discussions").mock(
            return_value=httpx.Response(
                200,
                json=[{"id": "d1", "notes": [{"id": 5, "body": "hi"}]}],
            ),
        )
        adapter = _gitlab()
        try:
            assert await adapter.list_review_threads() == []
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_reply_goes_into_the_discussion(self) -> None:
        route = respx.post(f"{_GL}/discussions/d1/notes").mock(
            return_value=httpx.Response(201, json={"id": 9}),
        )
        adapter = _gitlab()
        try:
            await adapter.reply_to_thread(
                ReviewThread(id="d1", comment_id=5, body="b"), "Fixed.",
            )
        finally:
            await adapter.close()
        assert json.loads(route.calls[0].request.content)["body"] == "Fixed."

    @respx.mock
    @pytest.mark.asyncio
    async def test_resolving_marks_the_discussion_resolved(self) -> None:
        route = respx.put(f"{_GL}/discussions/d1").mock(
            return_value=httpx.Response(200, json={"id": "d1"}),
        )
        adapter = _gitlab()
        try:
            assert await adapter.resolve_thread(
                ReviewThread(id="d1", comment_id=5, body="b"),
            )
        finally:
            await adapter.close()
        assert json.loads(route.calls[0].request.content)["resolved"] is True

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_failed_resolve_is_reported_not_raised(self) -> None:
        respx.put(f"{_GL}/discussions/d1").mock(return_value=httpx.Response(403))
        adapter = _gitlab()
        try:
            assert not await adapter.resolve_thread(
                ReviewThread(id="d1", comment_id=5, body="b"),
            )
        finally:
            await adapter.close()
