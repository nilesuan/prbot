"""Reconcile this review's findings with the threads a previous one left (C8).

The pull request is the store. Each finding is a comment thread; a fix is a
reply in that thread and the thread resolved. Nothing else has to hold state,
and the record of what happened sits where the people who did it are looking.

Three outcomes, and each means something different for calibration:

- persisting: reported again, not yet acted on. Says nothing either way.
- fixed: the finding is gone from a review of newer code. The author acted.
- human_resolved: someone resolved the thread themselves. The strongest
  acceptance signal available, and the only one that is unambiguous.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from prbot.review.identity import (
    extract_check_id,
    extract_fingerprint,
    finding_fingerprint,
)

if TYPE_CHECKING:
    from prbot.review.models import Finding
    from prbot.review.scorer import ScoredFinding
    from prbot.vcs.models import ReviewThread

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutcomeReport:
    """What happened to each finding since the last review."""

    new: list[ScoredFinding] = field(default_factory=list)
    persisting: list[tuple[ScoredFinding, ReviewThread]] = field(
        default_factory=list,
    )
    fixed: list[ReviewThread] = field(default_factory=list)
    human_resolved: list[ReviewThread] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        """The shape a metrics sink wants."""
        return {
            "findings_new": len(self.new),
            "findings_persisting": len(self.persisting),
            "findings_fixed": len(self.fixed),
            "findings_human_resolved": len(self.human_resolved),
        }


def reconcile(
    reported: list[ScoredFinding],
    threads: list[ReviewThread],
    bot_user: str = "",
) -> OutcomeReport:
    """Match this run's findings against the threads a previous run left.

    Threads without prbot's marker are ignored entirely. Human review threads
    are none of prbot's business, and touching one would be the fastest way
    to make a team turn the bot off.

    SEC-AUTH-02: the marker is not identity. Anyone who can comment on the
    pull request can paste it, and a forged thread would otherwise be taken
    as prbot's own: a marker matching a live finding suppressed that finding
    as already-reported, and one matching nothing drew a 'no longer reported'
    reply and a resolve. A thread counts as ours only when its first comment
    was written by the authenticated user.

    When bot_user is empty the identity could not be established, so the
    marker is used alone rather than discarding all prior state, which would
    re-post every finding on the pull request. Findings are then matched by
    exact fingerprint only (SEC-AUTH-01).
    """
    ours: dict[str, ReviewThread] = {}
    ignored = 0
    for thread in threads:
        fingerprint = extract_fingerprint(thread.body)
        if fingerprint is None:
            continue
        if bot_user and thread.author.lower() != bot_user.lower():
            ignored += 1
            continue
        ours[fingerprint] = thread

    if ignored:
        logger.warning(
            "Ignored %d thread(s) carrying prbot's finding marker but "
            "written by another author",
            ignored,
        )

    new: list[ScoredFinding] = []
    persisting: list[tuple[ScoredFinding, ReviewThread]] = []
    human_resolved: list[ReviewThread] = []
    claimed: set[str] = set()

    # Exact fingerprints first, so a thread goes to the finding it was
    # written for before any reworded report can take it.
    matches: list[tuple[ScoredFinding, ReviewThread | None]] = []
    for scored in reported:
        fingerprint = finding_fingerprint(scored.finding)
        thread = ours.get(fingerprint)
        if thread is not None:
            claimed.add(fingerprint)
        matches.append((scored, thread))

    # SEC-AUTH-01: a reworded finding is matched on the check id and line a
    # thread carries, and whoever wrote the thread chose those. Only a thread
    # whose author was checked against our own identity is trusted with it.
    # With GITHUB_TOKEN that identity is unavailable, and a forged thread
    # naming a finding's check and line would otherwise take the finding.
    if bot_user:
        _match_reworded(matches, ours, claimed)

    for scored, thread in matches:
        if thread is None:
            new.append(scored)
        elif thread.resolved:
            # Someone decided this one is settled. Re-raising it is how a
            # review bot teaches people to ignore it.
            human_resolved.append(thread)
        else:
            persisting.append((scored, thread))

    # A thread nothing claimed is fixed, unless a finding of its check still
    # covers its line. That finding went to a nearer thread, and the defect
    # this one is about may be the one still being reported. Without an
    # identity no finding is matched this way, so nothing is held back.
    fixed = [
        thread
        for fingerprint, thread in ours.items()
        if fingerprint not in claimed
        and not thread.resolved
        and not (
            bot_user
            and any(_covers(sf.finding, thread) for sf in reported)
        )
    ]

    report = OutcomeReport(
        new=new,
        persisting=persisting,
        fixed=fixed,
        human_resolved=human_resolved,
    )
    logger.info("outcomes.reconciled %s", report.counts())
    return report


def _match_reworded(
    matches: list[tuple[ScoredFinding, ReviewThread | None]],
    ours: dict[str, ReviewThread],
    claimed: set[str],
) -> None:
    """Give each unmatched finding the unclaimed open thread nearest it.

    The fingerprint hashes the title and the model rewords titles between
    runs, so the same defect on the same lines can arrive under a new
    fingerprint and would otherwise open a second thread. A thread is taken
    to be about a finding when _covers says so, which is the scorer's rule
    for two reports of one check being one defect, applied to a report and a
    thread. A thread whose code has since moved away from its anchor no
    longer matches, and the finding is posted as new.

    Pairs are taken nearest first, measured from the finding's last line,
    where a finding is anchored, then narrowest finding first
    (SEC-INTEG-01). Taking the first thread that fit made the outcome depend
    on the order findings and threads arrived in, and let a wide finding
    take a narrow one's thread.

    A resolved thread is never taken (SEC-DESIGN-01). File, check and line
    cannot tell a reworded report of the defect someone resolved from a new
    defect of the same check on the same line, and taking it would file the
    new one under their decision without showing it to anyone. Only an exact
    fingerprint carries a resolution forward.
    """
    pairs = sorted(
        (
            abs(scored.finding.line_end - thread.line),
            scored.finding.line_end - scored.finding.line_start,
            i,
            fingerprint,
        )
        for i, (scored, matched) in enumerate(matches)
        if matched is None
        for fingerprint, thread in ours.items()
        if fingerprint not in claimed
        and not thread.resolved
        and _covers(scored.finding, thread)
    )
    for _distance, _width, i, fingerprint in pairs:
        scored, matched = matches[i]
        if matched is None and fingerprint not in claimed:
            claimed.add(fingerprint)
            matches[i] = (scored, ours[fingerprint])


def _covers(finding: Finding, thread: ReviewThread) -> bool:
    """Whether a finding is about the line a thread of ours is anchored on.

    Same file, the same check named in the thread's posted header, and the
    finding's lines include the thread's anchor.
    """
    return (
        thread.line is not None
        and thread.path == finding.file_path
        and extract_check_id(thread.body) == finding.check_id
        and finding.line_start <= thread.line <= finding.line_end
    )
