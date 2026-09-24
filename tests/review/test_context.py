"""Tests for expanded file context (B8).

The model sees hunks and nothing else, so architecture, testing and
maintainability findings cannot be well grounded: the enclosing function is
usually not in the diff. This is the standard ceiling on diff-only reviewers
and the most common source of false positives in them.

Whether paying for the surrounding lines raises precision enough to justify
the tokens is a question for measurement, so this ships as a setting that
defaults to off, exactly as the datamarking question did.
"""

from __future__ import annotations

from prbot.review.context import build_context_excerpt, hunk_spans
from prbot.vcs.models import FileDiff

_PATCH = """@@ -10,3 +10,4 @@ class Handler:
     def handle(self, request):
-        return self.legacy(request)
+        return self.modern(request)
     # trailing
"""

_FILE = "\n".join(f"line {i}" for i in range(1, 41))


class TestHunkSpans:
    def test_it_covers_the_hunk(self) -> None:
        assert hunk_spans(_PATCH) == [(10, 13)]

    def test_each_hunk_is_its_own_span(self) -> None:
        """Two hunks are two regions, not one region between them.

        Reporting them as one range from the first to the last made the
        excerpt a whole-file copy for any file edited near both ends.
        """
        patch = "@@ -1,2 +1,2 @@\n a\n+b\n@@ -50,2 +50,3 @@\n x\n+y\n"
        assert hunk_spans(patch) == [(1, 2), (50, 52)]

    def test_a_pure_deletion_keeps_its_position(self) -> None:
        assert hunk_spans("@@ -7,2 +6,0 @@\n-a\n-b\n") == [(6, 6)]

    def test_a_patch_without_hunks_has_no_spans(self) -> None:
        assert hunk_spans("no hunks here") == []

    def test_an_empty_patch_has_no_spans(self) -> None:
        assert hunk_spans("") == []


def _numbers(excerpt: str) -> set[int]:
    """Line numbers present in an excerpt.

    Asserting on the text will not work: the content is datamarked, so
    "line 5" arrives as "line ^mark^ 5".
    """
    out: set[int] = set()
    for line in excerpt.splitlines():
        head = line.strip().split(" ", 1)[0]
        if head.isdigit():
            out.add(int(head))
    return out


class TestExcerpt:
    @staticmethod
    def _diff() -> FileDiff:
        return FileDiff(path="src/app.py", status="modified", patch=_PATCH)

    def test_it_includes_lines_before_and_after(self) -> None:
        numbers = _numbers(
            build_context_excerpt(self._diff(), _FILE, context_lines=5),
        )
        assert 5 in numbers
        assert 18 in numbers

    def test_it_stops_at_the_top_of_the_file(self) -> None:
        diff = FileDiff(
            path="a.py", status="modified", patch="@@ -1,2 +1,2 @@\n a\n+b\n",
        )
        out = build_context_excerpt(diff, _FILE, context_lines=20)
        assert out.splitlines()[0].strip().startswith("1")

    def test_it_stops_at_the_end_of_the_file(self) -> None:
        diff = FileDiff(
            path="a.py", status="modified", patch="@@ -38,2 +38,2 @@\n a\n+b\n",
        )
        numbers = _numbers(
            build_context_excerpt(diff, _FILE, context_lines=20),
        )
        assert 40 in numbers
        assert 41 not in numbers

    def test_lines_are_numbered_so_findings_can_cite_them(self) -> None:
        assert 8 in _numbers(
            build_context_excerpt(self._diff(), _FILE, context_lines=2),
        )

    def test_zero_context_produces_nothing(self) -> None:
        assert build_context_excerpt(self._diff(), _FILE, context_lines=0) == ""

    def test_no_content_produces_nothing(self) -> None:
        assert build_context_excerpt(self._diff(), None, context_lines=5) == ""

    def test_a_patch_without_hunks_produces_nothing(self) -> None:
        diff = FileDiff(path="a.py", status="modified", patch="binary")
        assert build_context_excerpt(diff, _FILE, context_lines=5) == ""

    def test_every_excerpt_line_is_datamarked(self) -> None:
        """It is file content, so it is as untrusted as the patch.

        Every numbered line carries the mark, not just one of them. The only
        unmarked line is prbot's own separator between two windows.
        """
        from prbot.security.datamarking import get_session_mark

        mark = f"^{get_session_mark()}^"
        diff = FileDiff(
            path="a.tf", status="modified",
            patch="@@ -5,2 +5,2 @@\n a\n+b\n@@ -30,2 +30,2 @@\n c\n+d\n",
        )
        rows = build_context_excerpt(diff, _FILE, context_lines=3).splitlines()
        numbered = [row for row in rows if row.strip()[:1].isdigit()]
        assert numbered
        assert all(mark in row for row in numbered)
        assert [row.strip() for row in rows if row not in numbered] == ["..."]


class TestExcerptIsBoundedByTheHunks:
    """The excerpt is the neighbourhood of each change, never the gap.

    A window per hunk is also what the hallucination check assumes the model
    saw: it measures a finding's distance to the nearest hunk against
    context_lines, so code between two distant hunks was never "shown" as far
    as the validator is concerned, and sending it only spends tokens.
    """

    _LONG = "\n".join(f"line {i}" for i in range(1, 1001))

    @staticmethod
    def _hunks(*starts: int) -> FileDiff:
        patch = "".join(f"@@ -{s},2 +{s},2 @@\n a\n+b\n" for s in starts)
        return FileDiff(path="a.tf", status="modified", patch=patch)

    def test_distant_hunks_do_not_pull_in_the_lines_between_them(self) -> None:
        out = build_context_excerpt(
            self._hunks(5, 900), self._LONG, context_lines=40,
        )
        numbers = _numbers(out)
        assert {1, 5, 46} <= numbers
        assert {860, 900, 941} <= numbers
        assert 500 not in numbers
        assert 47 not in numbers
        assert 859 not in numbers

    def test_the_excerpt_grows_with_the_hunks_not_the_file(self) -> None:
        out = build_context_excerpt(
            self._hunks(5, 900), self._LONG, context_lines=40,
        )
        # Each window is at most the hunk plus context on both sides.
        assert len(_numbers(out)) <= 2 * (2 + 2 * 40)

    def test_a_gap_between_windows_is_marked(self) -> None:
        """Numbering alone would let 46 and 860 read as adjacent lines."""
        out = build_context_excerpt(
            self._hunks(5, 900), self._LONG, context_lines=40,
        )
        lines = out.splitlines()
        at = next(i for i, row in enumerate(lines) if row.strip().startswith("46 "))
        assert not lines[at + 1].strip()[:1].isdigit()

    def test_overlapping_windows_merge_without_repeating_lines(self) -> None:
        """The distant third hunk is what one whole span would fail on."""
        out = build_context_excerpt(
            self._hunks(10, 60, 900), self._LONG, context_lines=40,
        )
        numbered = [
            int(row.strip().split(" ", 1)[0])
            for row in out.splitlines() if row.strip()[:1].isdigit()
        ]
        assert numbered == [*range(1, 102), *range(860, 942)]

    def test_adjacent_windows_merge(self) -> None:
        # The first window ends at 11+40 = 51 and the second starts at
        # 92-40 = 52, so they touch and no gap is marked between them. The
        # distant third hunk leaves exactly one gap, after line 133.
        out = build_context_excerpt(
            self._hunks(10, 92, 900), self._LONG, context_lines=40,
        )
        rows = out.splitlines()
        gaps = [i for i, row in enumerate(rows) if not row.strip()[:1].isdigit()]
        assert len(gaps) == 1
        assert rows[gaps[0] - 1].strip().startswith("133 ")

    def test_a_hunk_past_the_end_of_the_file_has_no_window(self) -> None:
        """The fetched file can be shorter than the patch says it is.

        A window that would start after the file ends is dropped, and a
        patch left with no window gives no excerpt at all.
        """
        assert build_context_excerpt(
            self._hunks(900), _FILE, context_lines=5,
        ) == ""

    def test_only_windows_inside_the_file_are_shown(self) -> None:
        out = build_context_excerpt(
            self._hunks(5, 900), _FILE, context_lines=5,
        )
        assert _numbers(out) == set(range(1, 12))
        assert all(row.strip()[:1].isdigit() for row in out.splitlines())


class TestPromptIntegration:
    @staticmethod
    def _inputs():
        from prbot.vcs.models import PRDiff, PRMetadata

        diff = PRDiff(
            files=[FileDiff(path="src/app.py", status="modified", patch=_PATCH)],
        )
        meta = PRMetadata(
            title="t", body="b", state="open",
            head_sha="a" * 40, base_sha="b" * 40,
            head_ref="f", base_ref="main", author="x", number=1,
        )
        return diff, meta

    def test_context_is_absent_by_default(self) -> None:
        from prbot.review.prompts import build_user_prompt

        diff, meta = self._inputs()
        assert "Surrounding code" not in build_user_prompt(diff, meta)

    def test_context_appears_when_supplied(self) -> None:
        from prbot.review.prompts import build_user_prompt

        diff, meta = self._inputs()
        out = build_user_prompt(
            diff, meta,
            file_contents={"src/app.py": _FILE},
            context_lines=4,
        )
        assert "Surrounding code" in out
        assert 8 in _numbers(out)

    def test_a_missing_file_does_not_break_the_prompt(self) -> None:
        from prbot.review.prompts import build_user_prompt

        diff, meta = self._inputs()
        out = build_user_prompt(
            diff, meta, file_contents={}, context_lines=4,
        )
        assert "src/app.py" in out
        assert "Surrounding code" not in out
