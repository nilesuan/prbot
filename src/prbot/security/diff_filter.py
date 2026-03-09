"""Diff filtering (story-6-3).

Removes binary files, generated/lock files, and user-configured
exclusion patterns before sending to review agents.
Returns a new PRDiff without modifying the original.
"""

from __future__ import annotations

import logging
from pathlib import PurePosixPath

from prbot.vcs.models import FileDiff, PRDiff

logger = logging.getLogger(__name__)

# Binary file extensions (S49)
BINARY_EXTENSIONS: frozenset[str] = frozenset({
    # Images
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp",
    ".tiff", ".tif",
    # Fonts
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    # Archives
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    # Binary
    ".exe", ".dll", ".so", ".dylib", ".bin", ".o", ".a", ".lib",
    ".pyc", ".pyo", ".class", ".jar", ".war",
    # Media
    ".mp3", ".mp4", ".avi", ".mov", ".wav", ".flac", ".ogg",
    ".mkv", ".webm",
    # Database
    ".sqlite", ".db", ".sqlite3",
    # Documents
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
})

# Generated file patterns — with **/ prefix for directory-scoped matching (NG-23)
GENERATED_PATTERNS: list[str] = [
    # Lock files
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "Gemfile.lock",
    "composer.lock",
    "go.sum",
    "uv.lock",
    # Generated directories
    "**/__snapshots__/*",
    "**/node_modules/*",
    "**/.next/*",
    "**/dist/*",
    "**/build/*",
    "**/coverage/*",
    # Minified files
    "*.min.js",
    "*.min.css",
    # Source maps
    "*.map",
    # Generated code markers
    "**/__generated__/*",
    "**/generated/*",
    "**/auto_generated/*",
]


def is_binary_file(file_diff: FileDiff) -> bool:
    """Detect binary files by extension, empty patch, or marker (S49)."""
    path = PurePosixPath(file_diff.path)
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return True
    if not file_diff.patch and file_diff.status != "removed":
        return True
    return "Binary files differ" in (file_diff.patch or "")


def is_generated_file(path: str) -> bool:
    """Check if a file matches generated file patterns (NG-23)."""
    posix_path = PurePosixPath(path)
    return any(posix_path.match(pattern) for pattern in GENERATED_PATTERNS)


def _matches_exclusion(
    path: str,
    exclusion_patterns: list[str],
) -> bool:
    """Check if a file matches user-configured exclusion patterns (G4-08)."""
    posix_path = PurePosixPath(path)
    return any(
        posix_path.match(pattern) for pattern in exclusion_patterns
    )


def filter_diff(
    diff: PRDiff,
    exclusion_patterns: list[str] | None = None,
) -> PRDiff:
    """Filter diff to remove binary, generated, and excluded files (S49, S55).

    Returns a new PRDiff. Does not modify the original.
    """
    exclusions = exclusion_patterns or []
    filtered_files: list[FileDiff] = []
    binary_count = 0
    generated_count = 0
    excluded_count = 0

    for file_diff in diff.files:
        if is_binary_file(file_diff):
            binary_count += 1
            continue
        if is_generated_file(file_diff.path):
            generated_count += 1
            continue
        if exclusions and _matches_exclusion(file_diff.path, exclusions):
            excluded_count += 1
            continue
        filtered_files.append(file_diff)

    total_removed = binary_count + generated_count + excluded_count
    if total_removed > 0:
        logger.info(
            "Diff filtering: removed %d files "
            "(binary=%d, generated=%d, excluded=%d)",
            total_removed,
            binary_count,
            generated_count,
            excluded_count,
        )

    return PRDiff(
        files=filtered_files,
        head_sha=diff.head_sha,
        base_sha=diff.base_sha,
        truncated=diff.truncated,
    )
