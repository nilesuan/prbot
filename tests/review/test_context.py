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

from prbot.review.context import build_context_excerpt, hunk_span
from prbot.vcs.models import FileDiff

_PATCH = """@@ -10,3 +10,4 @@ class Handler:
     def handle(self, request):
-        return self.legacy(request)
+        return self.modern(request)
     # trailing
"""

_FILE = "\n".join(f"line {i}" for i in range(1, 41))


class TestHunkSpan:
    def test_it_covers_the_hunk(self) -> None:
        assert hunk_span(_PATCH) == (10, 13)

    def test_multiple_hunks_span_from_first_to_last(self) -> None:
        patch = "@@ -1,2 +1,2 @@\n a\n+b\n@@ -50,2 +50,3 @@\n x\n+y\n"
        assert hunk_span(patch) == (1, 52)

    def test_a_patch_without_hunks_has_no_span(self) -> None:
        assert hunk_span("no hunks here") is None

    def test_an_empty_patch_has_no_span(self) -> None:
        assert hunk_span("") is None


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

    def test_the_excerpt_is_datamarked(self) -> None:
        """It is file content, so it is as untrusted as the patch."""
        from prbot.security.datamarking import get_session_mark

        out = build_context_excerpt(self._diff(), _FILE, context_lines=3)
        assert f"^{get_session_mark()}^" in out


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
