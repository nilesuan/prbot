"""Tests for configuration model and validators (story-1-4)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from prbot.config import (
    PrBotConfig,
    _validate_url_not_internal,
    build_config,
    detect_platform,
    detect_pr_context,
    load_toml_config,
)
from prbot.exceptions import ConfigError


class TestPrBotConfig:
    """Test PrBotConfig model construction and validation."""

    def test_valid_config_minimal(self) -> None:
        c = PrBotConfig(platform="github", repo="owner/repo", pr_number=42)
        assert c.platform == "github"
        assert c.repo == "owner/repo"
        assert c.pr_number == 42
        assert c.confidence_threshold == 70
        assert c.blocker_threshold == 70
        assert c.aws_region == "ap-southeast-2"
        assert c.draft_behavior == "skip"
        assert c.log_level == "INFO"
        assert c.dry_run is False

    def test_valid_config_all_fields(self) -> None:
        c = PrBotConfig(
            platform="gitlab",
            repo="org/project",
            pr_number=1,
            aws_region="us-east-1",
            confidence_threshold=60,
            blocker_threshold=80,
            general_model_id="test-model",
            security_model_id="test-opus",
            max_diff_tokens=50_000,
            budget_limit_usd=2.50,
            timeout_seconds=120,
            api_base_url="https://gitlab.example.com",
            secret_name="prbot/token",
            draft_behavior="review",
            excluded_patterns=["*.lock"],
            log_level="debug",
            dry_run=True,
        )
        assert c.platform == "gitlab"
        assert c.log_level == "DEBUG"  # uppercased
        assert c.dry_run is True

    def test_config_is_frozen(self) -> None:
        c = PrBotConfig(platform="github", repo="o/r", pr_number=1)
        with pytest.raises(ValidationError):
            c.platform = "gitlab"  # type: ignore[misc]

    def test_repo_rejects_path_traversal(self) -> None:
        with pytest.raises(ValidationError, match="repo"):
            PrBotConfig(platform="github", repo="../../etc/passwd", pr_number=1)

    def test_repo_rejects_spaces(self) -> None:
        with pytest.raises(ValidationError, match="repo"):
            PrBotConfig(platform="github", repo="owner/ repo", pr_number=1)

    def test_repo_rejects_command_injection(self) -> None:
        with pytest.raises(ValidationError, match="repo"):
            PrBotConfig(platform="github", repo="owner/repo;rm -rf", pr_number=1)

    def test_repo_allows_dots_hyphens_underscores(self) -> None:
        c = PrBotConfig(platform="github", repo="my-org/my_repo.v2", pr_number=1)
        assert c.repo == "my-org/my_repo.v2"

    def test_pr_number_must_be_positive(self) -> None:
        with pytest.raises(ValidationError, match="pr_number"):
            PrBotConfig(platform="github", repo="o/r", pr_number=0)

    def test_pr_number_rejects_negative(self) -> None:
        with pytest.raises(ValidationError, match="pr_number"):
            PrBotConfig(platform="github", repo="o/r", pr_number=-1)

    def test_threshold_ordering_invariant(self) -> None:
        with pytest.raises(ValidationError, match="blocker_threshold"):
            PrBotConfig(
                platform="github", repo="o/r", pr_number=1,
                blocker_threshold=50, confidence_threshold=70,
            )

    def test_threshold_ordering_equal_is_valid(self) -> None:
        c = PrBotConfig(
            platform="github", repo="o/r", pr_number=1,
            blocker_threshold=70, confidence_threshold=70,
        )
        assert c.blocker_threshold == 70

    def test_api_base_url_rejects_private_ip(self) -> None:
        with pytest.raises((ValidationError, ConfigError)):
            PrBotConfig(
                platform="github", repo="o/r", pr_number=1,
                api_base_url="https://192.168.1.1/api",
            )

    def test_api_base_url_rejects_link_local(self) -> None:
        with pytest.raises((ValidationError, ConfigError)):
            PrBotConfig(
                platform="github", repo="o/r", pr_number=1,
                api_base_url="https://169.254.169.254/latest",
            )

    def test_api_base_url_rejects_loopback(self) -> None:
        with pytest.raises((ValidationError, ConfigError)):
            PrBotConfig(
                platform="github", repo="o/r", pr_number=1,
                api_base_url="https://127.0.0.1/api",
            )

    def test_api_base_url_allows_public(self) -> None:
        c = PrBotConfig(
            platform="github", repo="o/r", pr_number=1,
            api_base_url="https://api.github.com",
        )
        assert c.api_base_url == "https://api.github.com"

    def test_aws_region_valid_format(self) -> None:
        c = PrBotConfig(
            platform="github", repo="o/r",
            pr_number=1, aws_region="us-west-2",
        )
        assert c.aws_region == "us-west-2"

    def test_aws_region_rejects_path_traversal(self) -> None:
        with pytest.raises(ValidationError, match="aws_region"):
            PrBotConfig(
                platform="github", repo="o/r",
                pr_number=1, aws_region="../../etc",
            )

    def test_aws_region_rejects_invalid_format(self) -> None:
        with pytest.raises(ValidationError, match="aws_region"):
            PrBotConfig(
                platform="github", repo="o/r",
                pr_number=1, aws_region="invalid",
            )

    def test_secret_name_valid(self) -> None:
        c = PrBotConfig(
            platform="github", repo="o/r", pr_number=1,
            secret_name="prbot/vcs-token",
        )
        assert c.secret_name == "prbot/vcs-token"

    def test_secret_name_rejects_path_traversal(self) -> None:
        with pytest.raises(ValidationError, match="secret_name"):
            PrBotConfig(
                platform="github", repo="o/r", pr_number=1,
                secret_name="../../../etc/passwd",
            )

    def test_secret_name_rejects_leading_slash(self) -> None:
        with pytest.raises(ValidationError, match="secret_name"):
            PrBotConfig(
                platform="github", repo="o/r", pr_number=1,
                secret_name="/etc/passwd",
            )

    def test_log_level_valid_lowercase(self) -> None:
        c = PrBotConfig(platform="github", repo="o/r", pr_number=1, log_level="warning")
        assert c.log_level == "WARNING"

    def test_log_level_rejects_invalid(self) -> None:
        with pytest.raises(ValidationError, match="log_level"):
            PrBotConfig(platform="github", repo="o/r", pr_number=1, log_level="VERBOSE")


class TestValidateUrlNotInternal:
    """Test SSRF prevention helper."""

    def test_rejects_metadata_ip(self) -> None:
        with pytest.raises(ConfigError, match="SSRF"):
            _validate_url_not_internal("https://169.254.169.254/latest/meta-data/")

    def test_rejects_http_without_override(self) -> None:
        with pytest.raises(ConfigError, match="HTTP"):
            _validate_url_not_internal("http://example.com/api")

    def test_allows_http_with_override(self) -> None:
        with patch.dict(os.environ, {"PRBOT_ALLOW_HTTP": "1"}):
            result = _validate_url_not_internal("http://example.com/api")
            assert result == "http://example.com/api"

    def test_rejects_private_ip(self) -> None:
        with pytest.raises(ConfigError, match="private"):
            _validate_url_not_internal("https://10.0.0.1/api")

    def test_allows_public_dns(self) -> None:
        result = _validate_url_not_internal("https://api.github.com")
        assert result == "https://api.github.com"


class TestDetectPlatform:
    """Test CI platform auto-detection."""

    def test_github_actions_detected(self) -> None:
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}, clear=True):
            assert detect_platform() == "github"

    def test_gitlab_ci_detected(self) -> None:
        with patch.dict(os.environ, {"GITLAB_CI": "true"}, clear=True):
            assert detect_platform() == "gitlab"

    def test_no_ci_returns_none(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert detect_platform() is None


class TestDetectPrContext:
    """Test repo/PR extraction from CI environment."""

    def test_github_repo_and_pr(self) -> None:
        env = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_REF": "refs/pull/42/merge",
        }
        with patch.dict(os.environ, env, clear=True):
            ctx = detect_pr_context()
            assert ctx == {"repo": "owner/repo", "pr_number": "42"}

    def test_github_non_pr_ref_raises(self) -> None:
        env = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_REF": "refs/heads/main",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            pytest.raises(ConfigError, match="pull_request"),
        ):
            detect_pr_context()

    def test_github_malformed_ref_raises(self) -> None:
        env = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_REF": "refs/pull/abc/merge",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            pytest.raises(ConfigError, match="pull_request"),
        ):
            detect_pr_context()

    def test_gitlab_repo_and_mr(self) -> None:
        env = {
            "GITLAB_CI": "true",
            "CI_PROJECT_PATH": "group/project",
            "CI_MERGE_REQUEST_IID": "99",
        }
        with patch.dict(os.environ, env, clear=True):
            ctx = detect_pr_context()
            assert ctx == {"repo": "group/project", "pr_number": "99"}

    def test_gitlab_missing_mr_iid_raises(self) -> None:
        env = {
            "GITLAB_CI": "true",
            "CI_PROJECT_PATH": "group/project",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            pytest.raises(ConfigError, match="merge_request"),
        ):
            detect_pr_context()

    def test_no_ci_raises(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            pytest.raises(ConfigError, match="--repo"),
        ):
            detect_pr_context()


class TestLoadTomlConfig:
    """Test TOML config file loading."""

    def test_loads_prbot_toml(self, tmp_path: Path) -> None:
        toml_file = tmp_path / ".prbot.toml"
        toml_file.write_text('[prbot]\nconfidence_threshold = 80\n')
        result = load_toml_config(str(toml_file))
        assert result["confidence_threshold"] == 80

    def test_loads_pyproject_toml(self, tmp_path: Path) -> None:
        toml_file = tmp_path / "pyproject.toml"
        toml_file.write_text('[tool.prbot]\nblocker_threshold = 90\n')
        result = load_toml_config(str(toml_file))
        assert result["blocker_threshold"] == 90

    def test_missing_file_returns_empty(self) -> None:
        result = load_toml_config("/nonexistent/.prbot.toml")
        assert result == {}

    def test_invalid_toml_raises(self, tmp_path: Path) -> None:
        toml_file = tmp_path / ".prbot.toml"
        toml_file.write_text("invalid toml {{{{")
        with pytest.raises(ConfigError, match="Invalid TOML"):
            load_toml_config(str(toml_file))

    def test_bare_prbot_toml_without_section(self, tmp_path: Path) -> None:
        toml_file = tmp_path / ".prbot.toml"
        toml_file.write_text('confidence_threshold = 85\n')
        result = load_toml_config(str(toml_file))
        assert result["confidence_threshold"] == 85


class TestBuildConfig:
    """Test config merge priority: CLI > env > TOML > defaults."""

    def test_cli_args_override_env(self) -> None:
        config = build_config(
            cli_args={
                "platform": "github", "repo": "cli/repo",
                "pr_number": 1, "config": None,
            },
            env_vars={"PRBOT_REPO": "env/repo"},
            toml_config={},
        )
        assert config.repo == "cli/repo"

    def test_env_overrides_toml(self) -> None:
        config = build_config(
            cli_args={"platform": "github", "pr_number": 1, "config": None},
            env_vars={"PRBOT_REPO": "env/repo"},
            toml_config={"repo": "toml/repo"},
        )
        assert config.repo == "env/repo"

    def test_toml_overrides_defaults(self) -> None:
        config = build_config(
            cli_args={
                "platform": "github", "repo": "o/r",
                "pr_number": 1, "config": None,
            },
            env_vars={},
            toml_config={"confidence_threshold": 60},
        )
        assert config.confidence_threshold == 60

    def test_defaults_used_when_nothing_set(self) -> None:
        config = build_config(
            cli_args={
                "platform": "github", "repo": "o/r",
                "pr_number": 1, "config": None,
            },
            env_vars={},
            toml_config={},
        )
        assert config.confidence_threshold == 70
        assert config.timeout_seconds == 300

    def test_env_int_parsing(self) -> None:
        config = build_config(
            cli_args={"platform": "github", "repo": "o/r", "config": None},
            env_vars={"PRBOT_PR_NUMBER": "42"},
            toml_config={},
        )
        assert config.pr_number == 42

    def test_env_invalid_int_raises(self) -> None:
        with pytest.raises(ConfigError, match="Invalid integer"):
            build_config(
                cli_args={"platform": "github", "repo": "o/r", "config": None},
                env_vars={"PRBOT_PR_NUMBER": "abc"},
                toml_config={},
            )

    def test_validation_failure_raises_config_error(self) -> None:
        with pytest.raises(ConfigError, match="validation failed"):
            build_config(
                cli_args={"config": None},
                env_vars={},
                toml_config={"platform": "invalid"},
            )


class TestAsyncSmoke:
    """Verify pytest asyncio_mode=auto catches async test failures."""

    @pytest.mark.asyncio
    async def test_async_test_runs(self) -> None:
        assert 1 + 1 == 2
