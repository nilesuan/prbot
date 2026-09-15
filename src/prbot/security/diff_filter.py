"""Diff filtering (story-6-3).

Removes binary files, generated/lock files, and user-configured
exclusion patterns before sending to review agents.
Returns a new PRDiff without modifying the original.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import PurePosixPath

import pathspec

from prbot.exceptions import ConfigError
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

# Generated file patterns, in gitignore syntax (A2).
#
# These are matched by pathspec using git's own wildmatch rules, not
# PurePosixPath.match. The distinction matters: PurePosixPath.match compares
# from the right and treats ** as a single segment, so "**/node_modules/*"
# matched neither "node_modules/pkg/i.js" nor "web/node_modules/a/b.js".
#
# Under gitignore rules a pattern with no separator matches at any depth, a
# leading "**/" matches in every directory including the root, and a trailing
# "/**" matches the whole subtree.
GENERATED_PATTERNS: list[str] = [
    # Lock files — no separator, so these match at any depth
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
    # Generated directories, at the root or nested at any depth
    "**/__snapshots__/**",
    "**/node_modules/**",
    "**/.next/**",
    "**/dist/**",
    "**/build/**",
    "**/coverage/**",
    "**/__generated__/**",
    "**/generated/**",
    "**/auto_generated/**",
    # Minified files
    "*.min.js",
    "*.min.css",
    # Source maps
    "*.map",
]


@lru_cache(maxsize=32)
def _compile(patterns: tuple[str, ...]) -> pathspec.PathSpec:
    """Compile gitignore-style patterns, surfacing malformed ones.

    A pattern that fails to compile must not be dropped: silently matching
    nothing is exactly the failure this module is being fixed for.
    """
    try:
        return pathspec.PathSpec.from_lines("gitwildmatch", patterns)
    except Exception as exc:  # pathspec raises several pattern error types
        raise ConfigError(
            f"Invalid exclusion pattern in {list(patterns)!r}: {exc}"
        ) from exc


def is_binary_file(file_diff: FileDiff) -> bool:
    """Detect binary files by extension, empty patch, or marker (S49).

    An empty patch alone is not proof of a binary file. GitHub omits the
    patch for text files above its size limit while still reporting line
    counts, so an empty patch is only treated as binary when no lines
    changed either. Otherwise a large source file would disappear from the
    review counted as binary.
    """
    path = PurePosixPath(file_diff.path)
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return True
    if "Binary files differ" in (file_diff.patch or ""):
        return True
    if file_diff.patch or file_diff.status == "removed":
        return False
    if file_diff.additions or file_diff.deletions:
        logger.warning(
            "No patch for %s despite %d additions and %d deletions; the "
            "host likely omitted it for size. Reviewing without content.",
            file_diff.path,
            file_diff.additions,
            file_diff.deletions,
        )
        return False
    return True


def is_generated_file(path: str) -> bool:
    """Check if a file matches generated file patterns (NG-23, A2)."""
    return _compile(tuple(GENERATED_PATTERNS)).match_file(path)


def _matches_exclusion(
    path: str,
    exclusion_patterns: list[str],
) -> bool:
    """Check if a file matches user-configured exclusion patterns (G4-08).

    Patterns use gitignore syntax, which is what a repository-level list of
    excluded paths is expected to mean.
    """
    if not exclusion_patterns:
        return False
    return _compile(tuple(exclusion_patterns)).match_file(path)


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
