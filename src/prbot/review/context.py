"""Expanded file context for review prompts (B8).

The model sees hunks and nothing else. A hunk carries three lines of context
by default, which is rarely the enclosing function, so a judgement about
architecture, testing or maintainability is being made without the thing being
judged. This is the standard ceiling on diff-only reviewers and the most
common source of their false positives.

Whether paying for the surrounding lines raises precision enough to justify
the tokens is a question for measurement, not argument, so context_lines
defaults to 0 and the capability exists to make the experiment runnable.
"""

from __future__ import annotations

import re

from prbot.vcs.models import FileDiff

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def hunk_span(patch: str) -> tuple[int, int] | None:
    """New-side line range covered by a patch, first hunk to last.

    Returns None when the patch declares no hunks, which is the case for a
    binary file or a patch the host omitted.
    """
    starts: list[int] = []
    ends: list[int] = []

    for line in patch.split("\n"):
        match = _HUNK.match(line)
        if not match:
            continue
        start = int(match.group(1))
        count = int(match.group(2)) if match.group(2) is not None else 1
        starts.append(start)
        # Inclusive: a hunk starting at 10 with 4 lines ends at 13.
        ends.append(start + max(count, 1) - 1)

    if not starts:
        return None
    return min(starts), max(ends)


def build_context_excerpt(
    file_diff: FileDiff,
    content: str | None,
    context_lines: int,
) -> str:
    """Numbered excerpt of the head revision around the change.

    Line numbers are included because a finding cites them, and because the
    hallucination check then measures the finding against the same numbers.
    Content is datamarked: it is file content from the pull request and is
    exactly as untrusted as the patch.
    """
    if context_lines <= 0 or not content or not file_diff.patch:
        return ""

    span = hunk_span(file_diff.patch)
    if span is None:
        return ""

    from prbot.security.datamarking import apply_datamarking

    lines = content.split("\n")
    first = max(1, span[0] - context_lines)
    last = min(len(lines), span[1] + context_lines)

    width = len(str(last))
    return "\n".join(
        f"{number:>{width}} {apply_datamarking(lines[number - 1])}"
        for number in range(first, last + 1)
    )
