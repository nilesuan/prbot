"""Tests for verdict state machine (story-5-5)."""

from __future__ import annotations

from prbot.review.models import AgentError, AgentResult, Finding, TokenUsage
from prbot.review.scorer import ScoredFinding, score_findings
from prbot.review.verdict import (
    ReviewVerdict,
    determine_verdict,
    has_blocker_findings,
)


def _make_finding(
    *,
    severity: str = "medium",
    confidence: int = 75,
) -> Finding:
    return Finding(
        id="test-1",
        category="general",
        check_id="Q-ARCH-01",
        title="Test",
        description="Test desc",
        file_path="src/test.py",
        line_start=1,
        line_end=10,
        severity=severity,
        confidence=confidence,
    )


def _make_result(findings: list[Finding] | None = None) -> AgentResult:
    return AgentResult(
        agent="general",
        findings=findings or [],
        token_usage=TokenUsage(1000, 200, 0.0),
        latency_ms=500,
        model_id="test-model",
    )


def _make_error() -> AgentError:
    return AgentError(
        agent="security",
        error_type="timeout",
        message="timed out",
    )


def _score_and_verdict(outcomes, blocker_threshold=80):
    reported, _borderline, _hidden, score = score_findings(
        outcomes, blocker_threshold=blocker_threshold,
    )
    verdict = determine_verdict(
        outcomes, reported, score, blocker_threshold,
    )
    return verdict, score


class TestDetermineVerdict:
    """Tests for 8-scenario verdict decision table."""

    def test_scenario1_clean_approve(self) -> None:
        """No findings, all agents OK → APPROVE."""
        outcomes = [_make_result([]), _make_result([])]
        verdict, _ = _score_and_verdict(outcomes)
        assert verdict == ReviewVerdict.APPROVE

    def test_scenario2_no_findings_agent_failed(self) -> None:
        """No findings, one agent failed → COMMENT."""
        outcomes = [_make_result([]), _make_error()]
        verdict, _ = _score_and_verdict(outcomes)
        assert verdict == ReviewVerdict.COMMENT

    def test_scenario3_both_failed(self) -> None:
        """Both agents failed → COMMENT (G-28)."""
        outcomes = [_make_error(), _make_error()]
        verdict, _ = _score_and_verdict(outcomes)
        assert verdict == ReviewVerdict.COMMENT

    def test_scenario4_findings_above_threshold(self) -> None:
        """Findings present, score above threshold → COMMENT."""
        finding = _make_finding(severity="low", confidence=75)
        outcomes = [_make_result([finding]), _make_result([])]
        verdict, score = _score_and_verdict(outcomes)
        assert verdict == ReviewVerdict.COMMENT
        assert score.clamped_score >= 80

    def test_scenario5_score_below_threshold(self) -> None:
        """Score below threshold → REQUEST_CHANGES."""
        findings = [
            _make_finding(severity="critical", confidence=75),
            _make_finding(severity="high", confidence=80),
            _make_finding(severity="high", confidence=85),
        ]
        outcomes = [_make_result(findings), _make_result([])]
        verdict, score = _score_and_verdict(outcomes)
        assert verdict == ReviewVerdict.REQUEST_CHANGES
        assert score.clamped_score < 80

    def test_scenario6_critical_override(self) -> None:
        """Critical finding at high confidence → REQUEST_CHANGES."""
        finding = _make_finding(severity="critical", confidence=90)
        outcomes = [_make_result([finding]), _make_result([])]
        verdict, score = _score_and_verdict(outcomes)
        assert verdict == ReviewVerdict.REQUEST_CHANGES
        assert score.critical_override is True

    def test_scenario7_agent_failed_with_findings(self) -> None:
        """Agent failed + findings → COMMENT (never REQUEST_CHANGES)."""
        finding = _make_finding(severity="critical", confidence=90)
        outcomes = [_make_result([finding]), _make_error()]
        verdict, _ = _score_and_verdict(outcomes)
        assert verdict == ReviewVerdict.COMMENT

    def test_scenario8_agent_failed_no_findings(self) -> None:
        """Agent failed + no findings → COMMENT."""
        outcomes = [_make_result([]), _make_error()]
        verdict, _ = _score_and_verdict(outcomes)
        assert verdict == ReviewVerdict.COMMENT

    def test_never_approve_with_errors(self) -> None:
        """Even with zero findings, error → not APPROVE."""
        outcomes = [_make_result([]), _make_error()]
        verdict, _ = _score_and_verdict(outcomes)
        assert verdict != ReviewVerdict.APPROVE

    def test_never_request_changes_with_errors(self) -> None:
        """Even with critical findings, error → COMMENT."""
        finding = _make_finding(severity="critical", confidence=95)
        outcomes = [_make_result([finding]), _make_error()]
        verdict, _ = _score_and_verdict(outcomes)
        assert verdict != ReviewVerdict.REQUEST_CHANGES


class TestHasBlockerFindings:
    """Tests for has_blocker_findings helper."""

    def test_critical_high_confidence(self) -> None:
        finding = _make_finding(severity="critical", confidence=90)
        sf = ScoredFinding.from_finding(finding, threshold=70)
        assert has_blocker_findings([sf], blocker_threshold=80)

    def test_medium_not_blocker(self) -> None:
        finding = _make_finding(severity="medium", confidence=90)
        sf = ScoredFinding.from_finding(finding, threshold=70)
        assert not has_blocker_findings([sf], blocker_threshold=80)
