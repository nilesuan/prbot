"""Tests for diff filtering (story-6-7)."""

from __future__ import annotations

import pytest

from prbot.security.diff_filter import (
    _matches_exclusion,
    filter_diff,
    is_binary_file,
    is_generated_file,
)
from prbot.vcs.models import FileDiff, PRDiff


def _make_file(
    path: str = "src/app.py",
    status: str = "modified",
    patch: str = "+code",
) -> FileDiff:
    return FileDiff(path=path, status=status, patch=patch)


class TestIsBinaryFile:
    """Tests for binary file detection."""

    def test_png_extension(self) -> None:
        assert is_binary_file(_make_file("logo.png"))

    def test_jpg_extension(self) -> None:
        assert is_binary_file(_make_file("photo.jpg"))

    def test_py_not_binary(self) -> None:
        assert not is_binary_file(_make_file("app.py"))

    def test_empty_patch_binary(self) -> None:
        f = FileDiff(path="data.bin", status="added", patch="")
        assert is_binary_file(f)

    def test_empty_patch_removed_not_binary(self) -> None:
        f = FileDiff(path="old.py", status="removed", patch="")
        assert not is_binary_file(f)

    def test_binary_marker_in_patch(self) -> None:
        f = FileDiff(
            path="image.dat",
            status="modified",
            patch="Binary files differ",
        )
        assert is_binary_file(f)

    def test_case_insensitive_extension(self) -> None:
        assert is_binary_file(_make_file("icon.PNG"))


class TestIsGeneratedFile:
    """Tests for generated file detection."""

    def test_package_lock(self) -> None:
        assert is_generated_file("package-lock.json")

    def test_yarn_lock(self) -> None:
        assert is_generated_file("yarn.lock")

    def test_uv_lock(self) -> None:
        assert is_generated_file("uv.lock")

    def test_snapshots_nested(self) -> None:
        """NG-23: Nested __snapshots__ detected."""
        assert is_generated_file(
            "src/components/__snapshots__/App.snap",
        )

    def test_min_js(self) -> None:
        assert is_generated_file("vendor/lodash.min.js")

    def test_source_map(self) -> None:
        assert is_generated_file("dist/app.js.map")

    def test_regular_js_not_generated(self) -> None:
        assert not is_generated_file("src/app.js")

    def test_regular_py_not_generated(self) -> None:
        assert not is_generated_file("src/main.py")


class TestMatchesExclusion:
    """Tests for user exclusion pattern matching (G4-08)."""

    def test_vendor_glob(self) -> None:
        assert _matches_exclusion(
            "src/vendor/lodash.js", ["**/vendor/**"],
        )

    def test_no_match(self) -> None:
        assert not _matches_exclusion(
            "src/app.py", ["vendor/**"],
        )

    def test_specific_file(self) -> None:
        assert _matches_exclusion(
            "config/settings.json", ["**/*.json"],
        )


class TestFilterDiff:
    """Tests for filter_diff integration."""

    def test_removes_binary(self) -> None:
        diff = PRDiff(files=[
            _make_file("src/app.py"),
            _make_file("logo.png"),
        ])
        result = filter_diff(diff)
        assert len(result.files) == 1
        assert result.files[0].path == "src/app.py"

    def test_removes_generated(self) -> None:
        diff = PRDiff(files=[
            _make_file("src/app.py"),
            _make_file("package-lock.json"),
        ])
        result = filter_diff(diff)
        assert len(result.files) == 1

    def test_removes_excluded(self) -> None:
        diff = PRDiff(files=[
            _make_file("src/app.py"),
            _make_file("docs/readme.md"),
        ])
        result = filter_diff(diff, exclusion_patterns=["**/*.md"])
        assert len(result.files) == 1

    def test_preserves_original(self) -> None:
        original_files = [_make_file("src/app.py"), _make_file("logo.png")]
        diff = PRDiff(files=original_files)
        filter_diff(diff)
        assert len(diff.files) == 2  # Original unchanged

    def test_all_filtered_empty_result(self) -> None:
        diff = PRDiff(files=[_make_file("logo.png")])
        result = filter_diff(diff)
        assert len(result.files) == 0

    def test_preserves_sha_and_truncated(self) -> None:
        sha = "abcdef1234567890abcdef1234567890abcdef12"
        diff = PRDiff(
            files=[_make_file("src/app.py")],
            head_sha=sha,
            base_sha=sha,
            truncated=True,
        )
        result = filter_diff(diff)
        assert result.head_sha == sha
        assert result.truncated is True


class TestGlobSemantics:
    """A2: patterns must match at the depths users expect (gitignore rules).

    PurePosixPath.match matches from the right and does not treat ** as
    recursive, so `**/node_modules/*` matched nothing real and `vendor/**`
    matched nothing at all.
    """

    def test_builtin_excludes_root_level_node_modules(self) -> None:
        assert is_generated_file("node_modules/pkg/index.js")

    def test_builtin_excludes_nested_node_modules(self) -> None:
        assert is_generated_file("web/node_modules/pkg/index.js")

    def test_builtin_excludes_deeply_nested_files(self) -> None:
        assert is_generated_file("web/node_modules/a/b/c/deep.js")

    def test_builtin_excludes_root_level_dist(self) -> None:
        assert is_generated_file("dist/app.js")

    def test_builtin_excludes_nested_dist(self) -> None:
        assert is_generated_file("frontend/src/dist/app.js")

    def test_builtin_excludes_lockfile_at_any_depth(self) -> None:
        assert is_generated_file("uv.lock")
        assert is_generated_file("services/api/uv.lock")

    def test_builtin_does_not_overmatch_similar_names(self) -> None:
        assert not is_generated_file("src/distribution.py")
        assert not is_generated_file("src/my_node_modules_helper.py")
        assert not is_generated_file("src/app.py")

    def test_user_anchored_pattern_matches_subtree(self) -> None:
        assert _matches_exclusion("vendor/lib/x.go", ["vendor/**"])
        assert _matches_exclusion("vendor/a/b/c/x.go", ["vendor/**"])

    def test_user_recursive_pattern_matches_at_any_depth(self) -> None:
        assert _matches_exclusion("a/vendor/lib/x.go", ["**/vendor/**"])
        assert _matches_exclusion("vendor/lib/x.go", ["**/vendor/**"])

    def test_user_suffix_pattern_matches_at_any_depth(self) -> None:
        assert _matches_exclusion("poetry.lock", ["*.lock"])
        assert _matches_exclusion("sub/dir/poetry.lock", ["*.lock"])

    def test_user_pattern_does_not_match_unrelated_file(self) -> None:
        assert not _matches_exclusion("src/app.py", ["vendor/**", "*.lock"])

    def test_invalid_pattern_is_reported_not_swallowed(self) -> None:
        """A pattern that cannot compile must not silently match nothing.

        A bare "!" is a negation with nothing to negate, which git itself
        rejects. ("[" is not invalid here: gitignore treats an unclosed
        bracket as a literal.)
        """
        from prbot.exceptions import ConfigError

        with pytest.raises(ConfigError, match="Invalid exclusion pattern"):
            _matches_exclusion("src/app.py", ["!"])


class TestShippedConfigPatterns:
    """The patterns in .prbot.toml must actually exclude what they name."""

    @staticmethod
    def _shipped() -> list[str]:
        import tomllib
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent.parent
        with (root / ".prbot.toml").open("rb") as handle:
            return tomllib.load(handle)["prbot"]["excluded_patterns"]

    def test_vendor_pattern_excludes_vendored_code(self) -> None:
        assert _matches_exclusion("vendor/github.com/x/y.go", self._shipped())

    def test_node_modules_pattern_excludes_dependencies(self) -> None:
        assert _matches_exclusion("node_modules/left-pad/index.js", self._shipped())
        assert _matches_exclusion("web/node_modules/left-pad/index.js", self._shipped())

    def test_shipped_patterns_leave_source_alone(self) -> None:
        assert not _matches_exclusion("src/prbot/cli.py", self._shipped())
