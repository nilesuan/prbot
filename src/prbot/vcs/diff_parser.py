"""Unified diff parser with three-coordinate system (story-3-6).

Parses unified diff format to map changed lines to coordinates:
- old_line: line number in the base version
- new_line: line number in the head version
- diff_position: 1-based position within the diff (for GitHub review comments)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Hunk header: @@ -old_start,old_count +new_start,new_count @@
_HUNK_PATTERN = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@"
)

# Platform file limits for truncation detection
_TRUNCATION_LIMITS: dict[str, int] = {
    "github": 3000,
    "gitlab": 1000,  # GitLab overflow threshold
}


@dataclass(frozen=True)
class DiffCoordinate:
    """Position of a changed line in a diff."""

    file_path: str
    old_line: int | None  # None for added lines
    new_line: int | None  # None for deleted lines
    diff_position: int  # 1-based position in the diff


def parse_unified_diff(
    patch: str, file_path: str,
) -> list[DiffCoordinate]:
    """Parse a unified diff patch into line coordinates.

    Args:
        patch: Unified diff text (the patch content for a single file).
        file_path: Path of the file being diffed.

    Returns:
        List of DiffCoordinate for each changed line (additions and deletions).
    """
    if not patch:
        return []

    coordinates: list[DiffCoordinate] = []
    old_line = 0
    new_line = 0
    position = 0  # 1-based diff position

    for line in patch.splitlines():
        position += 1

        hunk_match = _HUNK_PATTERN.match(line)
        if hunk_match:
            old_line = int(hunk_match.group(1))
            new_line = int(hunk_match.group(3))
            continue

        if line.startswith("+"):
            coordinates.append(DiffCoordinate(
                file_path=file_path,
                old_line=None,
                new_line=new_line,
                diff_position=position,
            ))
            new_line += 1
        elif line.startswith("-"):
            coordinates.append(DiffCoordinate(
                file_path=file_path,
                old_line=old_line,
                new_line=None,
                diff_position=position,
            ))
            old_line += 1
        else:
            # Context line
            old_line += 1
            new_line += 1

    return coordinates


def detect_truncation(file_count: int, platform: str) -> bool:
    """Detect if a diff is truncated based on platform file limits.

    Args:
        file_count: Number of files in the diff.
        platform: 'github' or 'gitlab'.

    Returns:
        True if the file count meets or exceeds the platform limit.
    """
    limit = _TRUNCATION_LIMITS.get(platform, 3000)
    return file_count >= limit
