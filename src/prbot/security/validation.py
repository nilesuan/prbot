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


def _build_line_index(diff: PRDiff) -> dict[str, set[int]]:
    """Parse each file's patch to extract new-side changed line numbers.

    Returns dict mapping file path to set of changed line numbers.
    """
    index: dict[str, set[int]] = {}

    for file_diff in diff.files:
        lines: set[int] = set()

        if not file_diff.patch:
            index[file_diff.path] = lines
            continue

        current_line = 0
        for raw_line in file_diff.patch.split("\n"):
            # Parse hunk header: @@ -old_start,old_count +new_start,new_count @@
            hunk_match = re.match(
                r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@",
                raw_line,
            )
            if hunk_match:
                current_line = int(hunk_match.group(1))
                continue

            if raw_line.startswith("+") and not raw_line.startswith("+++"):
                lines.add(current_line)
                current_line += 1
            elif raw_line.startswith("-") and not raw_line.startswith("---"):
                # Removed lines don't advance new-side counter
                pass
            else:
                # Context line
                current_line += 1

        index[file_diff.path] = lines

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

        # Check line range overlap with changed lines
        changed_lines = line_index.get(finding.file_path, set())
        finding_lines = set(
            range(finding.line_start, finding.line_end + 1),
        )

        if changed_lines and not finding_lines & changed_lines:
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
