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

    Each call gets its own line range and its own title by default, so that
    findings meant to be separate are not merged as one defect by
    deduplicate_findings (B1, D1). The title matters as much as the line
    range now: what makes two reports one defect is agreement about the
    subject, so two findings that name the same subject are one however far
    apart they sit. Pass distinct=False to build a deliberate duplicate.
    """
    n = next(_finding_seq) if distinct else 0
    line = n * 100 + 1 if distinct else 1
    return Finding(
        id="test-1",
        category="general",
        check_id=check_id,
        title=f"Defect {n}" if distinct else "Test",
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

    def test_borderline_deducts_at_reduced_weight(self) -> None:
        finding = _make_finding(severity="high", confidence=60)
        scored = ScoredFinding.from_finding(finding, threshold=70)
        assert scored.band == "borderline"
        # A finding the reviewer is less sure of costs less, not nothing:
        # weight 15.0 (high) * 0.60 confidence * BORDERLINE_DEDUCTION_FACTOR.
        assert scored.deduction == pytest.approx(15.0 * 0.60 * 0.5)

    def test_hidden_no_deduction(self) -> None:
        # medium, not critical: the severity floor deliberately rescues a
        # critical from `hidden` however unconfident it is, so a critical
        # can no longer demonstrate the hidden band. See TestSeverityFloor.
        finding = _make_finding(severity="medium", confidence=40)
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


class TestBorderlineMovesTheScore:
    """Half a weight is only half a weight if it reaches the total.

    ScoredFinding.from_finding computed a borderline deduction, but
    score_findings summed only the reported band, so the deduction was
    computed and then thrown away and the score stayed at 100.
    """

    def test_a_borderline_finding_lowers_the_score(self) -> None:
        result = _make_result([_make_finding(severity="high", confidence=60)])
        _, borderline, _, score = score_findings([result])
        assert len(borderline) == 1
        assert score.clamped_score < 100

    def test_the_total_counts_both_bands(self) -> None:
        result = _make_result([
            _make_finding(severity="high", confidence=85),    # reported
            _make_finding(severity="high", confidence=60),    # borderline
        ])
        _, _, _, score = score_findings([result])
        expected = 15.0 * 0.85 + 15.0 * 0.60 * 0.5
        assert score.total_deductions == pytest.approx(expected)

    def test_a_surfaced_critical_moves_the_score(self) -> None:
        """The MR 194 shape: a low-confidence critical must cost something."""
        result = _make_result(
            [_make_finding(severity="critical", confidence=38)],
        )
        _, _, _, score = score_findings([result])
        assert score.clamped_score == int(100 - 25.0 * 0.38 * 0.5)

    def test_hidden_findings_still_cost_nothing(self) -> None:
        result = _make_result([_make_finding(severity="low", confidence=10)])
        _, _, hidden, score = score_findings([result])
        assert hidden == 1
        assert score.clamped_score == 100


class TestSeverityFloor:
    """A finding that would lose data is never silently dropped (C3)."""

    def test_critical_is_never_hidden(self) -> None:
        scored = ScoredFinding.from_finding(
            _make_finding(severity="critical", confidence=20), 70,
        )
        assert scored.band == "borderline"

    def test_high_is_never_hidden(self) -> None:
        scored = ScoredFinding.from_finding(
            _make_finding(severity="high", confidence=5), 70,
        )
        assert scored.band == "borderline"

    def test_low_confidence_critical_still_deducts(self) -> None:
        scored = ScoredFinding.from_finding(
            _make_finding(severity="critical", confidence=38), 70,
        )
        assert scored.deduction == pytest.approx(25.0 * 0.38 * 0.5)

    def test_medium_is_still_hidden_when_unconfident(self) -> None:
        scored = ScoredFinding.from_finding(
            _make_finding(severity="medium", confidence=20), 70,
        )
        assert scored.band == "hidden"
        assert scored.deduction == 0.0

    def test_floor_does_not_promote_into_reported(self) -> None:
        """The floor makes a finding visible. It does not make it confident."""
        scored = ScoredFinding.from_finding(
            _make_finding(severity="critical", confidence=10), 70,
        )
        assert scored.band != "reported"

    def test_critical_at_low_confidence_is_not_a_blocker(self) -> None:
        """A surfaced critical must not auto-fail a review on suspicion."""
        result = _make_result(
            [_make_finding(severity="critical", confidence=30)],
        )
        _, borderline, _, score = score_findings([result])
        assert len(borderline) == 1
        assert score.critical_override is False


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

    def test_same_check_and_subject_across_files_is_one_finding(self) -> None:
        """One defect in two files was deducted twice (D1).

        The hardcoded account id that lives in both the CI file and the
        README is one defect with two homes, not two defects.
        """
        from prbot.review.scorer import deduplicate_findings

        merged = deduplicate_findings([
            AgentResult(agent="general", findings=[_finding(
                check_id="Q-ARCH-04",
                title="AWS account ID hardcoded in CI pipeline and README",
                file_path=".gitlab-ci.yml",
            )]),
            AgentResult(agent="security", findings=[_finding(
                "security",
                check_id="Q-ARCH-04",
                title="Hardcoded AWS account ID exposed in public README",
                file_path="README.md",
            )]),
        ])
        assert len(merged) == 1

    def test_same_check_different_subjects_across_files_stay_separate(
        self,
    ) -> None:
        """The same check firing on two unrelated things is two defects."""
        from prbot.review.scorer import deduplicate_findings

        merged = deduplicate_findings([
            AgentResult(agent="general", findings=[_finding(
                check_id="Q-TEST-02",
                title="No test for the retry backoff ceiling",
                file_path="tests/test_retry.py",
            )]),
            AgentResult(agent="general", findings=[_finding(
                check_id="Q-TEST-02",
                title="Pagination cursor is never exercised",
                file_path="tests/test_paging.py",
            )]),
        ])
        assert len(merged) == 2

    def test_same_check_in_one_file_is_one_finding_whatever_the_lines(
        self,
    ) -> None:
        """Line overlap is not what makes two reports one defect (D1)."""
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
        assert len(merged) == 1

    def test_different_checks_on_distant_lines_stay_separate(self) -> None:
        from prbot.review.scorer import deduplicate_findings

        merged = deduplicate_findings([
            AgentResult(agent="general", findings=[_finding(
                check_id="Q-ERR-01", line_start=10, line_end=12,
            )]),
            AgentResult(agent="security", findings=[_finding(
                "security",
                check_id="S-CRED-01",
                title="Secret key committed to the repository",
                line_start=90, line_end=92,
            )]),
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
