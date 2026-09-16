"""Tests for inline review comments and platform review submission (C1, C2).

diff_parser computed a diff_position for every changed line and was used by
nothing but its own tests, while a REQUEST_CHANGES verdict only changed the
process exit code and left the pull request untouched.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from prbot.auth.token import TokenResult
from prbot.exceptions import VCSResponseError
from prbot.vcs.github import GitHubAdapter
from prbot.vcs.gitlab import GitLabAdapter
from prbot.vcs.models import InlineComment

_HEAD = "abcdef1234567890abcdef1234567890abcdef12"
_BASE = "1234567890abcdef1234567890abcdef12345678"


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


def _comments() -> list[InlineComment]:
    return [
        InlineComment(path="src/app.py", line=12, body="Bare except here."),
        InlineComment(
            path="src/util.py", line=40, body="Unbounded loop.", start_line=38,
        ),
    ]


class TestGitHubReviewSubmission:
    @respx.mock
    @pytest.mark.asyncio
    async def test_inline_comments_are_sent_with_the_review(self) -> None:
        route = respx.post(
            "https://api.github.com/repos/owner/repo/pulls/42/reviews",
        ).mock(return_value=httpx.Response(200, json={"id": 7}))
        adapter = _github()
        try:
            await adapter.submit_review(
                "summary", "COMMENT", _comments(), head_sha=_HEAD,
                base_sha=_BASE,
            )
        finally:
            await adapter.close()

        payload = json.loads(route.calls[0].request.content)
        assert payload["event"] == "COMMENT"
        assert payload["body"] == "summary"
        assert len(payload["comments"]) == 2
        first = payload["comments"][0]
        assert first["path"] == "src/app.py"
        assert first["line"] == 12
        assert first["side"] == "RIGHT"

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_multi_line_comment_carries_its_start(self) -> None:
        route = respx.post(
            "https://api.github.com/repos/owner/repo/pulls/42/reviews",
        ).mock(return_value=httpx.Response(200, json={"id": 7}))
        adapter = _github()
        try:
            await adapter.submit_review(
                "s", "COMMENT", _comments(), head_sha=_HEAD, base_sha=_BASE,
            )
        finally:
            await adapter.close()

        second = json.loads(route.calls[0].request.content)["comments"][1]
        assert second["start_line"] == 38
        assert second["line"] == 40
        assert second["start_side"] == "RIGHT"

    @respx.mock
    @pytest.mark.asyncio
    async def test_request_changes_reaches_the_pull_request(self) -> None:
        route = respx.post(
            "https://api.github.com/repos/owner/repo/pulls/42/reviews",
        ).mock(return_value=httpx.Response(200, json={"id": 7}))
        adapter = _github()
        try:
            await adapter.submit_review(
                "s", "REQUEST_CHANGES", [], head_sha=_HEAD, base_sha=_BASE,
            )
        finally:
            await adapter.close()

        assert json.loads(route.calls[0].request.content)["event"] == (
            "REQUEST_CHANGES"
        )

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_refused_event_still_delivers_the_review(self) -> None:
        """GitHub does not permit the Actions token to approve, and nobody
        may approve their own pull request. The verdict still reaches the
        exit code, so losing the event beats losing the review."""
        route = respx.post(
            "https://api.github.com/repos/owner/repo/pulls/42/reviews",
        ).mock(side_effect=[
            httpx.Response(422, json={
                "message": "GitHub Actions is not permitted to approve "
                           "pull requests.",
            }),
            httpx.Response(200, json={"id": 7}),
        ])
        adapter = _github()
        try:
            await adapter.submit_review(
                "s", "APPROVE", [], head_sha=_HEAD, base_sha=_BASE,
            )
        finally:
            await adapter.close()

        assert route.call_count == 2
        assert json.loads(route.calls[1].request.content)["event"] == "COMMENT"

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_rejected_position_keeps_the_event(self) -> None:
        """A stale line costs the anchors, not the verdict."""
        route = respx.post(
            "https://api.github.com/repos/owner/repo/pulls/42/reviews",
        ).mock(side_effect=[
            httpx.Response(422, json={"message": "line must be part of diff"}),
            httpx.Response(200, json={"id": 7}),
        ])
        adapter = _github()
        try:
            await adapter.submit_review(
                "s", "REQUEST_CHANGES", _comments(),
                head_sha=_HEAD, base_sha=_BASE,
            )
        finally:
            await adapter.close()

        second = json.loads(route.calls[1].request.content)
        assert second["event"] == "REQUEST_CHANGES"
        assert second["comments"] == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_review_that_cannot_be_delivered_still_raises(self) -> None:
        respx.post(
            "https://api.github.com/repos/owner/repo/pulls/42/reviews",
        ).mock(return_value=httpx.Response(422, json={"message": "no"}))
        adapter = _github()
        try:
            with pytest.raises(VCSResponseError):
                await adapter.submit_review(
                    "s", "APPROVE", _comments(),
                    head_sha=_HEAD, base_sha=_BASE,
                )
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_the_head_sha_is_pinned(self) -> None:
        """Without it the review can land on a commit that has moved on."""
        route = respx.post(
            "https://api.github.com/repos/owner/repo/pulls/42/reviews",
        ).mock(return_value=httpx.Response(200, json={"id": 7}))
        adapter = _github()
        try:
            await adapter.submit_review(
                "s", "COMMENT", [], head_sha=_HEAD, base_sha=_BASE,
            )
        finally:
            await adapter.close()

        assert json.loads(route.calls[0].request.content)["commit_id"] == _HEAD


class TestGitLabReviewSubmission:
    @respx.mock
    @pytest.mark.asyncio
    async def test_each_comment_becomes_a_positioned_discussion(self) -> None:
        discussions = respx.post(
            "https://gitlab.com/api/v4/projects/owner%2Frepo"
            "/merge_requests/99/discussions",
        ).mock(return_value=httpx.Response(201, json={"id": "abc"}))
        notes = respx.post(
            "https://gitlab.com/api/v4/projects/owner%2Frepo"
            "/merge_requests/99/notes",
        ).mock(return_value=httpx.Response(201, json={"id": 3}))
        adapter = _gitlab()
        try:
            await adapter.submit_review(
                "summary", "COMMENT", _comments(),
                head_sha=_HEAD, base_sha=_BASE,
            )
        finally:
            await adapter.close()

        assert discussions.call_count == 2
        assert notes.call_count == 1
        payload = json.loads(discussions.calls[0].request.content)
        assert payload["position"]["new_path"] == "src/app.py"
        assert payload["position"]["new_line"] == 12
        assert payload["position"]["head_sha"] == _HEAD
        assert payload["position"]["base_sha"] == _BASE
        assert payload["position"]["position_type"] == "text"

    @respx.mock
    @pytest.mark.asyncio
    async def test_request_changes_withdraws_approval(self) -> None:
        respx.post(
            "https://gitlab.com/api/v4/projects/owner%2Frepo"
            "/merge_requests/99/notes",
        ).mock(return_value=httpx.Response(201, json={"id": 3}))
        unapprove = respx.post(
            "https://gitlab.com/api/v4/projects/owner%2Frepo"
            "/merge_requests/99/unapprove",
        ).mock(return_value=httpx.Response(201, json={}))
        adapter = _gitlab()
        try:
            await adapter.submit_review(
                "s", "REQUEST_CHANGES", [], head_sha=_HEAD, base_sha=_BASE,
            )
        finally:
            await adapter.close()

        assert unapprove.call_count == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_approve_approves(self) -> None:
        respx.post(
            "https://gitlab.com/api/v4/projects/owner%2Frepo"
            "/merge_requests/99/notes",
        ).mock(return_value=httpx.Response(201, json={"id": 3}))
        approve = respx.post(
            "https://gitlab.com/api/v4/projects/owner%2Frepo"
            "/merge_requests/99/approve",
        ).mock(return_value=httpx.Response(201, json={}))
        adapter = _gitlab()
        try:
            await adapter.submit_review(
                "s", "APPROVE", [], head_sha=_HEAD, base_sha=_BASE,
            )
        finally:
            await adapter.close()

        assert approve.call_count == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_rejected_position_does_not_lose_the_review(self) -> None:
        """A stale line must not take the whole summary down with it."""
        respx.post(
            "https://gitlab.com/api/v4/projects/owner%2Frepo"
            "/merge_requests/99/discussions",
        ).mock(return_value=httpx.Response(400, json={"message": "bad line"}))
        notes = respx.post(
            "https://gitlab.com/api/v4/projects/owner%2Frepo"
            "/merge_requests/99/notes",
        ).mock(return_value=httpx.Response(201, json={"id": 3}))
        adapter = _gitlab()
        try:
            await adapter.submit_review(
                "summary", "COMMENT", _comments(),
                head_sha=_HEAD, base_sha=_BASE,
            )
        finally:
            await adapter.close()

        assert notes.call_count == 1


class TestBuildingInlineComments:
    """Only findings that land on a reviewable line become inline."""

    @staticmethod
    def _finding(**overrides: Any):
        from prbot.review.models import Finding

        base: dict[str, Any] = {
            "id": "general-1",
            "category": "general",
            "check_id": "Q-ERR-01",
            "title": "Bare except",
            "description": "Catches everything.",
            "file_path": "src/app.py",
            "line_start": 2,
            "line_end": 2,
            "severity": "medium",
            "confidence": 85,
            "suggestion": "Catch the specific exception.",
        }
        base.update(overrides)
        return Finding(**base)

    @staticmethod
    def _diff():
        from prbot.vcs.models import FileDiff, PRDiff

        return PRDiff(
            files=[
                FileDiff(
                    path="src/app.py",
                    status="modified",
                    patch="@@ -1,3 +1,4 @@\n import os\n+import sys\n ctx\n",
                ),
            ],
            head_sha=_HEAD,
            base_sha=_BASE,
        )

    def test_a_finding_on_an_added_line_becomes_inline(self) -> None:
        from prbot.review.formatter import build_inline_comments
        from prbot.review.scorer import ScoredFinding

        scored = [
            ScoredFinding(finding=self._finding(), band="reported", deduction=1.0),
        ]
        comments = build_inline_comments(scored, self._diff())
        assert len(comments) == 1
        assert comments[0].path == "src/app.py"
        assert comments[0].line == 2
        assert "Q-ERR-01" in comments[0].body
        assert "Catch the specific exception." in comments[0].body

    def test_a_finding_outside_the_diff_stays_in_the_summary(self) -> None:
        from prbot.review.formatter import build_inline_comments
        from prbot.review.scorer import ScoredFinding

        scored = [
            ScoredFinding(
                finding=self._finding(line_start=900, line_end=900),
                band="reported",
                deduction=1.0,
            ),
        ]
        assert build_inline_comments(scored, self._diff()) == []

    def test_a_finding_for_an_unknown_file_stays_in_the_summary(self) -> None:
        from prbot.review.formatter import build_inline_comments
        from prbot.review.scorer import ScoredFinding

        scored = [
            ScoredFinding(
                finding=self._finding(file_path="other.py"),
                band="reported",
                deduction=1.0,
            ),
        ]
        assert build_inline_comments(scored, self._diff()) == []

    def test_inline_bodies_are_sanitised(self) -> None:
        from prbot.review.formatter import build_inline_comments
        from prbot.review.scorer import ScoredFinding

        scored = [
            ScoredFinding(
                finding=self._finding(description="Ping @octocat <img src=x>"),
                band="reported",
                deduction=1.0,
            ),
        ]
        body = build_inline_comments(scored, self._diff())[0].body
        assert "`@octocat`" in body
        assert "<img" not in body


class TestFileContentIsBounded:
    """SEC-INPUT-04: get_file_content returned the whole body, unbounded.

    The file sizes are chosen by the contributor whose branch is under
    review, every non-removed file in the diff was fetched, and all of them
    were held for the run. build_context_excerpt only ever uses a window
    around the hunks, so the rest was retained for nothing.
    """

    @respx.mock
    @pytest.mark.asyncio
    async def test_an_oversized_file_is_skipped(self) -> None:
        respx.get(
            "https://api.github.com/repos/owner/repo/contents/big.py",
        ).mock(return_value=httpx.Response(200, text="x" * 3_000_000))
        adapter = _github()
        try:
            assert await adapter.get_file_content("big.py", _HEAD) is None
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_normal_file_is_returned(self) -> None:
        respx.get(
            "https://api.github.com/repos/owner/repo/contents/small.py",
        ).mock(return_value=httpx.Response(200, text="import os\n"))
        adapter = _github()
        try:
            assert await adapter.get_file_content("small.py", _HEAD) == (
                "import os\n"
            )
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_gitlab_is_bounded_too(self) -> None:
        respx.get(
            "https://gitlab.com/api/v4/projects/owner%2Frepo"
            "/repository/files/big.py/raw",
        ).mock(return_value=httpx.Response(200, text="x" * 3_000_000))
        adapter = _gitlab()
        try:
            assert await adapter.get_file_content("big.py", _HEAD) is None
        finally:
            await adapter.close()


class TestOversizedBodyIsNeverFullyBuffered:
    """SEC-INPUT-04: the bound ran after httpx had buffered everything.

    send_with_retry uses the non-streaming API, so response.content was
    already the whole body by the time the length was checked. The bound
    limited what was retained, not what was allocated.
    """

    @respx.mock
    @pytest.mark.asyncio
    async def test_the_request_asks_for_a_bounded_range(self) -> None:
        route = respx.get(
            "https://api.github.com/repos/owner/repo/contents/big.py",
        ).mock(return_value=httpx.Response(200, text="import os\n"))
        adapter = _github()
        try:
            await adapter.get_file_content("big.py", _HEAD)
        finally:
            await adapter.close()
        assert route.call_count == 1
        assert "range" in {
            k.lower() for k in route.calls[0].request.headers
        }

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_truncated_response_is_discarded(self) -> None:
        """206 means the server had more than the bound allowed."""
        respx.get(
            "https://api.github.com/repos/owner/repo/contents/big.py",
        ).mock(return_value=httpx.Response(206, text="x" * 1000))
        adapter = _github()
        try:
            assert await adapter.get_file_content("big.py", _HEAD) is None
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_server_ignoring_range_is_still_bounded(self) -> None:
        respx.get(
            "https://api.github.com/repos/owner/repo/contents/big.py",
        ).mock(return_value=httpx.Response(200, text="x" * 3_000_000))
        adapter = _github()
        try:
            assert await adapter.get_file_content("big.py", _HEAD) is None
        finally:
            await adapter.close()

    @respx.mock
    @pytest.mark.asyncio
    async def test_gitlab_asks_for_a_range_too(self) -> None:
        route = respx.get(
            "https://gitlab.com/api/v4/projects/owner%2Frepo"
            "/repository/files/f.py/raw",
        ).mock(return_value=httpx.Response(200, text="ok\n"))
        adapter = _gitlab()
        try:
            await adapter.get_file_content("f.py", _HEAD)
        finally:
            await adapter.close()
        assert "range" in {
            k.lower() for k in route.calls[0].request.headers
        }
