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


def validate_findings_against_diff(
    findings: list[Finding],
    diff: PRDiff,
) -> list[Finding]:
    """Validate findings against diff content (S7 Layer 4).

    - Removes findings referencing non-existent files
    - Penalizes findings for lines outside changed ranges
      by reducing confidence by HALLUCINATION_PENALTY

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
            # No overlap — penalize confidence
            new_confidence = max(0, finding.confidence - HALLUCINATION_PENALTY)
            logger.warning(
                "Penalizing finding %s: lines %d-%d not in changed "
                "range (confidence %d → %d)",
                finding.id,
                finding.line_start,
                finding.line_end,
                finding.confidence,
                new_confidence,
            )
            finding = replace(finding, confidence=new_confidence)

        validated.append(finding)

    return validated
