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

from prbot.review.identity import extract_fingerprint, finding_fingerprint

if TYPE_CHECKING:
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
) -> OutcomeReport:
    """Match this run's findings against the threads a previous run left.

    Threads without prbot's marker are ignored entirely. Human review threads
    are none of prbot's business, and touching one would be the fastest way
    to make a team turn the bot off.
    """
    ours: dict[str, ReviewThread] = {}
    for thread in threads:
        fingerprint = extract_fingerprint(thread.body)
        if fingerprint is not None:
            ours[fingerprint] = thread

    new: list[ScoredFinding] = []
    persisting: list[tuple[ScoredFinding, ReviewThread]] = []
    human_resolved: list[ReviewThread] = []
    seen: set[str] = set()

    for scored in reported:
        fingerprint = finding_fingerprint(scored.finding)
        seen.add(fingerprint)
        thread = ours.get(fingerprint)
        if thread is None:
            new.append(scored)
        elif thread.resolved:
            # Someone decided this one is settled. Re-raising it is how a
            # review bot teaches people to ignore it.
            human_resolved.append(thread)
        else:
            persisting.append((scored, thread))

    fixed = [
        thread
        for fingerprint, thread in ours.items()
        if fingerprint not in seen and not thread.resolved
    ]

    report = OutcomeReport(
        new=new,
        persisting=persisting,
        fixed=fixed,
        human_resolved=human_resolved,
    )
    logger.info("outcomes.reconciled %s", report.counts())
    return report
