"""Hallucination validation (story-6-2).

Post-processing defense: validates findings against actual diff content.
Removes findings for non-existent files, penalizes findings referencing
lines outside changed ranges.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace

from prbot.review.models import Finding
from prbot.vcs.models import PRDiff

logger = logging.getLogger(__name__)

# Confidence penalty for findings referencing lines outside changed ranges
# The most confidence a finding can lose for citing lines the agent was
# never shown. It is charged in full only well beyond the context window;
# see _distance_penalty.
HALLUCINATION_PENALTY = 40


def _build_line_index(diff: PRDiff) -> dict[str, set[int] | None]:
    """Map each file to the new-side line numbers its hunks cover (B4).

    The index previously held added lines only, so a finding about a line
    the diff deletes, or about the context around a change, found no overlap
    and was penalised. "This pull request removes the authorisation check"
    is exactly the kind of finding that should survive.

    A hunk's new-side span covers added lines, context lines, and the
    position where a deletion happened, which is the only new-side line
    number a deletion can be described by. Findings outside every hunk are
    still outside the reviewed region.

    Returns None for a file whose patch declares no hunks, meaning there is
    no basis to judge its line numbers either way.
    """
    index: dict[str, set[int] | None] = {}

    for file_diff in diff.files:
        if not file_diff.patch:
            index[file_diff.path] = None
            continue

        covered: set[int] = set()
        saw_hunk = False

        for raw_line in file_diff.patch.split("\n"):
            hunk_match = re.match(
                r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@",
                raw_line,
            )
            if not hunk_match:
                continue
            saw_hunk = True
            start = int(hunk_match.group(1))
            count = (
                int(hunk_match.group(2))
                if hunk_match.group(2) is not None
                else 1
            )
            # A zero-length new side still marks the position of a pure
            # deletion, so keep at least that one line addressable.
            covered.update(range(start, start + max(count, 1)))

        index[file_diff.path] = covered if saw_hunk else None

    return index


def _distance_penalty(gap: int, context_lines: int) -> int:
    """Confidence to remove for citing a line `gap` lines outside a hunk.

    The flat penalty this replaces was 40 points for any gap at all, which
    is larger than the whole borderline band and enough on its own to drive
    a 90%-confidence finding below the reporting threshold. It was charged
    even when the agent had been shown the line: `context_lines` puts the
    enclosing declaration in the prompt, so a finding a few lines outside a
    hunk is reasoning about code the agent actually read.

    The rule follows from that. Inside the window the agent was given the
    code, so nothing is charged. Beyond it the finding cites code the agent
    never saw, and the charge rises with how far outside it went, reaching
    the full penalty one window further out.

    With context off the window is empty and any gap is charged in full,
    which is the behaviour that shipped before.
    """
    if gap <= context_lines:
        return 0
    beyond = gap - context_lines
    scale = max(context_lines, 1)
    return min(HALLUCINATION_PENALTY, round(HALLUCINATION_PENALTY * beyond / scale))


def validate_findings_against_diff(
    findings: list[Finding],
    diff: PRDiff,
    context_lines: int = 0,
) -> list[Finding]:
    """Validate findings against diff content (S7 Layer 4).

    - Removes findings referencing non-existent files
    - Reduces confidence for lines outside the region the agent was shown,
      in proportion to how far outside they fall

    Returns a new list of validated findings.
    """
    line_index = _build_line_index(diff)
    diff_files = {f.path for f in diff.files}
    validated: list[Finding] = []

    for finding in findings:
        # Check file existence
        if finding.file_path not in diff_files:
            logger.warning(
                "Removing hallucinated finding for non-existent file: %s "
                "(S28)",
                finding.file_path,
            )
            continue

        # Check line range overlap with the hunks the diff actually covers
        changed_lines = line_index.get(finding.file_path)
        if changed_lines is None:
            # No hunk headers, so no basis to judge the line numbers.
            validated.append(finding)
            continue

        finding_lines = set(
            range(finding.line_start, finding.line_end + 1),
        )

        if not finding_lines & changed_lines:
            # Distance to the nearest reviewed line, which with several
            # hunks need not be the first or the last of them.
            gap = min(
                min(
                    abs(finding.line_start - covered),
                    abs(finding.line_end - covered),
                )
                for covered in changed_lines
            ) if changed_lines else HALLUCINATION_PENALTY
            penalty = _distance_penalty(gap, context_lines)
            if penalty:
                new_confidence = max(0, finding.confidence - penalty)
                logger.warning(
                    "Penalizing finding %s: lines %d-%d are %d lines outside "
                    "the reviewed region (confidence %d → %d)",
                    finding.id,
                    finding.line_start,
                    finding.line_end,
                    gap,
                    finding.confidence,
                    new_confidence,
                )
                finding = replace(finding, confidence=new_confidence)

        validated.append(finding)

    return validated
