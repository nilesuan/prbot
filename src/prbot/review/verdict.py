"""Verdict state machine (story-5-2).

Deterministic verdict determination covering 8 scenarios.
Agent failure always downgrades — never APPROVE or REQUEST_CHANGES
with incomplete data.
"""

from __future__ import annotations

import enum
import logging

from prbot.review.models import AgentError, AgentOutcome, AgentResult
from prbot.review.scorer import ReviewScore, ScoredFinding

logger = logging.getLogger(__name__)


class ReviewVerdict(enum.StrEnum):
    """Review verdict outcomes."""

    APPROVE = "APPROVE"
    COMMENT = "COMMENT"
    REQUEST_CHANGES = "REQUEST_CHANGES"


def has_blocker_findings(
    reported: list[ScoredFinding],
    blocker_threshold: int = 80,
) -> bool:
    """Check if any finding is a blocker (critical/high + high confidence)."""
    return any(
        sf.finding.severity in ("critical", "high")
        and sf.finding.confidence >= blocker_threshold
        for sf in reported
    )


def determine_verdict(
    outcomes: list[AgentOutcome],
    reported: list[ScoredFinding],
    score: ReviewScore,
    blocker_threshold: int = 80,
) -> ReviewVerdict:
    """Determine review verdict using 8-scenario decision table (S15).

    Scenarios:
    1. No findings, all agents OK → APPROVE
    2. No findings, agent failed → COMMENT (incomplete data)
    3. Both agents failed → COMMENT (G-28)
    4. Findings present, score >= threshold, no blockers → COMMENT
    5. Findings present, score < threshold → REQUEST_CHANGES
    6. Critical override (critical finding at high confidence) → REQUEST_CHANGES
    7. Agent failed + findings → COMMENT (never REQUEST_CHANGES)
    8. Agent failed + no findings → COMMENT
    """
    has_errors = any(isinstance(o, AgentError) for o in outcomes)
    has_results = any(isinstance(o, AgentResult) for o in outcomes)
    all_failed = not has_results

    # Scenario 3: Both agents failed (G-28)
    if all_failed:
        logger.warning("Both agents failed — verdict COMMENT (G-28)")
        return ReviewVerdict.COMMENT

    # Agent failure safety: never APPROVE or REQUEST_CHANGES
    if has_errors:
        logger.info(
            "Agent failure detected — capping verdict at COMMENT",
        )
        return ReviewVerdict.COMMENT

    # No agent failures from here on
    if not reported and score.finding_count == 0:
        # Scenario 1: Clean review
        return ReviewVerdict.APPROVE

    # Scenario 6: Critical override
    if score.critical_override:
        return ReviewVerdict.REQUEST_CHANGES

    # Scenario 5: Score below threshold
    if score.clamped_score < blocker_threshold:
        return ReviewVerdict.REQUEST_CHANGES

    # Scenario 4: Findings present but above threshold
    return ReviewVerdict.COMMENT
