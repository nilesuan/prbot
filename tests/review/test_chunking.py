"""Tests for reviewing a large diff in pieces (C6).

A diff over max_diff_tokens raised DiffTooLargeError and exited 2, so the
largest pull requests, the ones most worth reviewing, got no review at all.
One call carrying 100,000 tokens of diff also loses attention across files
well before it hits that limit.

The chunk boundary is max_diff_tokens, which is already configured and
already the number the pipeline refused at. No new threshold is introduced,
and cost stays bounded by budget_limit_usd, which is checked across every
chunk before any call is made.
"""

from __future__ import annotations

from prbot.review.chunking import chunk_diff, needs_chunking
from prbot.vcs.models import FileDiff, PRDiff

_HEAD = "abcdef1234567890abcdef1234567890abcdef12"
_BASE = "1234567890abcdef1234567890abcdef12345678"


def _file(path: str, lines: int) -> FileDiff:
    body = "\n".join(f"+line {i} of {path}" for i in range(lines))
    return FileDiff(
        path=path,
        status="modified",
        patch=f"@@ -1,1 +1,{lines} @@\n{body}\n",
        additions=lines,
    )


def _diff(*files: FileDiff) -> PRDiff:
    return PRDiff(files=list(files), head_sha=_HEAD, base_sha=_BASE)


class TestWhenChunkingApplies:
    def test_a_small_diff_is_one_chunk(self) -> None:
        diff = _diff(_file("a.py", 5), _file("b.py", 5))
        assert not needs_chunking(diff, 100_000)
        assert len(chunk_diff(diff, 100_000)) == 1

    def test_the_single_chunk_is_the_original(self) -> None:
        diff = _diff(_file("a.py", 5))
        assert chunk_diff(diff, 100_000)[0].files == diff.files

    def test_a_large_diff_is_split(self) -> None:
        diff = _diff(*[_file(f"f{i}.py", 200) for i in range(10)])
        assert needs_chunking(diff, 2_000)
        assert len(chunk_diff(diff, 2_000)) > 1


class TestChunksAreWellFormed:
    def test_every_file_appears_exactly_once(self) -> None:
        diff = _diff(*[_file(f"f{i}.py", 200) for i in range(10)])
        chunks = chunk_diff(diff, 2_000)
        seen = [f.path for chunk in chunks for f in chunk.files]
        assert sorted(seen) == sorted(f.path for f in diff.files)
        assert len(seen) == len(set(seen))

    def test_no_chunk_is_empty(self) -> None:
        diff = _diff(*[_file(f"f{i}.py", 200) for i in range(10)])
        assert all(chunk.files for chunk in chunk_diff(diff, 2_000))

    def test_chunks_keep_the_shas(self) -> None:
        diff = _diff(*[_file(f"f{i}.py", 200) for i in range(6)])
        for chunk in chunk_diff(diff, 2_000):
            assert chunk.head_sha == _HEAD
            assert chunk.base_sha == _BASE

    def test_truncation_is_carried_to_every_chunk(self) -> None:
        diff = PRDiff(
            files=[_file(f"f{i}.py", 200) for i in range(6)],
            head_sha=_HEAD,
            base_sha=_BASE,
            truncated=True,
        )
        assert all(chunk.truncated for chunk in chunk_diff(diff, 2_000))

    def test_file_order_is_preserved(self) -> None:
        diff = _diff(*[_file(f"f{i:02d}.py", 200) for i in range(10)])
        chunks = chunk_diff(diff, 2_000)
        flat = [f.path for chunk in chunks for f in chunk.files]
        assert flat == [f.path for f in diff.files]


class TestOversizedSingleFile:
    def test_a_file_larger_than_the_limit_gets_its_own_chunk(self) -> None:
        diff = _diff(_file("small.py", 5), _file("huge.py", 5_000))
        chunks = chunk_diff(diff, 2_000)
        huge = [c for c in chunks if any(f.path == "huge.py" for f in c.files)]
        assert len(huge) == 1
        assert len(huge[0].files) == 1

    def test_it_is_still_reviewed_rather_than_dropped(self) -> None:
        diff = _diff(_file("huge.py", 5_000))
        chunks = chunk_diff(diff, 2_000)
        assert len(chunks) == 1
        assert chunks[0].files[0].path == "huge.py"


class TestDegenerateInput:
    def test_an_empty_diff_yields_no_chunks(self) -> None:
        assert chunk_diff(_diff(), 2_000) == []

    def test_a_zero_limit_gives_one_file_per_chunk(self) -> None:
        diff = _diff(_file("a.py", 5), _file("b.py", 5))
        chunks = chunk_diff(diff, 0)
        assert len(chunks) == 2
        assert all(len(c.files) == 1 for c in chunks)
