"""Tests for reading files beyond the diff (read_file tool).

The facts that settled the largest defects a verified review found were
outside the diff: a route table in another file of the same module, a CI
file listing which test files run. An agent that can only read the diff
cannot establish them, so it either guesses or says nothing. The reader
gives it bounded, read-only access to the head revision.
"""

from __future__ import annotations

import pytest

from prbot.review.tools import FileReader

_SOURCE = "\n".join(f"line {i}" for i in range(1, 1001))


def _numbers(text: str) -> list[int]:
    out: list[int] = []
    for row in text.splitlines():
        head = row.strip().split(" ", 1)[0]
        if head.isdigit():
            out.append(int(head))
    return out


def _reader(files: dict[str, str | None] | None = None, **kw: object):
    fetched: list[str] = []
    store = {"routes.tf": _SOURCE} if files is None else files

    async def fetch(path: str) -> str | None:
        fetched.append(path)
        return store.get(path)

    return FileReader(fetch, **kw), fetched


class TestReading:
    @pytest.mark.asyncio
    async def test_it_returns_numbered_lines(self) -> None:
        reader, _ = _reader()
        out = await reader.read("routes.tf", 10, 12)
        assert _numbers(out) == [10, 11, 12]

    @pytest.mark.asyncio
    async def test_the_content_is_datamarked(self) -> None:
        """It is repository content, exactly as untrusted as the diff."""
        from prbot.security.datamarking import get_session_mark

        reader, _ = _reader()
        out = await reader.read("routes.tf", 1, 3)
        assert f"^{get_session_mark()}^" in out

    @pytest.mark.asyncio
    async def test_it_says_how_long_the_file_is(self) -> None:
        reader, _ = _reader()
        out = await reader.read("routes.tf", 1, 3)
        assert "of 1000" in out

    @pytest.mark.asyncio
    async def test_a_read_is_capped(self) -> None:
        reader, _ = _reader(max_lines_per_read=50)
        out = await reader.read("routes.tf", 1, 1000)
        assert _numbers(out) == list(range(1, 51))

    @pytest.mark.asyncio
    async def test_no_range_reads_from_the_top(self) -> None:
        reader, _ = _reader(max_lines_per_read=5)
        assert _numbers(await reader.read("routes.tf")) == [1, 2, 3, 4, 5]

    @pytest.mark.asyncio
    async def test_a_range_past_the_end_is_clamped(self) -> None:
        reader, _ = _reader()
        assert _numbers(await reader.read("routes.tf", 998, 2000)) == [
            998, 999, 1000,
        ]

    @pytest.mark.asyncio
    async def test_a_missing_file_says_so(self) -> None:
        reader, _ = _reader()
        out = await reader.read("nope.tf")
        assert "not found" in out.lower()

    @pytest.mark.asyncio
    async def test_a_file_is_fetched_once(self) -> None:
        reader, fetched = _reader()
        await reader.read("routes.tf", 1, 5)
        await reader.read("routes.tf", 6, 9)
        assert fetched == ["routes.tf"]

    @pytest.mark.asyncio
    async def test_a_shared_cache_serves_other_readers(self) -> None:
        cache: dict[str, str | None] = {"routes.tf": _SOURCE}
        reader, fetched = _reader(cache=cache)
        await reader.read("routes.tf", 1, 2)
        assert fetched == []


class TestTheBudget:
    @pytest.mark.asyncio
    async def test_the_budget_bounds_total_lines(self) -> None:
        reader, _ = _reader(max_lines_per_read=40, max_lines_total=60)
        first = await reader.read("routes.tf", 1, 40)
        second = await reader.read("routes.tf", 41, 80)
        assert len(_numbers(first)) == 40
        assert _numbers(second) == list(range(41, 61))

    @pytest.mark.asyncio
    async def test_an_exhausted_budget_says_so(self) -> None:
        reader, _ = _reader(max_lines_per_read=10, max_lines_total=10)
        await reader.read("routes.tf", 1, 10)
        out = await reader.read("routes.tf", 11, 20)
        assert _numbers(out) == []
        assert "budget" in out.lower()

    @pytest.mark.asyncio
    async def test_lines_read_is_reported(self) -> None:
        reader, _ = _reader()
        await reader.read("routes.tf", 1, 7)
        assert reader.lines_read == 7


class TestRefusals:
    """The reader is read-only and bounded to what review may see."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", [
        "/etc/passwd", "../outside.tf", "a/../../b.tf", "", "a\x00b.tf",
        "a\nb.tf", "x" * 600,
    ])
    async def test_unsafe_paths_are_refused_without_fetching(
        self, path: str,
    ) -> None:
        reader, fetched = _reader()
        out = await reader.read(path)
        assert "refused" in out.lower()
        assert fetched == []

    @pytest.mark.asyncio
    async def test_an_excluded_path_is_refused(self) -> None:
        """A file the configuration keeps out of review stays out of it."""
        reader, fetched = _reader(
            {"secrets/prod.tfvars": "password = x"},
            exclusion_patterns=["secrets/"],
        )
        out = await reader.read("secrets/prod.tfvars")
        assert "refused" in out.lower()
        assert fetched == []

    @pytest.mark.asyncio
    async def test_a_generated_file_is_refused(self) -> None:
        reader, fetched = _reader({"package-lock.json": "{}"})
        out = await reader.read("package-lock.json")
        assert "refused" in out.lower()
        assert fetched == []

    @pytest.mark.asyncio
    async def test_a_binary_file_is_refused(self) -> None:
        reader, fetched = _reader({"logo.png": "x"})
        out = await reader.read("logo.png")
        assert "refused" in out.lower()
        assert fetched == []

    @pytest.mark.asyncio
    async def test_a_refusal_costs_no_budget(self) -> None:
        reader, _ = _reader()
        await reader.read("../x")
        assert reader.lines_read == 0
