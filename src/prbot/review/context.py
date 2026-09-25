"""Expanded file context for review prompts (B8).

The model sees hunks and nothing else. A hunk carries three lines of context
by default, which is rarely the enclosing function, so a judgement about
architecture, testing or maintainability is being made without the thing being
judged. This is the standard ceiling on diff-only reviewers and the most
common source of their false positives.

context_lines defaults to 40 (see config.py for why). Each hunk gets its own
window of that many lines either side, so the cost grows with the size of the
change rather than with the distance between its first and last hunk.
"""

from __future__ import annotations

import re

from prbot.vcs.models import FileDiff

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def hunk_spans(patch: str) -> list[tuple[int, int]]:
    """New-side line range of each hunk in a patch, in patch order.

    Empty when the patch declares no hunks, which is the case for a binary
    file or a patch the host omitted.
    """
    spans: list[tuple[int, int]] = []

    for line in patch.split("\n"):
        match = _HUNK.match(line)
        if not match:
            continue
        start = int(match.group(1))
        count = int(match.group(2)) if match.group(2) is not None else 1
        # Inclusive: a hunk starting at 10 with 4 lines ends at 13. A
        # zero-length new side still marks where a deletion happened.
        spans.append((start, start + max(count, 1) - 1))

    return spans


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

    spans = hunk_spans(file_diff.patch)
    if not spans:
        return ""

    from prbot.security.datamarking import apply_datamarking

    lines = content.split("\n")
    windows = _windows(spans, context_lines, len(lines))
    if not windows:
        return ""

    width = len(str(windows[-1][1]))
    out: list[str] = []
    for i, (first, last) in enumerate(windows):
        if i:
            # Without a separator the numbering is the only sign that lines
            # were skipped, and a reader skimming 46 then 860 can miss it.
            out.append(f"{'':>{width}} ...")
        out.extend(
            f"{number:>{width}} {apply_datamarking(lines[number - 1])}"
            for number in range(first, last + 1)
        )
    return "\n".join(out)


def _windows(
    spans: list[tuple[int, int]],
    context_lines: int,
    line_count: int,
) -> list[tuple[int, int]]:
    """Each hunk widened by context_lines, clamped to the file, merged.

    A window per hunk rather than one from the first hunk to the last: the
    single range made the excerpt the whole file for any file edited near
    both ends, and that is what a test file gaining one case at the top and
    one at the bottom looks like. It is also the region the hallucination
    check treats as shown, since it measures each finding's distance to the
    nearest hunk. Windows that overlap or touch are merged so no line is
    rendered twice.
    """
    windows: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        first = max(1, start - context_lines)
        last = min(line_count, end + context_lines)
        if first > last:
            continue
        if windows and first <= windows[-1][1] + 1:
            windows[-1] = (windows[-1][0], max(windows[-1][1], last))
        else:
            windows.append((first, last))
    return windows
