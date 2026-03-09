"""Tests for CLI entry point (story-1-4)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from prbot.cli import EXIT_BLOCKERS, EXIT_CONFIG_ERROR, EXIT_PASS, main, parse_args


class TestParseArgs:
    """Test CLI argument parsing."""

    def test_help_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc_info:
            parse_args(["--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "prbot" in captured.out

    def test_version_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc_info:
            parse_args(["--version"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "0.1.0" in captured.out

    def test_all_flags(self) -> None:
        args = parse_args([
            "--platform", "github",
            "--pr", "42",
            "--repo", "owner/repo",
            "--config", "/path/to/config.toml",
            "--dry-run",
        ])
        assert args.platform == "github"
        assert args.pr_number == 42
        assert args.repo == "owner/repo"
        assert args.config == "/path/to/config.toml"
        assert args.dry_run is True

    def test_no_args_defaults(self) -> None:
        args = parse_args([])
        assert args.platform is None
        assert args.pr_number is None
        assert args.repo is None
        assert args.dry_run is False


class TestMain:
    """Test main() entry point with exit codes."""

    def test_valid_config_exits_0(self) -> None:
        mock_pipeline = AsyncMock(return_value=EXIT_PASS)
        with patch("prbot.cli.run_pipeline", mock_pipeline):
            with pytest.raises(SystemExit) as exc_info:
                main(["--platform", "github", "--pr", "42", "--repo", "owner/repo"])
            assert exc_info.value.code == EXIT_PASS
            mock_pipeline.assert_called_once()

    def test_missing_platform_and_no_ci_exits_2(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(SystemExit) as exc_info:
                main(["--pr", "42", "--repo", "owner/repo"])
            assert exc_info.value.code == EXIT_CONFIG_ERROR

    def test_invalid_repo_exits_2(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["--platform", "github", "--pr", "42", "--repo", "../../bad"])
        assert exc_info.value.code == EXIT_CONFIG_ERROR

    def test_exit_code_constants(self) -> None:
        assert EXIT_PASS == 0
        assert EXIT_BLOCKERS == 1
        assert EXIT_CONFIG_ERROR == 2
