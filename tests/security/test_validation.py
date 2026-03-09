"""Tests for hallucination validation (story-6-7)."""

from __future__ import annotations

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
        diff = _make_diff([
            FileDiff(path="empty.py", status="removed", patch=""),
        ])
        index = _build_line_index(diff)
        assert index["empty.py"] == set()

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
