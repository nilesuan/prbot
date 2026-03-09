"""Confidence-banded scoring (story-5-1).

Classifies findings into hidden/borderline/reported bands,
applies weighted severity deductions, and detects critical overrides.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from prbot.review.models import AgentError, AgentOutcome, AgentResult, Finding

logger = logging.getLogger(__name__)

# Severity weights for score deductions (S54)
SEVERITY_WEIGHTS: dict[str, float] = {
    "critical": 25.0,
    "high": 15.0,
    "medium": 8.0,
    "low": 3.0,
    "info": 0.0,
}

ConfidenceBand = Literal["reported", "borderline", "hidden"]


@dataclass(frozen=True)
class ScoredFinding:
    """A finding with its confidence band and score deduction."""

    finding: Finding
    band: ConfidenceBand
    deduction: float

    @classmethod
    def from_finding(
        cls,
        finding: Finding,
        threshold: int,
    ) -> ScoredFinding:
        """Classify a finding and calculate its deduction."""
        band = classify_confidence_band(finding.confidence, threshold)
        weight = SEVERITY_WEIGHTS.get(finding.severity, 0.0)
        deduction = weight * (finding.confidence / 100.0) if band == "reported" else 0.0
        return cls(finding=finding, band=band, deduction=deduction)


@dataclass(frozen=True)
class ReviewScore:
    """Aggregated review score."""

    raw_score: float
    clamped_score: int
    total_deductions: float
    finding_count: int
    critical_override: bool


def classify_confidence_band(
    confidence: int,
    threshold: int,
) -> ConfidenceBand:
    """Classify a finding into a confidence band (S13, S31).

    - reported: confidence >= threshold
    - borderline: threshold - 15 <= confidence < threshold
    - hidden: confidence < threshold - 15
    """
    if confidence >= threshold:
        return "reported"
    if confidence >= threshold - 15:
        return "borderline"
    return "hidden"


def score_findings(
    outcomes: list[AgentOutcome],
    threshold: int = 70,
    blocker_threshold: int = 80,
) -> tuple[list[ScoredFinding], list[ScoredFinding], int, ReviewScore]:
    """Score all findings from agent outcomes (G4-04).

    Args:
        outcomes: Agent results/errors.
        threshold: Confidence threshold for reported band.
        blocker_threshold: Confidence threshold for critical override.

    Returns:
        (reported, borderline, hidden_count, score) tuple.
    """
    reported: list[ScoredFinding] = []
    borderline: list[ScoredFinding] = []
    hidden_count = 0
    critical_override = False

    for outcome in outcomes:
        if isinstance(outcome, AgentError):
            continue
        if not isinstance(outcome, AgentResult):
            continue

        for finding in outcome.findings:
            scored = ScoredFinding.from_finding(finding, threshold)

            if scored.band == "reported":
                reported.append(scored)
                # Check critical override
                if (
                    finding.severity == "critical"
                    and finding.confidence >= blocker_threshold
                ):
                    critical_override = True
            elif scored.band == "borderline":
                borderline.append(scored)
            else:
                hidden_count += 1

    total_deductions = sum(sf.deduction for sf in reported)
    raw_score = 100.0 - total_deductions

    if critical_override:
        raw_score = 0.0

    clamped = max(0, int(raw_score))

    score = ReviewScore(
        raw_score=raw_score,
        clamped_score=clamped,
        total_deductions=total_deductions,
        finding_count=len(reported) + len(borderline) + hidden_count,
        critical_override=critical_override,
    )

    if hidden_count > 0:
        logger.info(
            "Scoring: %d findings hidden below threshold (G4-04)",
            hidden_count,
        )

    return reported, borderline, hidden_count, score
