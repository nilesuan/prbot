"""Verdict state machine (story-5-2).

Deterministic verdict determination covering 8 scenarios.
Agent failure always downgrades — never APPROVE or REQUEST_CHANGES
with incomplete data.
"""

from __future__ import annotations

import enum
import logging

from prbot.review.models import AgentOutcome, AgentResult
from prbot.review.scorer import ReviewScore, ScoredFinding

logger = logging.getLogger(__name__)


class ReviewVerdict(enum.StrEnum):
    """Review verdict outcomes."""

    APPROVE = "APPROVE"
    COMMENT = "COMMENT"
    REQUEST_CHANGES = "REQUEST_CHANGES"


def has_blocker_findings(
    reported: list[ScoredFinding],
    blocker_confidence: int = 70,
) -> bool:
    """Check if any finding is a blocker (critical/high at high confidence)."""
    return any(
        sf.finding.severity in ("critical", "high")
        and sf.finding.confidence >= blocker_confidence
        for sf in reported
    )


def agents_without_coverage(
    outcomes: list[AgentOutcome],
) -> set[str]:
    """Agents that produced no result at all (GEN-ARCH-01).

    Chunking changed the shape of the outcomes list. It used to hold one
    entry per agent, so any AgentError meant an agent was missing. It now
    holds one entry per agent per chunk, and a transient failure in a single
    chunk is not a missing agent: the other chunks still covered their files.

    An agent has lost coverage only when every one of its attempts failed.
    """
    succeeded: set[str] = set()
    attempted: set[str] = set()
    for outcome in outcomes:
        attempted.add(outcome.agent)
        if isinstance(outcome, AgentResult):
            succeeded.add(outcome.agent)
    return attempted - succeeded


def determine_verdict(
    outcomes: list[AgentOutcome],
    reported: list[ScoredFinding],
    score: ReviewScore,
    blocker_confidence: int = 70,
    min_passing_score: int = 70,
) -> ReviewVerdict:
    """Determine review verdict using a deterministic decision table (S15).

    Two thresholds, deliberately separate (B3):

    - blocker_confidence is a *confidence*. A critical or high finding at or
      above it blocks, however good the rest of the review looks.
    - min_passing_score is a *quality score*. A review below it blocks even
      with no individual blocker, because the weight of findings says so.

    These were previously one value compared against both quantities, and
    has_blocker_findings was written, tested and never called, so the
    documented "no blockers" condition checked nothing.

    Scenarios:
    1. No findings, all agents OK → APPROVE
    2. An agent produced no result in any chunk → COMMENT (never approve
       or reject on partial data)
    3. Both agents failed → COMMENT (G-28)
    4. Critical finding at or above blocker confidence → REQUEST_CHANGES
    5. Critical or high finding at or above blocker confidence →
       REQUEST_CHANGES
    6. Score below the passing mark → REQUEST_CHANGES
    7. Findings present, no blocker, score passes → COMMENT
    """
    # GEN-ARCH-01: cap on an agent that produced nothing anywhere, not on
    # any single failed attempt. With N chunks one throttled call used to
    # discard blocker-grade findings the other chunks had already produced.
    uncovered = agents_without_coverage(outcomes)
    has_errors = bool(uncovered)
    has_results = any(isinstance(o, AgentResult) for o in outcomes)
    all_failed = not has_results

    # Scenario 3: Both agents failed (G-28)
    if all_failed:
        logger.warning("Both agents failed — verdict COMMENT (G-28)")
        return ReviewVerdict.COMMENT

    # Agent failure safety: never APPROVE or REQUEST_CHANGES
    if has_errors:
        logger.info(
            "Agents with no successful pass (%s) — capping verdict at "
            "COMMENT", ", ".join(sorted(uncovered)),
        )
        return ReviewVerdict.COMMENT

    # No agent failures from here on
    if not reported and score.finding_count == 0:
        # Scenario 1: Clean review
        return ReviewVerdict.APPROVE

    # Scenario 4: Critical override
    if score.critical_override:
        logger.info("Critical finding above blocker confidence")
        return ReviewVerdict.REQUEST_CHANGES

    # Scenario 5: Any blocker-grade finding
    if has_blocker_findings(reported, blocker_confidence):
        logger.info(
            "Blocker finding at or above confidence %d", blocker_confidence,
        )
        return ReviewVerdict.REQUEST_CHANGES

    # Scenario 6: Score below the passing mark
    if score.clamped_score < min_passing_score:
        logger.info(
            "Score %d below passing mark %d",
            score.clamped_score, min_passing_score,
        )
        return ReviewVerdict.REQUEST_CHANGES

    # Scenario 7: Findings present, nothing blocking
    return ReviewVerdict.COMMENT
