"""Tests for skipping a re-review of an unchanged commit (C3).

ReviewStateRecord.from_html_comment was dead code and post_or_update_comment
fetched the previous comment body and threw it away. Every push, and every
re-run of the same push, paid for a full review of a commit that had already
been reviewed.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from prbot.cli import EXIT_PASS, run_pipeline
from prbot.config import PrBotConfig
from prbot.vcs.models import ReviewStateRecord
from tests.conftest import FakeVCSAdapter

_HEAD = "abcdef1234567890abcdef1234567890abcdef12"
_OTHER = "999999999999999999999999999999999999999f"
_REVIEW_ID = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"


def _config(**overrides: Any) -> PrBotConfig:
    defaults: dict[str, Any] = {
        "platform": "github",
        "repo": "owner/repo",
        "pr_number": 42,
        "general_model_id": "anthropic.claude-sonnet-4-6",
        "security_model_id": "anthropic.claude-sonnet-4-6",
    }
    defaults.update(overrides)
    return PrBotConfig(**defaults)


def _comment_for(head_sha: str, verdict: str = "COMMENT") -> str:
    record = ReviewStateRecord(
        review_id=_REVIEW_ID,
        head_sha=head_sha,
        score=88,
        verdict=verdict,
        findings_hash="a" * 64,
        timestamp="2026-09-15T00:00:00+00:00",
    )
    return f"## Previous review\n\n{record.to_html_comment()}"


def _response(findings: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "usage": {"inputTokens": 100, "outputTokens": 10},
        "output": {
            "message": {
                "content": [
                    {
                        "toolUse": {
                            "name": "report_findings",
                            "input": {"findings": findings},
                        },
                    },
                ],
            },
        },
    }


def _run(adapter: FakeVCSAdapter, config: PrBotConfig, calls: list[str]):
    def bedrock(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs["model_id"])
        return _response([])

    token = AsyncMock()
    token.mask_in_ci = lambda: None
    token.source = "env"
    token.redacted = "***"

    return (
        patch("prbot.auth.resolve_token", new_callable=AsyncMock),
        patch("prbot.auth.validate_aws_session_credentials"),
        patch("prbot.auth.validate_token_scopes", new_callable=AsyncMock),
        patch("prbot.vcs.create_vcs_adapter", return_value=adapter),
        patch("prbot.review.runner._invoke_bedrock", bedrock),
        token,
    )


async def _pipeline(
    adapter: FakeVCSAdapter, config: PrBotConfig,
) -> tuple[int, list[str]]:
    calls: list[str] = []
    resolve, creds, scopes, create, bedrock, token = _run(adapter, config, calls)
    with resolve as mock_resolve, creds, scopes, create, bedrock:
        mock_resolve.return_value = token
        exit_code = await run_pipeline(config)
    return exit_code, calls


class TestUnchangedCommitIsSkipped:
    @pytest.mark.asyncio
    async def test_no_bedrock_call_for_a_commit_already_reviewed(self) -> None:
        adapter = FakeVCSAdapter(bot_comment=(55, _comment_for(_HEAD)))
        exit_code, calls = await _pipeline(adapter, _config())
        assert calls == []
        assert exit_code == EXIT_PASS

    @pytest.mark.asyncio
    async def test_the_existing_comment_is_left_alone(self) -> None:
        adapter = FakeVCSAdapter(bot_comment=(55, _comment_for(_HEAD)))
        await _pipeline(adapter, _config())
        assert adapter.updated_comments == []
        assert adapter.posted_comments == []

    @pytest.mark.asyncio
    async def test_a_previous_blocking_verdict_still_exits_one(self) -> None:
        """Skipping must not turn a REQUEST_CHANGES into a pass."""
        from prbot.cli import EXIT_BLOCKERS

        adapter = FakeVCSAdapter(
            bot_comment=(55, _comment_for(_HEAD, "REQUEST_CHANGES")),
        )
        exit_code, calls = await _pipeline(adapter, _config())
        assert calls == []
        assert exit_code == EXIT_BLOCKERS


class TestChangedCommitIsReviewed:
    @pytest.mark.asyncio
    async def test_a_different_head_sha_triggers_a_review(self) -> None:
        adapter = FakeVCSAdapter(bot_comment=(55, _comment_for(_OTHER)))
        _, calls = await _pipeline(adapter, _config())
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_no_previous_comment_triggers_a_review(self) -> None:
        adapter = FakeVCSAdapter()
        _, calls = await _pipeline(adapter, _config())
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_an_unparseable_record_triggers_a_review(self) -> None:
        adapter = FakeVCSAdapter(
            bot_comment=(55, "## Review\n<!-- prbot:state:not json -->"),
        )
        _, calls = await _pipeline(adapter, _config())
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_a_comment_without_a_record_triggers_a_review(self) -> None:
        adapter = FakeVCSAdapter(bot_comment=(55, "## Review\nno marker"))
        _, calls = await _pipeline(adapter, _config())
        assert len(calls) == 2


class TestForcingAReview:
    @pytest.mark.asyncio
    async def test_force_overrides_the_skip(self) -> None:
        adapter = FakeVCSAdapter(bot_comment=(55, _comment_for(_HEAD)))
        _, calls = await _pipeline(adapter, _config(force_review=True))
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_the_comment_is_updated_when_forced(self) -> None:
        adapter = FakeVCSAdapter(bot_comment=(55, _comment_for(_HEAD)))
        await _pipeline(adapter, _config(force_review=True))
        assert len(adapter.updated_comments) == 1
        assert adapter.updated_comments[0][0] == 55


class TestCommentLookupHappensOnce:
    @pytest.mark.asyncio
    async def test_the_bot_comment_is_searched_for_once(self) -> None:
        """Searching twice paginated the whole comment list twice."""
        adapter = FakeVCSAdapter()
        await _pipeline(adapter, _config())
        assert adapter.calls.count("find_bot_comment") == 1
        assert adapter.calls.count("post_or_update_comment") == 0


class TestReviewHistoryIsPreserved:
    """prbot overwrites its own comment, so only its last verdict survives.

    13 of the 34 audited production comments had updated_at later than
    created_at, and the state marker always tracked updated_at. On MR 194
    prbot reviewed three commits and approved the first two at 100/100 with
    zero findings; GitLab preserved only the third. Reconstructing what the
    bot had said about the code before the fixes required the CI audit
    records, and any attempt to measure whether prbot is improving is
    defeated by the same thing: the record of what it said before a fix is
    deleted by the fix.
    """

    @staticmethod
    def _record(sha: str, score: int, verdict: str, **kw: object):
        from prbot.vcs.models import ReviewStateRecord

        return ReviewStateRecord(
            review_id="4b1e8a1e-0f9c-4b2e-8a3d-1c2f3e4a5b6c",
            head_sha=sha,
            score=score,
            verdict=verdict,
            findings_hash="0" * 64,
            timestamp="2026-09-16T02:37:01+00:00",
            **kw,  # type: ignore[arg-type]
        )

    def test_a_record_round_trips_its_history(self) -> None:
        from prbot.vcs.models import ReviewStateRecord

        first = self._record("a" * 40, 100, "APPROVE")
        second = self._record("b" * 40, 89, "REQUEST_CHANGES").superseding(
            first,
        )

        parsed = ReviewStateRecord.from_html_comment(second.to_html_comment())
        assert parsed is not None
        assert parsed.head_sha == "b" * 40
        assert len(parsed.history) == 1
        assert parsed.history[0]["head_sha"] == "a" * 40
        assert parsed.history[0]["score"] == 100
        assert parsed.history[0]["verdict"] == "APPROVE"

    def test_history_accumulates_in_order(self) -> None:
        r1 = self._record("a" * 40, 100, "APPROVE")
        r2 = self._record("b" * 40, 100, "APPROVE").superseding(r1)
        r3 = self._record("c" * 40, 89, "REQUEST_CHANGES").superseding(r2)

        assert [h["head_sha"][0] for h in r3.history] == ["a", "b"]

    def test_history_is_bounded(self) -> None:
        """The state record lives in a comment with a size limit."""
        from prbot.vcs.models import _HISTORY_LIMIT

        record = self._record("0" * 40, 100, "APPROVE")
        for i in range(_HISTORY_LIMIT + 5):
            record = self._record(
                f"{i:040x}", 100, "APPROVE",
            ).superseding(record)

        assert len(record.history) == _HISTORY_LIMIT

    def test_superseding_nothing_leaves_an_empty_history(self) -> None:
        assert self._record("a" * 40, 100, "APPROVE").superseding(None).history == ()

    def test_a_record_without_history_still_parses(self) -> None:
        """Comments written by earlier versions must keep working."""
        from prbot.vcs.models import ReviewStateRecord

        legacy = (
            '<!-- prbot:state:{"review_id":'
            '"4b1e8a1e-0f9c-4b2e-8a3d-1c2f3e4a5b6c",'
            f'"head_sha":"{"a" * 40}","score":100,"verdict":"APPROVE",'
            '"findings_hash":"' + "0" * 64 + '",'
            '"timestamp":"2026-09-16T02:37:01+00:00"} -->'
        )
        parsed = ReviewStateRecord.from_html_comment(legacy)
        assert parsed is not None
        assert parsed.history == ()
