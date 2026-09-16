"""Tests for hallucination validation (story-6-7)."""

from __future__ import annotations

from dataclasses import replace

from prbot.review.models import Finding
from prbot.security.validation import (
    HALLUCINATION_PENALTY,
    _build_line_index,
    validate_findings_against_diff,
)
from prbot.vcs.models import FileDiff, PRDiff


def _make_diff(
    files: list[FileDiff] | None = None,
) -> PRDiff:
    return PRDiff(
        files=files or [
            FileDiff(
                path="src/app.py",
                status="modified",
                patch=(
                    "@@ -10,3 +10,4 @@\n"
                    " context\n"
                    "+new_line\n"
                    " more_context\n"
                ),
                additions=1,
                deletions=0,
            ),
        ],
    )


def _make_finding(
    *,
    file_path: str = "src/app.py",
    line_start: int = 11,
    line_end: int = 11,
    confidence: int = 80,
) -> Finding:
    return Finding(
        id="test-1",
        category="general",
        check_id="Q-ARCH-01",
        title="Test",
        description="Test desc",
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        severity="medium",
        confidence=confidence,
    )


class TestBuildLineIndex:
    """Tests for _build_line_index."""

    def test_extracts_added_lines(self) -> None:
        diff = _make_diff()
        index = _build_line_index(diff)
        assert "src/app.py" in index
        assert 11 in index["src/app.py"]  # +new_line at line 11

    def test_empty_patch(self) -> None:
        """None, not an empty set: no hunks means no basis to judge lines.

        An empty set used to be indistinguishable from "this file has hunks
        but nothing was added", and the caller silently skipped the check for
        both (B4).
        """
        diff = _make_diff([
            FileDiff(path="empty.py", status="removed", patch=""),
        ])
        index = _build_line_index(diff)
        assert index["empty.py"] is None

    def test_multiple_hunks(self) -> None:
        patch = (
            "@@ -1,3 +1,4 @@\n"
            " line1\n"
            "+added1\n"
            " line3\n"
            "@@ -10,3 +11,4 @@\n"
            " line10\n"
            "+added2\n"
            " line12\n"
        )
        diff = _make_diff([
            FileDiff(path="multi.py", status="modified", patch=patch),
        ])
        index = _build_line_index(diff)
        assert 2 in index["multi.py"]   # added1
        assert 12 in index["multi.py"]  # added2


class TestValidateFindingsAgainstDiff:
    """Tests for hallucination validation."""

    def test_valid_finding_passes(self) -> None:
        diff = _make_diff()
        finding = _make_finding(line_start=11, line_end=11)
        result = validate_findings_against_diff([finding], diff)
        assert len(result) == 1
        assert result[0].confidence == 80  # unchanged

    def test_nonexistent_file_removed(self) -> None:
        diff = _make_diff()
        finding = _make_finding(file_path="nonexistent.py")
        result = validate_findings_against_diff([finding], diff)
        assert len(result) == 0

    def test_out_of_range_penalized(self) -> None:
        diff = _make_diff()
        finding = _make_finding(
            line_start=50, line_end=55, confidence=80,
        )
        result = validate_findings_against_diff([finding], diff)
        assert len(result) == 1
        assert result[0].confidence == 80 - HALLUCINATION_PENALTY

    def test_penalty_clamped_at_zero(self) -> None:
        diff = _make_diff()
        finding = _make_finding(
            line_start=50, line_end=55, confidence=30,
        )
        result = validate_findings_against_diff([finding], diff)
        assert len(result) == 1
        assert result[0].confidence == 0

    def test_all_valid_unchanged(self) -> None:
        diff = _make_diff()
        finding = _make_finding(line_start=11, line_end=11)
        result = validate_findings_against_diff([finding], diff)
        assert result[0] is finding  # Same object, not modified

    def test_multiple_findings_mixed(self) -> None:
        diff = _make_diff()
        valid = _make_finding(line_start=11, line_end=11)
        hallucinated = _make_finding(file_path="ghost.py")
        penalized = _make_finding(
            line_start=99, line_end=100, confidence=60,
        )
        result = validate_findings_against_diff(
            [valid, hallucinated, penalized], diff,
        )
        assert len(result) == 2  # ghost.py removed
        assert result[0].confidence == 80   # valid unchanged
        assert result[1].confidence == 20   # 60 - 40


# New-side numbering: context 10, added 11, context 12, context 13.
# The added-line index therefore holds only {11}, so under the old rule a
# finding about the deleted check or the surrounding context found no
# overlap and lost 40 points.
_REMOVAL_PATCH = """@@ -10,7 +10,7 @@ def handler(request):
     user = request.user
-    if not user.is_authenticated:
-        raise PermissionDenied
+    pass
     return render(request)
     # trailing context
"""


class TestValidationCoversTheWholeHunk:
    """B4: only added lines were indexed.

    A finding about a line the diff deletes, or about the context around a
    change, found no overlap and lost 40 confidence points. "This pull
    request removes the authorisation check" is exactly the kind of finding
    a security agent should be rewarded for.
    """

    @staticmethod
    def _diff(patch: str = _REMOVAL_PATCH) -> PRDiff:
        return PRDiff(
            files=[
                FileDiff(path="src/views.py", status="modified", patch=patch),
            ],
        )

    @staticmethod
    def _finding(line_start: int, line_end: int, confidence: int = 90):
        return Finding(
            id="security-1",
            category="security",
            check_id="S-AUTH-01",
            title="Authorisation check removed",
            description="d",
            file_path="src/views.py",
            line_start=line_start,
            line_end=line_end,
            severity="critical",
            confidence=confidence,
        )

    def test_finding_about_a_deleted_line_is_not_penalised(self) -> None:
        """The removal sits between new-side lines 10 and 11."""
        out = validate_findings_against_diff(
            [self._finding(10, 11)], self._diff(),
        )
        assert out[0].confidence == 90

    def test_finding_on_a_leading_context_line_is_not_penalised(self) -> None:
        out = validate_findings_against_diff(
            [self._finding(10, 10)], self._diff(),
        )
        assert out[0].confidence == 90

    def test_finding_on_a_trailing_context_line_is_not_penalised(self) -> None:
        out = validate_findings_against_diff(
            [self._finding(12, 13)], self._diff(),
        )
        assert out[0].confidence == 90

    def test_finding_outside_every_hunk_is_still_penalised(self) -> None:
        out = validate_findings_against_diff(
            [self._finding(900, 905)], self._diff(),
        )
        assert out[0].confidence == 50

    def test_finding_on_an_added_line_is_not_penalised(self) -> None:
        patch = "@@ -1,2 +1,3 @@\n import os\n+import sys\n context\n"
        out = validate_findings_against_diff(
            [self._finding(2, 2)], self._diff(patch),
        )
        assert out[0].confidence == 90

    def test_deletion_only_hunk_still_bounds_the_check(self) -> None:
        """An empty added-line index previously disabled validation."""
        patch = (
            "@@ -10,4 +10,2 @@\n a\n-b\n-c\n d\n"
        )
        out = validate_findings_against_diff(
            [self._finding(800, 800)], self._diff(patch),
        )
        assert out[0].confidence == 50

    def test_multiple_hunks_are_all_indexed(self) -> None:
        patch = (
            "@@ -1,2 +1,2 @@\n a\n+b\n"
            "@@ -50,2 +50,2 @@\n x\n+y\n"
        )
        out = validate_findings_against_diff(
            [self._finding(50, 51)], self._diff(patch),
        )
        assert out[0].confidence == 90

    def test_unparseable_patch_does_not_penalise(self) -> None:
        """No hunk headers means no basis to judge the line numbers."""
        out = validate_findings_against_diff(
            [self._finding(5, 5)], self._diff("no hunks here at all"),
        )
        assert out[0].confidence == 90

    def test_finding_for_a_file_not_in_the_diff_is_still_dropped(self) -> None:
        finding = self._finding(1, 1)
        finding = replace(finding, file_path="src/other.py")
        assert validate_findings_against_diff([finding], self._diff()) == []


class TestPenaltyIsProportionateToDistance:
    """The flat 40-point penalty was larger than the whole borderline band.

    Any finding whose lines fell outside a hunk lost 40 confidence points,
    which is more than the 15-point gap between reported and borderline and
    enough on its own to drive a 90%-confidence finding below the threshold.
    It was also charged against agents that had been shown the surrounding
    code: context_lines now puts the enclosing declaration in the prompt, so
    a finding about a line just outside a hunk is reasoning about code the
    agent actually read, not evidence of hallucination.
    """

    @staticmethod
    def _diff() -> PRDiff:
        # one hunk covering new-side lines 100-104
        patch = (
            "@@ -100,5 +100,5 @@\n"
            " a\n b\n-c\n+c2\n d\n e\n"
        )
        return PRDiff(
            files=[FileDiff(path="f.py", status="modified", patch=patch)],
        )

    @staticmethod
    def _finding(line_start: int, line_end: int, confidence: int = 90):
        return Finding(
            id="1", category="general", check_id="Q-ARCH-01", title="t",
            description="d", file_path="f.py",
            line_start=line_start, line_end=line_end,
            severity="medium", confidence=confidence,
        )

    def test_inside_the_hunk_is_not_penalised(self) -> None:
        out = validate_findings_against_diff(
            [self._finding(101, 102)], self._diff(), context_lines=40,
        )
        assert out[0].confidence == 90

    def test_inside_the_context_window_is_not_penalised(self) -> None:
        """The agent was shown these lines, so citing them is not a guess."""
        out = validate_findings_against_diff(
            [self._finding(130, 131)], self._diff(), context_lines=40,
        )
        assert out[0].confidence == 90

    def test_just_beyond_the_window_loses_a_little(self) -> None:
        out = validate_findings_against_diff(
            [self._finding(150, 150)], self._diff(), context_lines=40,
        )
        assert 0 < 90 - out[0].confidence < HALLUCINATION_PENALTY

    def test_far_beyond_the_window_loses_the_full_penalty(self) -> None:
        out = validate_findings_against_diff(
            [self._finding(900, 900)], self._diff(), context_lines=40,
        )
        assert out[0].confidence == 90 - HALLUCINATION_PENALTY

    def test_penalty_grows_with_distance(self) -> None:
        near = validate_findings_against_diff(
            [self._finding(150, 150)], self._diff(), context_lines=40,
        )[0].confidence
        far = validate_findings_against_diff(
            [self._finding(170, 170)], self._diff(), context_lines=40,
        )[0].confidence
        assert far < near

    def test_without_context_the_old_behaviour_is_preserved(self) -> None:
        """With context off, any line outside a hunk is a full penalty."""
        out = validate_findings_against_diff(
            [self._finding(150, 150)], self._diff(), context_lines=0,
        )
        assert out[0].confidence == 90 - HALLUCINATION_PENALTY
