"""Confidence-banded scoring (story-5-1).

Classifies findings into hidden/borderline/reported bands,
applies weighted severity deductions, and detects critical overrides.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from typing import Literal

from prbot.review.models import AgentOutcome, AgentResult, Finding
from prbot.security.diff_filter import _compile

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

# Most severe first, so a merged finding takes the worst reading.
_SEVERITY_RANK: dict[str, int] = {
    "critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4,
}

_TITLE_NOISE = re.compile(r"[^a-z0-9 ]+")


def _normalise_title(title: str) -> str:
    """Reduce a title to comparable words."""
    return " ".join(_TITLE_NOISE.sub(" ", title.lower()).split())


def _overlaps(a: Finding, b: Finding) -> bool:
    """Whether two findings cover any line in common."""
    return a.line_start <= b.line_end and b.line_start <= a.line_end


def _same_defect(a: Finding, b: Finding) -> bool:
    """Whether two findings describe one defect (B1).

    Same file and overlapping lines is necessary but not sufficient: a line
    can carry both a quality problem and a security problem. What settles it
    is agreement on what the problem is, either the same check or the same
    title. The title test matters because the agents use different check
    prefixes, so one defect seen by both is reported as Q-... by one and
    S-... by the other.
    """
    if a.file_path != b.file_path or not _overlaps(a, b):
        return False
    if a.check_id == b.check_id:
        return True
    return _normalise_title(a.title) == _normalise_title(b.title)


def _merge(a: Finding, b: Finding) -> Finding:
    """Combine two reports of one defect, taking the worse reading."""
    worse = min(a, b, key=lambda f: _SEVERITY_RANK.get(f.severity, 99))
    richer = max((a, b), key=lambda f: len(f.description))
    return replace(
        worse,
        confidence=max(a.confidence, b.confidence),
        description=richer.description,
        suggestion=richer.suggestion or worse.suggestion,
        line_start=min(a.line_start, b.line_start),
        line_end=max(a.line_end, b.line_end),
        reported_by=tuple(sorted(set(a.reported_by) | set(b.reported_by))),
    )


def deduplicate_findings(outcomes: list[AgentOutcome]) -> list[Finding]:
    """Collapse findings that describe the same defect (B1).

    score_findings previously concatenated every agent's findings, so a
    defect both agents noticed was reported twice and deducted twice. Two
    agents overlapping on credentials, input validation and error handling
    is the normal case, not an edge case, and it moved verdicts.
    """
    merged: list[Finding] = []
    duplicates = 0

    for outcome in outcomes:
        if not isinstance(outcome, AgentResult):
            continue
        for finding in outcome.findings:
            candidate = finding
            if not candidate.reported_by:
                candidate = replace(
                    candidate, reported_by=(outcome.agent,),
                )
            for i, existing in enumerate(merged):
                if _same_defect(existing, candidate):
                    merged[i] = _merge(existing, candidate)
                    duplicates += 1
                    break
            else:
                merged.append(candidate)

    if duplicates:
        logger.info(
            "Scoring: merged %d duplicate finding(s) across agents (B1)",
            duplicates,
        )
    return merged


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
    blocker_threshold: int = 70,
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

    for finding in deduplicate_findings(outcomes):
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


_SEVERITY_ORDER: list[str] = ["critical", "high", "medium", "low", "info"]


def _rule_matches(rule: object, finding: Finding) -> bool:
    """Whether a suppression rule covers a finding (C4)."""
    check_id = getattr(rule, "check_id", "")
    if not finding.check_id.startswith(check_id):
        return False

    ceiling = getattr(rule, "max_severity", None)
    if ceiling is not None:
        # A rule written to silence nits must not also silence a critical
        # that happens to share the check family.
        try:
            if _SEVERITY_ORDER.index(finding.severity) < _SEVERITY_ORDER.index(
                ceiling,
            ):
                return False
        except ValueError:
            return False

    path = getattr(rule, "path", None)
    if not path:
        return True
    return _compile((path,)).match_file(finding.file_path)


def apply_suppressions(
    findings: list[Finding],
    rules: list[object],
) -> tuple[list[Finding], list[tuple[Finding, str]]]:
    """Split findings into those to report and those suppressed (C4).

    Returns (kept, [(finding, reason), ...]). Suppressed findings are
    returned rather than discarded so the comment can say how many were
    hidden and the audit record can carry the number: a suppression list
    nobody can see is how a review bot becomes decorative.
    """
    if not rules:
        return list(findings), []

    kept: list[Finding] = []
    suppressed: list[tuple[Finding, str]] = []

    for finding in findings:
        for rule in rules:
            if _rule_matches(rule, finding):
                suppressed.append((finding, getattr(rule, "reason", "")))
                logger.info(
                    "Suppressed %s in %s: %s",
                    finding.check_id,
                    finding.file_path,
                    getattr(rule, "reason", ""),
                )
                break
        else:
            kept.append(finding)

    return kept, suppressed
