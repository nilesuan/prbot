"""Split an oversized diff into reviewable pieces (C6).

A diff over max_diff_tokens raised DiffTooLargeError and the run exited 2, so
the largest pull requests, the ones most worth reviewing, got no review at
all. A single call carrying the full limit also spreads the model's attention
across every file in it.

The boundary is max_diff_tokens itself. That number is already configured and
is already the point at which the pipeline refused, so chunking introduces no
new threshold to calibrate. Cost stays bounded by budget_limit_usd, which is
checked across every chunk before any call is made.

Files are packed greedily in their original order. Order is worth keeping:
the platforms return files in a stable order that usually groups a module
with its tests, and a chunk containing both reviews better than one that
splits them.
"""

from __future__ import annotations

import logging

from prbot.review.prompts import estimate_prompt_tokens
from prbot.vcs.models import FileDiff, PRDiff

logger = logging.getLogger(__name__)


def _file_tokens(file_diff: FileDiff) -> int:
    """Rough token cost of rendering one file into the prompt."""
    return estimate_prompt_tokens(file_diff.patch or "") + estimate_prompt_tokens(
        file_diff.path,
    )


def needs_chunking(diff: PRDiff, max_tokens: int) -> bool:
    """Whether this diff exceeds what one call should carry."""
    return sum(_file_tokens(f) for f in diff.files) > max_tokens


def chunk_diff(diff: PRDiff, max_tokens: int) -> list[PRDiff]:
    """Split a diff into pieces, each at or under max_tokens where possible.

    A file larger than the limit on its own gets a chunk to itself rather
    than being dropped or truncated: a partial review of a large file is
    worth more than no review of it, and truncating a patch would hand the
    model a diff whose line numbers no longer mean anything.
    """
    if not diff.files:
        return []

    chunks: list[list[FileDiff]] = []
    current: list[FileDiff] = []
    current_tokens = 0

    for file_diff in diff.files:
        tokens = _file_tokens(file_diff)
        if current and current_tokens + tokens > max_tokens:
            chunks.append(current)
            current = []
            current_tokens = 0
        current.append(file_diff)
        current_tokens += tokens

    if current:
        chunks.append(current)

    if len(chunks) > 1:
        logger.info(
            "diff.chunked files=%d chunks=%d limit=%d",
            len(diff.files), len(chunks), max_tokens,
        )

    return [
        PRDiff(
            files=files,
            head_sha=diff.head_sha,
            base_sha=diff.base_sha,
            truncated=diff.truncated,
        )
        for files in chunks
    ]
