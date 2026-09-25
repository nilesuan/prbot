"""Read-only access to the head revision beyond the diff (read_file tool).

The diff is rarely where the deciding fact lives. On the merge requests a
verified review judged most harshly, what settled the largest findings was a
route table defined in another file of the same module and a CI file listing
which test files run. An agent that sees only the diff cannot establish
either, so it guesses or stays silent.

The reader gives an agent a bounded way to look. It reads through the same
VCS API the pipeline already uses for context, at the head revision only, and
refuses anything review is not allowed to see: paths that escape the
repository, and files the diff filter would have dropped (binary, generated,
or excluded by configuration). Content is datamarked, because repository
content is exactly as untrusted as the diff.

The limits bound what a single agent can add to its own context. They are
sized to the facts that motivated the tool - a resource block, a job
definition, a list of test paths - which run to tens of lines, not hundreds.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from pathlib import PurePosixPath

READ_FILE_TOOL_NAME = "read_file"

# One read: comfortably more than a resource block or a CI job, well short
# of a whole large file.
MAX_LINES_PER_READ = 200
# Everything one agent may read across its turns. Three full reads.
MAX_LINES_TOTAL = 600
# Longer than any real repository path; a longer one is not a path.
_MAX_PATH_LENGTH = 512
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def read_file_tool_spec() -> dict[str, object]:
    """The Converse tool definition for read_file."""
    return {
        "toolSpec": {
            "name": READ_FILE_TOOL_NAME,
            "description": (
                "Read lines of a file in this repository at the head "
                "revision of the pull request, to establish a fact the diff "
                "does not show. Returns numbered lines. Read only what you "
                f"need: at most {MAX_LINES_PER_READ} lines per call and "
                f"{MAX_LINES_TOTAL} in total."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Repository-relative file path.",
                        },
                        "start_line": {"type": "integer", "minimum": 1},
                        "end_line": {"type": "integer", "minimum": 1},
                    },
                    "required": ["path"],
                },
            },
        },
    }


class FileReader:
    """One agent's bounded, read-only view of the head revision."""

    def __init__(
        self,
        fetch: Callable[[str], Awaitable[str | None]],
        *,
        exclusion_patterns: list[str] | None = None,
        cache: dict[str, str | None] | None = None,
        max_lines_per_read: int = MAX_LINES_PER_READ,
        max_lines_total: int = MAX_LINES_TOTAL,
    ) -> None:
        self._fetch = fetch
        self._exclusions = list(exclusion_patterns or [])
        # Shared between agents on purpose: three agents reading the same
        # file should cost one API call, not three.
        self._cache = cache if cache is not None else {}
        self._per_read = max_lines_per_read
        self._remaining = max_lines_total
        self.lines_read = 0

    async def read(
        self,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        """Numbered, datamarked lines of `path`, or a reason it was refused."""
        refusal = self._refusal(path)
        if refusal:
            return f"Refused: {refusal}"

        if self._remaining <= 0:
            return (
                "Refused: the read budget for this review is used up. "
                "Report your findings with what you have."
            )

        if path not in self._cache:
            self._cache[path] = await self._fetch(path)
        content = self._cache[path]
        if content is None:
            return f"Not found: {path} does not exist at the head revision."

        lines = content.split("\n")
        total = len(lines)
        first = max(1, start_line or 1)
        if first > total:
            return f"{path} has {total} lines; line {first} is past the end."
        last = end_line if end_line and end_line >= first else total
        last = min(last, total, first + self._per_read - 1,
                   first + self._remaining - 1)

        from prbot.security.datamarking import apply_datamarking

        width = len(str(last))
        body = "\n".join(
            f"{n:>{width}} {apply_datamarking(lines[n - 1])}"
            for n in range(first, last + 1)
        )
        count = last - first + 1
        self._remaining -= count
        self.lines_read += count
        return f"{path} lines {first}-{last} of {total}:\n{body}"

    def _refusal(self, path: str) -> str | None:
        """Why `path` may not be read, or None if it may."""
        from prbot.security.diff_filter import (
            BINARY_EXTENSIONS,
            _matches_exclusion,
            is_generated_file,
        )

        if not path or len(path) > _MAX_PATH_LENGTH or _CONTROL.search(path):
            return "not a usable repository path."
        if path.startswith("/") or "\\" in path:
            return "paths are repository-relative."
        if ".." in PurePosixPath(path).parts:
            return "paths may not leave the repository."
        if PurePosixPath(path).suffix.lower() in BINARY_EXTENSIONS:
            return "binary files are not reviewed."
        if is_generated_file(path):
            return "generated files are not reviewed."
        if self._exclusions and _matches_exclusion(path, self._exclusions):
            return "this path is excluded from review by configuration."
        return None
