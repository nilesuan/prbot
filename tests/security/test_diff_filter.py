"""Tests for diff filtering (story-6-7)."""

from __future__ import annotations

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
