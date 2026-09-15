"""Stable identity for a finding across reviews (C8).

Each finding is posted as its own comment thread, so a later run has to be
able to recognise the threads it wrote. The identity has to survive the code
moving: a defect on line 12 that becomes line 400 because something was
inserted above it is the same defect, and re-reporting it as new would make
the comment history useless within a few pushes.

So the fingerprint is the file, the check and what the finding says, and
deliberately not the line numbers, the severity or the confidence. Those are
readings of the defect, not the defect.
"""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prbot.review.models import Finding

FINDING_MARKER = "prbot:finding:"

_MARKER_PATTERN = re.compile(
    rf"<!--\s*{re.escape(FINDING_MARKER)}([0-9a-f]{{8,64}})\s*-->",
)

_TITLE_NOISE = re.compile(r"[^a-z0-9 ]+")


def _normalise(text: str) -> str:
    """Reduce a title to comparable words.

    Punctuation and case are not a new defect, so rewording a title slightly
    between runs must not orphan the thread that already discusses it.
    """
    return " ".join(_TITLE_NOISE.sub(" ", text.lower()).split())


def finding_fingerprint(finding: Finding) -> str:
    """A short, stable identifier for this defect."""
    payload = "\x00".join(
        (finding.file_path, finding.check_id, _normalise(finding.title)),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def marker_for(fingerprint: str) -> str:
    """The HTML comment that carries a fingerprint in a comment body.

    Invisible in rendered markdown, which is why the summary comment already
    uses the same technique for its state record.
    """
    return f"<!-- {FINDING_MARKER}{fingerprint} -->"


def extract_fingerprint(body: str) -> str | None:
    """Read a fingerprint back out of a comment body, if it has one."""
    match = _MARKER_PATTERN.search(body or "")
    return match.group(1) if match else None
