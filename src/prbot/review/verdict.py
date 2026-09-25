"""Verdict state machine (story-5-2).

Deterministic verdict determination covering 8 scenarios.
Agent failure never approves: incomplete data can show a problem, never
its absence.
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

    Incomplete data can show a problem but not its absence (SEC-DESIGN-04).
    A blocker, or a score already below the passing mark, stands however many
    agent passes failed, because the missing passes could only have added
    findings. Only a review every agent completed can be approved.

    Scenarios:
    1. No findings, every agent completed every pass → APPROVE
    2. No findings, an agent pass failed → COMMENT (files it did not review
       were reviewed by fewer agents, or by none)
    3. Both agents failed → COMMENT (G-28)
    4. Critical finding at or above blocker confidence → REQUEST_CHANGES,
       whatever failed
    5. Critical or high finding at or above blocker confidence →
       REQUEST_CHANGES, whatever failed
    6. Score below the passing mark → REQUEST_CHANGES, whatever failed
    7. Findings present, nothing blocking → COMMENT
    """
    has_results = any(isinstance(o, AgentResult) for o in outcomes)
    all_failed = not has_results

    # Scenario 3: Both agents failed (G-28)
    if all_failed:
        logger.warning("Both agents failed; verdict COMMENT (G-28)")
        return ReviewVerdict.COMMENT

    # SEC-DESIGN-04: what was found is checked first. A cap for an agent
    # with no result used to come before these, so a critical finding the
    # other agent confirmed became a COMMENT, exit 0, whenever one agent
    # failed.

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

    # Scenario 2: only a complete review approves. A failed pass, on one
    # chunk (SEC-DESIGN-02) or on all of an agent's (GEN-ARCH-01), left files
    # that agent never saw.
    failed = sum(1 for o in outcomes if not isinstance(o, AgentResult))
    if failed:
        uncovered = agents_without_coverage(outcomes)
        logger.info(
            "%d agent pass(es) failed%s, so the verdict is at most COMMENT",
            failed,
            f" ({', '.join(sorted(uncovered))} on every pass)"
            if uncovered else "",
        )
        return ReviewVerdict.COMMENT

    # Scenario 1: Clean review
    if not reported and score.finding_count == 0:
        return ReviewVerdict.APPROVE

    # Scenario 7: Findings present, nothing blocking
    return ReviewVerdict.COMMENT
