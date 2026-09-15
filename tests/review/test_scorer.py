"""Tests for confidence-banded scoring (story-5-5)."""

from __future__ import annotations

import itertools

import pytest

from prbot.review.models import AgentError, AgentResult, Finding, TokenUsage
from prbot.review.scorer import (
    ScoredFinding,
    classify_confidence_band,
    score_findings,
)

_finding_seq = itertools.count()


def _make_finding(
    *,
    severity: str = "medium",
    confidence: int = 75,
    check_id: str = "Q-ARCH-01",
    distinct: bool = True,
) -> Finding:
    """Build a finding.

    Each call lands on its own line range by default so that findings which
    are meant to be separate are not merged as one defect by
    deduplicate_findings (B1). Pass distinct=False to build a deliberate
    duplicate.
    """
    line = next(_finding_seq) * 100 + 1 if distinct else 1
    return Finding(
        id="test-1",
        category="general",
        check_id=check_id,
        title="Test",
        description="Test desc",
        file_path="src/test.py",
        line_start=line,
        line_end=line + 9,
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


class TestClassifyConfidenceBand:
    """Tests for three-band classification."""

    def test_reported_above_threshold(self) -> None:
        assert classify_confidence_band(85, 70) == "reported"

    def test_reported_at_threshold(self) -> None:
        assert classify_confidence_band(70, 70) == "reported"

    def test_borderline_just_below(self) -> None:
        assert classify_confidence_band(60, 70) == "borderline"

    def test_borderline_at_lower_bound(self) -> None:
        assert classify_confidence_band(55, 70) == "borderline"

    def test_hidden_below_borderline(self) -> None:
        assert classify_confidence_band(50, 70) == "hidden"

    def test_hidden_zero_confidence(self) -> None:
        assert classify_confidence_band(0, 70) == "hidden"


class TestScoredFinding:
    """Tests for ScoredFinding creation and deduction."""

    def test_reported_deduction(self) -> None:
        finding = _make_finding(severity="high", confidence=85)
        scored = ScoredFinding.from_finding(finding, threshold=70)
        assert scored.band == "reported"
        # 15.0 * 0.85 = 12.75
        assert scored.deduction == pytest.approx(12.75)

    def test_borderline_no_deduction(self) -> None:
        finding = _make_finding(severity="high", confidence=60)
        scored = ScoredFinding.from_finding(finding, threshold=70)
        assert scored.band == "borderline"
        assert scored.deduction == 0.0

    def test_hidden_no_deduction(self) -> None:
        finding = _make_finding(severity="critical", confidence=40)
        scored = ScoredFinding.from_finding(finding, threshold=70)
        assert scored.band == "hidden"
        assert scored.deduction == 0.0

    def test_critical_weight(self) -> None:
        finding = _make_finding(severity="critical", confidence=100)
        scored = ScoredFinding.from_finding(finding, threshold=70)
        assert scored.deduction == pytest.approx(25.0)

    def test_medium_weight(self) -> None:
        finding = _make_finding(severity="medium", confidence=100)
        scored = ScoredFinding.from_finding(finding, threshold=70)
        assert scored.deduction == pytest.approx(8.0)

    def test_info_zero_weight(self) -> None:
        finding = _make_finding(severity="info", confidence=100)
        scored = ScoredFinding.from_finding(finding, threshold=70)
        assert scored.deduction == 0.0


class TestScoreFindings:
    """Tests for score_findings aggregation."""

    def test_no_findings_perfect_score(self) -> None:
        result = _make_result([])
        reported, _borderline, _hidden, score = score_findings([result])
        assert score.clamped_score == 100
        assert len(reported) == 0

    def test_reported_finding_deducts(self) -> None:
        finding = _make_finding(severity="high", confidence=85)
        result = _make_result([finding])
        reported, _, _, score = score_findings([result])
        assert len(reported) == 1
        assert score.clamped_score < 100

    def test_hidden_count_tracked(self) -> None:
        # confidence=40 with threshold=70 → hidden (below 55)
        finding = _make_finding(confidence=40)
        result = _make_result([finding])
        _, _, hidden_count, _ = score_findings([result])
        assert hidden_count == 1

    def test_critical_override(self) -> None:
        finding = _make_finding(severity="critical", confidence=90)
        result = _make_result([finding])
        _, _, _, score = score_findings(
            [result], blocker_threshold=80,
        )
        assert score.critical_override is True
        assert score.clamped_score == 0

    def test_score_clamped_at_zero(self) -> None:
        # Many high-severity findings should not go negative
        findings = [
            _make_finding(severity="critical", confidence=100)
            for _ in range(10)
        ]
        result = _make_result(findings)
        _, _, _, score = score_findings([result])
        assert score.clamped_score == 0

    def test_agent_errors_skipped(self) -> None:
        error = AgentError(
            agent="security",
            error_type="timeout",
            message="timed out",
        )
        reported, _, _, score = score_findings([error])
        assert score.clamped_score == 100
        assert len(reported) == 0

    def test_mixed_bands(self) -> None:
        findings = [
            _make_finding(confidence=85),   # reported
            _make_finding(confidence=60),   # borderline
            _make_finding(confidence=40),   # hidden
        ]
        result = _make_result(findings)
        reported, borderline, hidden, _ = score_findings([result])
        assert len(reported) == 1
        assert len(borderline) == 1
        assert hidden == 1


def _finding(
    agent: str = "general",
    check_id: str = "Q-ERR-01",
    title: str = "Bare except swallows the error",
    file_path: str = "src/app.py",
    line_start: int = 10,
    line_end: int = 12,
    severity: str = "high",
    confidence: int = 90,
) -> Finding:
    return Finding(
        id=f"{agent}-1",
        category="security" if agent == "security" else "general",
        check_id=check_id,
        title=title,
        description="d",
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        severity=severity,
        confidence=confidence,
    )


class TestDeduplication:
    """B1: both agents reporting one defect deducted for it twice."""

    def test_same_check_and_overlap_is_one_finding(self) -> None:
        from prbot.review.scorer import deduplicate_findings

        merged = deduplicate_findings([
            AgentResult(agent="general", findings=[_finding("general")]),
            AgentResult(agent="security", findings=[_finding("security")]),
        ])
        assert len(merged) == 1

    def test_merged_finding_records_both_agents(self) -> None:
        from prbot.review.scorer import deduplicate_findings

        merged = deduplicate_findings([
            AgentResult(agent="general", findings=[_finding("general")]),
            AgentResult(agent="security", findings=[_finding("security")]),
        ])
        assert set(merged[0].reported_by) == {"general", "security"}

    def test_merged_finding_keeps_the_higher_confidence(self) -> None:
        from prbot.review.scorer import deduplicate_findings

        merged = deduplicate_findings([
            AgentResult(agent="general", findings=[_finding(confidence=60)]),
            AgentResult(
                agent="security",
                findings=[_finding("security", confidence=95)],
            ),
        ])
        assert merged[0].confidence == 95

    def test_merged_finding_keeps_the_higher_severity(self) -> None:
        from prbot.review.scorer import deduplicate_findings

        merged = deduplicate_findings([
            AgentResult(agent="general", findings=[_finding(severity="low")]),
            AgentResult(
                agent="security",
                findings=[_finding("security", severity="critical")],
            ),
        ])
        assert merged[0].severity == "critical"

    def test_same_title_across_check_ids_is_one_finding(self) -> None:
        """Agents use different prefixes for the same defect."""
        from prbot.review.scorer import deduplicate_findings

        merged = deduplicate_findings([
            AgentResult(agent="general", findings=[_finding(check_id="Q-ERR-01")]),
            AgentResult(
                agent="security",
                findings=[_finding("security", check_id="S-DATA-02")],
            ),
        ])
        assert len(merged) == 1

    def test_different_files_stay_separate(self) -> None:
        from prbot.review.scorer import deduplicate_findings

        merged = deduplicate_findings([
            AgentResult(agent="general", findings=[_finding(file_path="a.py")]),
            AgentResult(
                agent="security",
                findings=[_finding("security", file_path="b.py")],
            ),
        ])
        assert len(merged) == 2

    def test_non_overlapping_lines_stay_separate(self) -> None:
        from prbot.review.scorer import deduplicate_findings

        merged = deduplicate_findings([
            AgentResult(
                agent="general",
                findings=[_finding(line_start=10, line_end=12)],
            ),
            AgentResult(
                agent="security",
                findings=[_finding("security", line_start=90, line_end=92)],
            ),
        ])
        assert len(merged) == 2

    def test_different_defects_on_the_same_lines_stay_separate(self) -> None:
        from prbot.review.scorer import deduplicate_findings

        merged = deduplicate_findings([
            AgentResult(agent="general", findings=[_finding()]),
            AgentResult(
                agent="security",
                findings=[
                    _finding(
                        "security",
                        check_id="S-CRED-01",
                        title="Hardcoded API key",
                    ),
                ],
            ),
        ])
        assert len(merged) == 2

    def test_scoring_counts_a_duplicate_once(self) -> None:
        outcomes = [
            AgentResult(agent="general", findings=[_finding("general")]),
            AgentResult(agent="security", findings=[_finding("security")]),
        ]
        reported, _, _, score = score_findings(
            outcomes, threshold=70, blocker_threshold=70,
        )
        assert len(reported) == 1
        assert score.total_deductions == pytest.approx(13.5)
        assert score.clamped_score == 86
