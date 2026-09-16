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

# A finding below the reporting threshold used to deduct exactly nothing, so
# a review in which nothing cleared the threshold scored 100/100 however much
# the agents had found. Confidence is already a multiplier on the deduction;
# this factor is the additional discount for not having cleared the bar.
#
# 0.5 is chosen so that two independent borderline reports of one severity
# cost the same as a single confident report of it, and so that no borderline
# finding can fail a review on its own: the largest possible borderline
# deduction is a critical at the top of the band, 25.0 * 0.69 * 0.5 = 8.6,
# well inside the 30 points between a perfect score and the default passing
# mark. Uncertainty should move the score, not decide the verdict.
BORDERLINE_DEDUCTION_FACTOR = 0.5

# Severities that are never hidden, whatever the confidence attached to them.
#
# The band is a statement about how sure the reviewer is. It is not a licence
# to discard the finding: the cost of silently dropping a real critical is
# unbounded, and the cost of showing a speculative one is that a human spends
# a minute dismissing it. A finding here is surfaced as borderline with its
# confidence printed, so the reader can weigh it, and it is never promoted
# into `reported` - the floor makes a finding visible, not confident.
_ALWAYS_SURFACED: frozenset[str] = frozenset({"critical", "high"})

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


# Words that carry no subject matter, so they must not make two unrelated
# titles look alike.
_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "for",
    "from", "has", "have", "in", "into", "is", "it", "its", "no", "not", "of",
    "on", "or", "that", "the", "their", "then", "there", "this", "to", "was",
    "when", "which", "with", "without",
})

# How much of two titles' combined subject vocabulary must be shared before
# they are taken to name the same thing. At 0.5 more than half the words the
# two titles use between them are common to both, which is a statement about
# the titles rather than about any particular defect.
_TITLE_AGREEMENT = 0.5


def _subject_words(title: str) -> frozenset[str]:
    """The content words of a title."""
    return frozenset(_normalise_title(title).split()) - _STOPWORDS


def _titles_agree(a: str, b: str) -> bool:
    """Whether two titles name the same subject.

    Exact equality is too strict for this. Two agents, or one agent seeing a
    defect in two files, describe it in their own words: "AWS account ID
    hardcoded in CI pipeline" and "Hardcoded AWS account ID exposed in public
    README" are one defect written twice. Comparing the sets of subject words
    recognises that without needing the wording to match.
    """
    left, right = _subject_words(a), _subject_words(b)
    if not left or not right:
        return _normalise_title(a) == _normalise_title(b)
    union = left | right
    return len(left & right) / len(union) >= _TITLE_AGREEMENT


def _same_defect(a: Finding, b: Finding) -> bool:
    """Whether two findings describe one defect (B1, D1).

    What makes two reports one defect is agreement about what the problem is,
    not agreement about where it sits. The previous rule required an exact
    file match and a line overlap before it would look at the problem at all,
    so one defect deducted once per file it appeared in, and twice in a file
    where the agent cited two separate line ranges for it. A hardcoded
    credential in both a CI file and a README is one credential.

    Two cases, and co-location only decides the second:

    - The same check firing twice on the same lines is one defect, whatever
      the two reports called it. Anywhere else, in another file or elsewhere
      in the same one, the titles have to agree, because the same check
      legitimately fires on unrelated subjects in unrelated places.
    - Different checks are one defect only when they are co-located and name
      the same thing. The agents use different prefixes, so one defect seen
      by both is Q-... to one and S-... to the other.
    """
    if a.check_id == b.check_id:
        if a.file_path == b.file_path and _overlaps(a, b):
            return True
        return _titles_agree(a.title, b.title)
    return (
        a.file_path == b.file_path
        and _overlaps(a, b)
        and _normalise_title(a.title) == _normalise_title(b.title)
    )


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


def deduplicate_findings(
    outcomes: list[AgentOutcome],
) -> list[Finding]:
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
        band = classify_confidence_band(
            finding.confidence, threshold, finding.severity,
        )
        weight = SEVERITY_WEIGHTS.get(finding.severity, 0.0)
        full = weight * (finding.confidence / 100.0)
        if band == "reported":
            deduction = full
        elif band == "borderline":
            deduction = full * BORDERLINE_DEDUCTION_FACTOR
        else:
            deduction = 0.0
        return cls(finding=finding, band=band, deduction=deduction)


@dataclass(frozen=True)
class ReviewScore:
    """Aggregated review score."""

    raw_score: float
    clamped_score: int
    total_deductions: float
    finding_count: int
    critical_override: bool
    # How many reports were collapsed into another as one defect (D1).
    merged_count: int = 0


def classify_confidence_band(
    confidence: int,
    threshold: int,
    severity: str | None = None,
) -> ConfidenceBand:
    """Classify a finding into a confidence band (S13, S31, C3).

    - reported: confidence >= threshold
    - borderline: threshold - 15 <= confidence < threshold
    - hidden: confidence < threshold - 15

    A critical or high finding is never hidden. When severity is given and
    names one of those, a band of "hidden" is raised to "borderline" so the
    finding is still shown with its confidence attached. It is deliberately
    not raised to "reported": the reviewer was not confident, and saying
    otherwise would be a different lie from the one this fixes.
    """
    if confidence >= threshold:
        return "reported"
    if confidence >= threshold - 15:
        return "borderline"
    if severity in _ALWAYS_SURFACED:
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

    # How many reports were collapsed into another as one defect. A reader
    # told the agents produced six findings and shown four needs the other
    # two accounted for, so the count is carried into the comment.
    produced = sum(
        len(o.findings) for o in outcomes if isinstance(o, AgentResult)
    )
    deduplicated = deduplicate_findings(outcomes)
    merged_count = produced - len(deduplicated)

    for finding in deduplicated:
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

    # Both bands, because a borderline finding's reduced deduction is only a
    # reduced deduction if it reaches the total. Summing `reported` alone is
    # how the deduction was computed and then discarded, leaving the score at
    # 100 with findings on the page.
    total_deductions = sum(
        sf.deduction for sf in (*reported, *borderline)
    )
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
        merged_count=merged_count,
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
