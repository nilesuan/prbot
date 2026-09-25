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
from collections.abc import Callable

from prbot.review.prompts import estimate_prompt_tokens
from prbot.vcs.models import FileDiff, PRDiff, PRMetadata

logger = logging.getLogger(__name__)


def _file_tokens(file_diff: FileDiff) -> int:
    """Rough token cost of rendering one file into the prompt."""
    return estimate_prompt_tokens(file_diff.patch or "") + estimate_prompt_tokens(
        file_diff.path,
    )


def needs_chunking(diff: PRDiff, max_tokens: int) -> bool:
    """Whether this diff exceeds what one call should carry."""
    return sum(_file_tokens(f) for f in diff.files) > max_tokens


def chunk_diff(
    diff: PRDiff,
    max_tokens: int,
    size_of: Callable[[FileDiff], int] = _file_tokens,
) -> list[PRDiff]:
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
        tokens = size_of(file_diff)
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


def rendered_file_tokens(
    file_diff: FileDiff,
    *,
    datamark_diff: bool,
    file_contents: dict[str, str] | None,
    context_lines: int,
) -> int:
    """Estimated tokens for one file as it will appear in the prompt.

    The raw patch is not what is sent. Datamarking roughly doubles its
    length, and the surrounding-code excerpt can be many times larger than
    the patch it surrounds, so a limit applied to the patch alone is a limit
    on the wrong thing.
    """
    from prbot.review.prompts import render_file_block

    return estimate_prompt_tokens(
        render_file_block(
            file_diff,
            datamark_diff=datamark_diff,
            file_contents=file_contents,
            context_lines=context_lines,
        ),
    )


def chunk_for_prompt(
    diff: PRDiff,
    max_tokens: int,
    *,
    datamark_diff: bool,
    file_contents: dict[str, str] | None,
    context_lines: int,
    metadata: PRMetadata | None = None,
    all_paths: list[str] | None = None,
) -> list[PRDiff]:
    """Chunk a diff so each piece's prompt fits under max_tokens.

    Given the pull request's metadata, what every chunk repeats is taken off
    the limit first: the header, the description and the list of files shown
    elsewhere (SEC-DESIGN-03). Counting the files alone let each call run
    over the limit by all of that. If it leaves nothing, each file gets a
    chunk of its own.
    """
    limit = max_tokens
    if metadata is not None:
        from prbot.review.prompts import prompt_overhead_tokens

        header = prompt_overhead_tokens(
            metadata, datamark_diff=datamark_diff,
            all_paths=all_paths, truncated=diff.truncated,
        )
        if header >= max_tokens:
            logger.warning(
                "chunk.header tokens=%d leaves nothing of the %d limit; "
                "one file per chunk", header, max_tokens,
            )
        limit = max(1, max_tokens - header)
    return chunk_diff(
        diff,
        limit,
        size_of=lambda f: rendered_file_tokens(
            f,
            datamark_diff=datamark_diff,
            file_contents=file_contents,
            context_lines=context_lines,
        ),
    )
