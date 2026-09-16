"""GitLab needs both sides of a line to place a comment on it.

Live evidence from infrastructure/infrastructure-core!195: the one finding
the review produced was anchored to an unchanged context line, and GitLab
refused the discussion with

    400 Bad request - Note {:line_code=>["can't be blank",
                                         "must be a valid line code"]}

because the position carried only new_line. A finding lands on a context
line whenever its line_end is not itself an added line, which is most of
them, so inline comments were failing silently on GitLab and falling back
to a warning in the job log.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from prbot.auth.token import TokenResult
from prbot.review.models import Finding
from prbot.review.scorer import ScoredFinding
from prbot.vcs.diff_parser import map_new_to_old
from prbot.vcs.gitlab import GitLabAdapter
from prbot.vcs.models import FileDiff, InlineComment, PRDiff

_HEAD = "a" * 40
_BASE = "b" * 40

# +2 is added; 1, 3 and 4 are context, so they exist on both sides.
_PATCH = "@@ -1,3 +1,4 @@\n import os\n+import sys\n ctx\n tail\n"


class TestMapNewToOld:
    def test_a_context_line_maps_to_its_old_side(self) -> None:
        assert map_new_to_old(_PATCH) == {1: 1, 3: 2, 4: 3}

    def test_an_added_line_has_no_old_side(self) -> None:
        assert 2 not in map_new_to_old(_PATCH)

    def test_a_deletion_shifts_the_old_side_only(self) -> None:
        patch = "@@ -1,3 +1,2 @@\n keep\n-gone\n after\n"
        assert map_new_to_old(patch) == {1: 1, 2: 3}

    def test_an_empty_patch_maps_nothing(self) -> None:
        assert map_new_to_old("") == {}

    def test_text_before_the_first_hunk_is_ignored(self) -> None:
        patch = "--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,1 @@\n same\n"
        assert map_new_to_old(patch) == {1: 1}

    def test_the_no_newline_marker_is_not_a_line(self) -> None:
        patch = "@@ -1,1 +1,1 @@\n same\n\\ No newline at end of file\n"
        assert map_new_to_old(patch) == {1: 1}


def _finding(**overrides: Any) -> ScoredFinding:
    base: dict[str, Any] = {
        "id": "general-1",
        "category": "general",
        "check_id": "Q-ERR-01",
        "title": "Bare except",
        "description": "Catches everything.",
        "file_path": "src/app.py",
        "line_start": 1,
        "line_end": 3,
        "severity": "medium",
        "confidence": 85,
        "suggestion": "Catch the specific exception.",
        "failure_scenario": "A KeyboardInterrupt is caught and the run hangs.",
    }
    base.update(overrides)
    return ScoredFinding(
        finding=Finding(**base), band="reported", deduction=1.0,
    )


def _diff() -> PRDiff:
    return PRDiff(
        files=[FileDiff(path="src/app.py", status="modified", patch=_PATCH)],
        head_sha=_HEAD,
        base_sha=_BASE,
    )


class TestInlineCommentsCarryBothSides:
    def test_a_comment_on_a_context_line_carries_its_old_side(self) -> None:
        from prbot.review.formatter import build_inline_comments

        c = build_inline_comments([_finding()], _diff())[0]
        assert c.line == 3
        assert c.old_line == 2

    def test_a_comment_on_an_added_line_has_no_old_side(self) -> None:
        from prbot.review.formatter import build_inline_comments

        c = build_inline_comments(
            [_finding(line_start=2, line_end=2)], _diff(),
        )[0]
        assert c.line == 2
        assert c.old_line is None


def _gitlab() -> GitLabAdapter:
    return GitLabAdapter(
        token=TokenResult(value="glpat-t", source="test"),
        repo="owner/repo",
        pr_number=99,
    )


class TestGitLabPositionsAreAcceptable:
    _URL = (
        "https://gitlab.com/api/v4/projects/owner%2Frepo"
        "/merge_requests/99/discussions"
    )

    @respx.mock
    @pytest.mark.asyncio
    async def test_a_context_line_sends_both_sides(self) -> None:
        route = respx.post(self._URL).mock(
            return_value=httpx.Response(201, json={"id": "abc"}),
        )
        adapter = _gitlab()
        try:
            await adapter.submit_review(
                "s", "COMMENT",
                [InlineComment(
                    path="src/app.py", line=3, body="b", old_line=2,
                )],
                head_sha=_HEAD, base_sha=_BASE,
            )
        finally:
            await adapter.close()

        pos = json.loads(route.calls[0].request.content)["position"]
        assert pos["new_line"] == 3
        assert pos["old_line"] == 2

    @respx.mock
    @pytest.mark.asyncio
    async def test_an_added_line_sends_no_old_line(self) -> None:
        """Sending old_line for an added line is just as invalid."""
        route = respx.post(self._URL).mock(
            return_value=httpx.Response(201, json={"id": "abc"}),
        )
        adapter = _gitlab()
        try:
            await adapter.submit_review(
                "s", "COMMENT",
                [InlineComment(path="src/app.py", line=2, body="b")],
                head_sha=_HEAD, base_sha=_BASE,
            )
        finally:
            await adapter.close()

        pos = json.loads(route.calls[0].request.content)["position"]
        assert pos["new_line"] == 2
        assert "old_line" not in pos
