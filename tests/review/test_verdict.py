"""Tests for verdict state machine (story-5-5)."""

from __future__ import annotations

from prbot.review.models import AgentError, AgentResult, Finding, TokenUsage
from prbot.review.scorer import ReviewScore, ScoredFinding, score_findings
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
        assert has_blocker_findings([sf], blocker_confidence=80)

    def test_medium_not_blocker(self) -> None:
        finding = _make_finding(severity="medium", confidence=90)
        sf = ScoredFinding.from_finding(finding, threshold=70)
        assert not has_blocker_findings([sf], blocker_confidence=80)


class TestVerdictScalesAreSeparate:
    """B3: a 0-100 quality score was compared against a confidence threshold.

    They share a range and mean different things, and has_blocker_findings
    was written, tested and never called by determine_verdict.
    """

    @staticmethod
    def _score(clamped: int, critical_override: bool = False) -> ReviewScore:
        return ReviewScore(
            raw_score=float(clamped),
            clamped_score=clamped,
            total_deductions=100.0 - clamped,
            finding_count=1,
            critical_override=critical_override,
        )

    @staticmethod
    def _scored(severity: str, confidence: int) -> ScoredFinding:
        return ScoredFinding(
            finding=Finding(
                id="general-1",
                category="general",
                check_id="Q-ERR-01",
                title="t",
                description="d",
                file_path="src/app.py",
                line_start=1,
                line_end=1,
                severity=severity,
                confidence=confidence,
            ),
            band="reported",
            deduction=1.0,
        )

    def test_a_high_confidence_blocker_requests_changes(self) -> None:
        """Even when the score stays above the passing mark."""
        reported = [self._scored("critical", 95)]
        verdict = determine_verdict(
            [AgentResult(agent="general", findings=[])],
            reported,
            self._score(99),
            blocker_confidence=80,
            min_passing_score=70,
        )
        assert verdict == ReviewVerdict.REQUEST_CHANGES

    def test_a_low_confidence_blocker_does_not(self) -> None:
        reported = [self._scored("critical", 50)]
        verdict = determine_verdict(
            [AgentResult(agent="general", findings=[])],
            reported,
            self._score(99),
            blocker_confidence=80,
            min_passing_score=70,
        )
        assert verdict == ReviewVerdict.COMMENT

    def test_score_below_the_passing_mark_requests_changes(self) -> None:
        reported = [self._scored("low", 50)]
        verdict = determine_verdict(
            [AgentResult(agent="general", findings=[])],
            reported,
            self._score(40),
            blocker_confidence=80,
            min_passing_score=70,
        )
        assert verdict == ReviewVerdict.REQUEST_CHANGES

    def test_passing_score_with_no_blocker_comments(self) -> None:
        reported = [self._scored("medium", 75)]
        verdict = determine_verdict(
            [AgentResult(agent="general", findings=[])],
            reported,
            self._score(88),
            blocker_confidence=80,
            min_passing_score=70,
        )
        assert verdict == ReviewVerdict.COMMENT

    def test_the_two_thresholds_are_independent(self) -> None:
        """Raising the blocker confidence must not move the passing mark."""
        reported = [self._scored("high", 85)]
        strict = determine_verdict(
            [AgentResult(agent="general", findings=[])],
            reported, self._score(88),
            blocker_confidence=80, min_passing_score=70,
        )
        lenient = determine_verdict(
            [AgentResult(agent="general", findings=[])],
            reported, self._score(88),
            blocker_confidence=90, min_passing_score=70,
        )
        assert strict == ReviewVerdict.REQUEST_CHANGES
        assert lenient == ReviewVerdict.COMMENT


class TestConfigDefaultsAgreeWithFunctionDefaults:
    """B3: config defaulted blocker_threshold to 70, the functions to 80."""

    def test_blocker_confidence_defaults_match(self) -> None:
        import inspect

        from prbot.config import PrBotConfig

        sig = inspect.signature(determine_verdict)
        assert (
            sig.parameters["blocker_confidence"].default
            == PrBotConfig.model_fields["blocker_threshold"].default
        )

    def test_passing_score_defaults_match(self) -> None:
        import inspect

        from prbot.config import PrBotConfig

        sig = inspect.signature(determine_verdict)
        assert (
            sig.parameters["min_passing_score"].default
            == PrBotConfig.model_fields["min_passing_score"].default
        )


class TestChunkedOutcomesDoNotOverCap:
    """GEN-ARCH-01: chunking changed the shape of the outcomes list.

    Before chunking, outcomes held one entry per agent, so 'any agent
    errored' meant half the review was missing. With N chunks the list holds
    N*len(agents) entries, and a single transient failure in one chunk
    silently capped the whole pull request at COMMENT, discarding
    blocker-grade findings that the other chunks had already produced.

    An agent's coverage is only lost when every chunk failed for that agent.
    """

    @staticmethod
    def _blocker() -> list[ScoredFinding]:
        return [
            ScoredFinding(
                finding=Finding(
                    id="general-1", category="general", check_id="Q-ERR-01",
                    title="t", description="d", file_path="a.py",
                    line_start=1, line_end=1,
                    severity="critical", confidence=95,
                ),
                band="reported", deduction=23.75,
            ),
        ]

    @staticmethod
    def _score(
        clamped: int = 20, override: bool = True, findings: int = 1,
    ) -> ReviewScore:
        return ReviewScore(
            raw_score=float(clamped), clamped_score=clamped,
            total_deductions=100.0 - clamped, finding_count=findings,
            critical_override=override,
        )

    def test_one_chunk_failing_does_not_cap_the_verdict(self) -> None:
        outcomes = [
            AgentResult(agent="general", findings=[]),
            AgentResult(agent="security", findings=[]),
            AgentError(agent="general", error_type="throttled", message="m"),
            AgentResult(agent="security", findings=[]),
        ]
        assert determine_verdict(
            outcomes, self._blocker(), self._score(),
        ) == ReviewVerdict.REQUEST_CHANGES

    def test_an_agent_failing_in_every_chunk_still_caps(self) -> None:
        outcomes = [
            AgentResult(agent="general", findings=[]),
            AgentError(agent="security", error_type="throttled", message="m"),
            AgentResult(agent="general", findings=[]),
            AgentError(agent="security", error_type="throttled", message="m"),
        ]
        assert determine_verdict(
            outcomes, self._blocker(), self._score(),
        ) == ReviewVerdict.COMMENT

    def test_the_unchunked_single_failure_still_caps(self) -> None:
        """One agent, one chunk, failed: that agent has no coverage."""
        outcomes = [
            AgentResult(agent="general", findings=[]),
            AgentError(agent="security", error_type="timeout", message="m"),
        ]
        assert determine_verdict(
            outcomes, self._blocker(), self._score(),
        ) == ReviewVerdict.COMMENT

    def test_every_agent_failing_everywhere_is_still_comment(self) -> None:
        outcomes = [
            AgentError(agent="general", error_type="x", message="m"),
            AgentError(agent="security", error_type="x", message="m"),
        ]
        assert determine_verdict(
            outcomes, [], self._score(100, False),
        ) == ReviewVerdict.COMMENT

    def test_a_clean_chunked_review_can_still_approve(self) -> None:
        outcomes = [
            AgentResult(agent="general", findings=[]),
            AgentResult(agent="security", findings=[]),
            AgentResult(agent="general", findings=[]),
            AgentResult(agent="security", findings=[]),
        ]
        assert determine_verdict(
            outcomes, [], self._score(100, False, findings=0),
        ) == ReviewVerdict.APPROVE

    def test_partial_coverage_is_reported(self) -> None:
        from prbot.review.verdict import agents_without_coverage

        outcomes = [
            AgentResult(agent="general", findings=[]),
            AgentError(agent="security", error_type="x", message="m"),
            AgentError(agent="general", error_type="x", message="m"),
            AgentError(agent="security", error_type="x", message="m"),
        ]
        assert agents_without_coverage(outcomes) == {"security"}


class TestAFailedChunkIsNeverApproved:
    """SEC-DESIGN-02: a clean result covers only the files that were reviewed.

    An agent that failed on a chunk never saw that chunk's files, so a review
    with no findings says nothing about them, and the failed chunk may be the
    one with the defect. Every agent could fail on one chunk and succeed on
    another and the pull request was approved with exit 0. Chunking by what
    is sent makes a large diff several chunks, so this happens more often.
    """

    _CLEAN = ReviewScore(
        raw_score=100.0, clamped_score=100, total_deductions=0.0,
        finding_count=0, critical_override=False,
    )

    @staticmethod
    def _failed(agent: str) -> AgentError:
        return AgentError(agent=agent, error_type="throttled", message="m")

    def test_a_chunk_every_agent_failed_on_is_not_approved(self) -> None:
        outcomes = [
            AgentResult(agent="general", findings=[]),
            AgentResult(agent="security", findings=[]),
            self._failed("general"),
            self._failed("security"),
        ]
        assert determine_verdict(
            outcomes, [], self._CLEAN,
        ) == ReviewVerdict.COMMENT

    def test_one_agent_failing_on_one_chunk_is_not_approved(self) -> None:
        outcomes = [
            AgentResult(agent="general", findings=[]),
            AgentResult(agent="security", findings=[]),
            AgentResult(agent="general", findings=[]),
            self._failed("security"),
        ]
        assert determine_verdict(
            outcomes, [], self._CLEAN,
        ) == ReviewVerdict.COMMENT

    def test_a_blocker_from_another_chunk_still_requests_changes(self) -> None:
        """Holding back approval does not discard what other chunks found."""
        outcomes = [
            AgentResult(agent="general", findings=[]),
            AgentResult(agent="security", findings=[]),
            self._failed("general"),
            self._failed("security"),
        ]
        blocker = TestChunkedOutcomesDoNotOverCap._blocker()
        score = TestChunkedOutcomesDoNotOverCap._score()
        assert determine_verdict(
            outcomes, blocker, score,
        ) == ReviewVerdict.REQUEST_CHANGES
