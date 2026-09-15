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
        from prbot import __version__

        assert __version__ in captured.out

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
        # None, not False: an absent flag must not overwrite the env var or
        # TOML layers in build_config (A1).
        assert args.dry_run is None


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


class TestUnexpectedErrorsExitThree:
    """D4: a crash must not be reported to CI as REQUEST_CHANGES."""

    def test_unexpected_exception_exits_infra_not_blockers(self) -> None:
        from prbot.cli import EXIT_INFRA_ERROR, main

        boom = AsyncMock(side_effect=RuntimeError("something unforeseen"))
        with patch("prbot.cli.run_pipeline", boom), pytest.raises(
            SystemExit,
        ) as exc:
            main(["--platform", "github", "--repo", "o/r", "--pr", "1"])
        assert exc.value.code == EXIT_INFRA_ERROR

    def test_keyboard_interrupt_is_not_a_blocking_review(self) -> None:
        from prbot.cli import EXIT_INFRA_ERROR, main

        stop = AsyncMock(side_effect=KeyboardInterrupt())
        with patch("prbot.cli.run_pipeline", stop), pytest.raises(
            SystemExit,
        ) as exc:
            main(["--platform", "github", "--repo", "o/r", "--pr", "1"])
        assert exc.value.code == EXIT_INFRA_ERROR


class TestExitCodesComeFromTheExceptions:
    """GEN-MAINT-02: main() kept a second copy of the exit-code mapping."""

    def test_insufficient_scopes_exits_config_error(self) -> None:
        from prbot.cli import EXIT_CONFIG_ERROR, main
        from prbot.exceptions import InsufficientScopesError

        boom = AsyncMock(side_effect=InsufficientScopesError("missing repo"))
        with patch("prbot.cli.run_pipeline", boom), pytest.raises(
            SystemExit,
        ) as exc:
            main(["--platform", "github", "--repo", "o/r", "--pr", "1"])
        assert exc.value.code == EXIT_CONFIG_ERROR

    def test_auth_error_exits_infra_error(self) -> None:
        from prbot.cli import EXIT_INFRA_ERROR, main
        from prbot.exceptions import AuthError

        boom = AsyncMock(side_effect=AuthError("no token"))
        with patch("prbot.cli.run_pipeline", boom), pytest.raises(
            SystemExit,
        ) as exc:
            main(["--platform", "github", "--repo", "o/r", "--pr", "1"])
        assert exc.value.code == EXIT_INFRA_ERROR

    def test_the_mapping_lives_only_in_exceptions(self) -> None:
        """One source of truth: exceptions.py declares exit_code per class."""
        import inspect

        from prbot import cli

        source = inspect.getsource(cli.main)
        assert "InsufficientScopesError" not in source
        assert "except AuthError" not in source
        assert "e.exit_code" in source
