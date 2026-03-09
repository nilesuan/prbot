"""Tests for markdown formatter (story-5-5)."""

from __future__ import annotations

from prbot.review.formatter import (
    _format_agent_status,
    _format_borderline_section,
    _format_findings_table,
    _format_footer,
    _format_header,
    format_review_comment,
    truncate_comment,
)
from prbot.review.models import AgentError, AgentResult, Finding, TokenUsage
from prbot.review.scorer import ReviewScore, ScoredFinding
from prbot.review.verdict import ReviewVerdict


def _make_finding(
    *,
    severity: str = "medium",
    confidence: int = 75,
    check_id: str = "Q-ARCH-01",
    title: str = "Test finding",
) -> Finding:
    return Finding(
        id="test-1",
        category="general",
        check_id=check_id,
        title=title,
        description="Test description",
        file_path="src/test.py",
        line_start=1,
        line_end=10,
        severity=severity,
        confidence=confidence,
        suggestion="Fix it",
    )


def _make_scored(
    *,
    severity: str = "medium",
    confidence: int = 75,
) -> ScoredFinding:
    finding = _make_finding(severity=severity, confidence=confidence)
    return ScoredFinding.from_finding(finding, threshold=70)


def _make_score(
    clamped: int = 95,
    critical_override: bool = False,
) -> ReviewScore:
    return ReviewScore(
        raw_score=float(clamped),
        clamped_score=clamped,
        total_deductions=100.0 - clamped,
        finding_count=1,
        critical_override=critical_override,
    )


class TestFormatHeader:
    """Tests for _format_header."""

    def test_approve_header(self) -> None:
        header = _format_header(ReviewVerdict.APPROVE, _make_score(95))
        assert "APPROVE" in header
        assert "95/100" in header
        assert "✅" in header

    def test_request_changes_header(self) -> None:
        header = _format_header(
            ReviewVerdict.REQUEST_CHANGES, _make_score(40),
        )
        assert "REQUEST_CHANGES" in header
        assert "❌" in header

    def test_critical_override_note(self) -> None:
        header = _format_header(
            ReviewVerdict.REQUEST_CHANGES,
            _make_score(0, critical_override=True),
        )
        assert "critical override" in header


class TestFormatFindingsTable:
    """Tests for _format_findings_table."""

    def test_no_findings(self) -> None:
        result = _format_findings_table([])
        assert "No findings" in result

    def test_table_contains_finding(self) -> None:
        scored = _make_scored(severity="high", confidence=85)
        result = _format_findings_table([scored])
        assert "Q-ARCH-01" in result
        assert "high" in result
        assert "85%" in result

    def test_sorted_by_severity(self) -> None:
        critical = _make_scored(severity="critical", confidence=80)
        low = _make_scored(severity="low", confidence=90)
        result = _format_findings_table([low, critical])
        crit_pos = result.index("critical")
        low_pos = result.index("low")
        assert crit_pos < low_pos

    def test_includes_suggestion(self) -> None:
        scored = _make_scored()
        result = _format_findings_table([scored])
        assert "Fix it" in result


class TestFormatBorderlineSection:
    """Tests for _format_borderline_section."""

    def test_empty_borderline(self) -> None:
        assert _format_borderline_section([]) == ""

    def test_collapsed_section(self) -> None:
        finding = _make_finding(confidence=60)
        scored = ScoredFinding(finding=finding, band="borderline", deduction=0)
        result = _format_borderline_section([scored])
        assert "<details>" in result
        assert "</details>" in result
        assert "Borderline" in result


class TestFormatAgentStatus:
    """Tests for _format_agent_status."""

    def test_success_status(self) -> None:
        result = AgentResult(
            agent="general",
            findings=[],
            token_usage=TokenUsage(1000, 200, 0.0),
            latency_ms=500,
            model_id="test",
        )
        status = _format_agent_status([result])
        assert "general" in status
        assert "✅" in status

    def test_error_status(self) -> None:
        error = AgentError(
            agent="security",
            error_type="timeout",
            message="timed out",
            retryable=True,
        )
        status = _format_agent_status([error])
        assert "security" in status
        assert "❌" in status
        assert "retryable" in status


class TestFormatFooter:
    """Tests for _format_footer."""

    def test_disclaimer_present(self) -> None:
        footer = _format_footer(0, "")
        assert "AI model" in footer
        assert "may contain errors" in footer

    def test_hidden_count_shown(self) -> None:
        footer = _format_footer(5, "")
        assert "5" in footer
        assert "hidden" in footer

    def test_state_html_included(self) -> None:
        footer = _format_footer(0, "<!-- state -->")
        assert "<!-- state -->" in footer


class TestFormatReviewComment:
    """Tests for format_review_comment integration."""

    def test_complete_comment(self) -> None:
        score = _make_score(90)
        reported = [_make_scored()]
        comment = format_review_comment(
            ReviewVerdict.COMMENT,
            score,
            reported,
            [],
            0,
            [AgentResult(
                agent="general",
                findings=[],
                token_usage=TokenUsage(1000, 200, 0.0),
                latency_ms=500,
                model_id="test",
            )],
        )
        assert "COMMENT" in comment
        assert "90/100" in comment

    def test_both_agents_failed(self) -> None:
        """G-28: Both agents failed → Review Incomplete."""
        score = _make_score(100)
        errors = [
            AgentError(agent="general", error_type="timeout", message="t/o"),
            AgentError(agent="security", error_type="timeout", message="t/o"),
        ]
        comment = format_review_comment(
            ReviewVerdict.COMMENT, score, [], [], 0, errors,
        )
        assert "Review Incomplete" in comment
        assert "general" in comment
        assert "security" in comment


class TestTruncateComment:
    """Tests for progressive truncation."""

    def test_removes_borderline_first(self) -> None:
        comment = (
            "## Header\n\n"
            "Content\n\n"
            "<details>\nBorderline stuff\n</details>\n\n"
            "---\nFooter"
        )
        result = truncate_comment(comment, 50, [], [])
        assert "<details>" not in result
        assert "Header" in result

    def test_preserves_short_comment(self) -> None:
        comment = "Short comment"
        result = truncate_comment(comment, 1000, [], [])
        assert result == comment
