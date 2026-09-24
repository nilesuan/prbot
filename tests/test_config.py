"""Tests for configuration model and validators (story-1-4)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar
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

    def test_verification_is_off_by_default(self) -> None:
        """CR-FLAG-01: a new feature ships behind a flag that defaults off,
        and turning it on is a change of its own."""
        config = build_config(
            cli_args={"platform": "github", "repo": "o/r", "config": None},
            env_vars={"PRBOT_PR_NUMBER": "1"},
            toml_config={},
        )
        assert config.verify is False

    def test_verification_can_be_turned_on(self) -> None:
        config = build_config(
            cli_args={"platform": "github", "repo": "o/r", "config": None},
            env_vars={"PRBOT_PR_NUMBER": "1", "PRBOT_VERIFY": "1"},
            toml_config={},
        )
        assert config.verify is True

    def test_reading_beyond_the_diff_is_off_by_default(self) -> None:
        config = build_config(
            cli_args={"platform": "github", "repo": "o/r", "config": None},
            env_vars={"PRBOT_PR_NUMBER": "1"},
            toml_config={},
        )
        assert config.tool_turns == 0

    def test_env_tool_turns_parsing(self) -> None:
        config = build_config(
            cli_args={"platform": "github", "repo": "o/r", "config": None},
            env_vars={"PRBOT_PR_NUMBER": "1", "PRBOT_TOOL_TURNS": "3"},
            toml_config={},
        )
        assert config.tool_turns == 3

    def test_temperature_is_unset_by_default(self) -> None:
        config = build_config(
            cli_args={"platform": "github", "repo": "o/r", "config": None},
            env_vars={"PRBOT_PR_NUMBER": "1"},
            toml_config={},
        )
        assert config.temperature is None

    @pytest.mark.parametrize("value", ["11", "-1", "abc"])
    def test_tool_turns_outside_its_bounds_raises(self, value: str) -> None:
        """SEC-CONFIG-02: the 0-10 bound held, but nothing tested it."""
        with pytest.raises(ConfigError):
            build_config(
                cli_args={"platform": "github", "repo": "o/r", "config": None},
                env_vars={"PRBOT_PR_NUMBER": "1", "PRBOT_TOOL_TURNS": value},
                toml_config={},
            )

    def test_env_temperature_parsing(self) -> None:
        config = build_config(
            cli_args={"platform": "github", "repo": "o/r", "config": None},
            env_vars={"PRBOT_PR_NUMBER": "1", "PRBOT_TEMPERATURE": "0"},
            toml_config={},
        )
        assert config.temperature == 0.0

    def test_bot_login_is_unset_by_default(self) -> None:
        config = build_config(
            cli_args={"platform": "github", "repo": "o/r", "config": None},
            env_vars={"PRBOT_PR_NUMBER": "1"},
            toml_config={},
        )
        assert config.bot_login == ""

    def test_env_bot_login(self) -> None:
        config = build_config(
            cli_args={"platform": "github", "repo": "o/r", "config": None},
            env_vars={
                "PRBOT_PR_NUMBER": "1",
                "PRBOT_BOT_LOGIN": "github-actions[bot]",
            },
            toml_config={},
        )
        assert config.bot_login == "github-actions[bot]"

    def test_a_bot_login_that_is_not_a_login_raises(self) -> None:
        with pytest.raises(ConfigError, match="bot_login"):
            build_config(
                cli_args={"platform": "github", "repo": "o/r", "config": None},
                env_vars={
                    "PRBOT_PR_NUMBER": "1",
                    "PRBOT_BOT_LOGIN": "github actions bot",
                },
                toml_config={},
            )

    def test_temperature_out_of_range_raises(self) -> None:
        with pytest.raises(ConfigError, match="temperature"):
            build_config(
                cli_args={"platform": "github", "repo": "o/r", "config": None},
                env_vars={"PRBOT_PR_NUMBER": "1", "PRBOT_TEMPERATURE": "3"},
                toml_config={},
            )

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


class TestDryRunPrecedence:
    """A1: dry_run must be settable from env and TOML, not only the flag."""

    @staticmethod
    def _args(*extra: str) -> dict[str, object]:
        from prbot.cli import parse_args

        base = ["--platform", "github", "--repo", "o/r", "--pr", "1"]
        return vars(parse_args(base + list(extra)))

    def test_absent_flag_is_none_not_false(self) -> None:
        """A store_true default of False overwrites every lower layer."""
        assert self._args()["dry_run"] is None

    def test_env_var_survives_cli_overlay(self) -> None:
        config = build_config(
            cli_args=self._args(),
            env_vars={"PRBOT_DRY_RUN": "true"},
            toml_config={},
        )
        assert config.dry_run is True

    def test_toml_survives_cli_overlay(self) -> None:
        config = build_config(
            cli_args=self._args(),
            env_vars={},
            toml_config={"dry_run": True},
        )
        assert config.dry_run is True

    def test_flag_still_wins_over_env(self) -> None:
        config = build_config(
            cli_args=self._args("--dry-run"),
            env_vars={"PRBOT_DRY_RUN": "false"},
            toml_config={},
        )
        assert config.dry_run is True

    def test_defaults_to_false(self) -> None:
        config = build_config(
            cli_args=self._args(), env_vars={}, toml_config={},
        )
        assert config.dry_run is False

    def test_env_var_rejects_unparseable_boolean(self) -> None:
        """A typo previously fell through to False in silence."""
        with pytest.raises(ConfigError, match="boolean"):
            build_config(
                cli_args=self._args(),
                env_vars={"PRBOT_DRY_RUN": "yep"},
                toml_config={},
            )


class TestRegionValidationMatchesAws:
    """D5: the pattern rejected GovCloud and ISO regions outright."""

    ACCEPTED: ClassVar[list[str]] = [
        "ap-southeast-2", "ap-southeast-4", "us-east-1", "eu-central-1",
        "il-central-1", "mx-central-1", "us-gov-west-1", "us-gov-east-1",
        "us-iso-east-1", "us-isob-east-1", "ca-central-1", "sa-east-1",
    ]
    REJECTED: ClassVar[list[str]] = [
        "", "US-EAST-1", "us east 1", "useast1", "u-east-1", "us-east",
    ]

    @pytest.mark.parametrize("region", ACCEPTED)
    def test_real_regions_are_accepted(self, region: str) -> None:
        config = PrBotConfig(
            platform="github", repo="o/r", pr_number=1,
            aws_region=region, allowed_regions=[],
            general_model_id="anthropic.claude-sonnet-4-6",
            security_model_id="anthropic.claude-sonnet-4-6",
        )
        assert config.aws_region == region

    @pytest.mark.parametrize("region", REJECTED)
    def test_malformed_regions_are_rejected(self, region: str) -> None:
        with pytest.raises(ValidationError):
            PrBotConfig(
                platform="github", repo="o/r", pr_number=1,
                aws_region=region, allowed_regions=[],
            )


class TestListValuedConfigFromEnvironment:
    """D6: README documented PRBOT_EXCLUDED_PATTERNS; it raised ConfigError.

    Pydantic will not coerce a string to list[str], so the only way to set a
    list was a TOML file. The container workflows pass configuration purely
    through env:, which made the documented setting unreachable exactly where
    it is most needed.
    """

    @staticmethod
    def _build(**env: str) -> PrBotConfig:
        base = {
            "PRBOT_PLATFORM": "github",
            "PRBOT_REPO": "o/r",
            "PRBOT_PR_NUMBER": "1",
        }
        base.update(env)
        return build_config(cli_args=None, env_vars=base, toml_config={})

    def test_excluded_patterns_from_env(self) -> None:
        config = self._build(
            PRBOT_EXCLUDED_PATTERNS="*.lock,**/vendor/**,*.min.js",
        )
        assert config.excluded_patterns == [
            "*.lock", "**/vendor/**", "*.min.js",
        ]

    def test_surrounding_whitespace_is_trimmed(self) -> None:
        config = self._build(PRBOT_EXCLUDED_PATTERNS=" *.lock , **/dist/** ")
        assert config.excluded_patterns == ["*.lock", "**/dist/**"]

    def test_empty_entries_are_dropped(self) -> None:
        config = self._build(PRBOT_EXCLUDED_PATTERNS="*.lock,,,*.map")
        assert config.excluded_patterns == ["*.lock", "*.map"]

    def test_a_single_value_needs_no_comma(self) -> None:
        config = self._build(PRBOT_EXCLUDED_PATTERNS="*.lock")
        assert config.excluded_patterns == ["*.lock"]

    def test_an_empty_value_is_an_empty_list(self) -> None:
        config = self._build(PRBOT_EXCLUDED_PATTERNS="")
        assert config.excluded_patterns == []

    def test_allowed_regions_from_env(self) -> None:
        config = self._build(
            PRBOT_ALLOWED_REGIONS="ap-southeast-2,ap-southeast-4",
            PRBOT_AWS_REGION="ap-southeast-4",
            PRBOT_GENERAL_MODEL_ID="anthropic.claude-sonnet-4-6",
            PRBOT_SECURITY_MODEL_ID="anthropic.claude-sonnet-4-6",
        )
        assert config.allowed_regions == ["ap-southeast-2", "ap-southeast-4"]

    def test_toml_lists_still_work(self) -> None:
        config = build_config(
            cli_args=None,
            env_vars={
                "PRBOT_PLATFORM": "github",
                "PRBOT_REPO": "o/r",
                "PRBOT_PR_NUMBER": "1",
            },
            toml_config={"excluded_patterns": ["a", "b"]},
        )
        assert config.excluded_patterns == ["a", "b"]


class TestConfigIsNotReadFromTheReviewedTree:
    """SEC-CRED-03: an implicit CWD search reads the branch under review.

    GitLab Runner clones the merge request source into the job's working
    directory, so load_toml_config's search for ./.prbot.toml found a file
    committed on the untrusted branch. That file can set api_base_url,
    secret_name, excluded_patterns and suppression rules, so the code being
    reviewed chose where its reviewer sent credentials and which of its own
    files were looked at.
    """

    def test_implicit_search_is_refused_in_ci(self) -> None:
        env = {"GITHUB_ACTIONS": "true"}
        assert load_toml_config(None, env_vars=env) == {}

    def test_implicit_search_is_refused_on_gitlab_ci(self) -> None:
        env = {"GITLAB_CI": "true"}
        assert load_toml_config(None, env_vars=env) == {}

    def test_an_explicit_path_still_works_in_ci(self, tmp_path: Path) -> None:
        cfg = tmp_path / "trusted.toml"
        cfg.write_text('[prbot]\nconfidence_threshold = 55\n')
        env = {"GITHUB_ACTIONS": "true"}
        assert load_toml_config(str(cfg), env_vars=env) == {
            "confidence_threshold": 55,
        }

    def test_implicit_search_works_outside_ci_when_opted_in(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Opt-in rather than on-by-default, so the guard cannot fail open.

        See TestImplicitConfigSearchIsOptIn for the default.
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".prbot.toml").write_text(
            '[prbot]\nconfidence_threshold = 61\n',
        )
        assert load_toml_config(
            None, env_vars={"PRBOT_ALLOW_IMPLICIT_CONFIG": "1"},
        ) == {"confidence_threshold": 61}

    def test_build_config_does_not_read_the_tree_in_ci(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".prbot.toml").write_text(
            '[prbot]\napi_base_url = "https://attacker.example.com"\n',
        )
        config = build_config(
            cli_args=None,
            env_vars={
                "GITHUB_ACTIONS": "true",
                "PRBOT_PLATFORM": "github",
                "PRBOT_REPO": "o/r",
                "PRBOT_PR_NUMBER": "1",
            },
        )
        assert config.api_base_url is None


class TestSsrfGuardResolvesNames:
    """SEC-DATA-02: anything that was not a literal IP was allowed.

    _validate_url_not_internal tried ipaddress.ip_address on the hostname and
    allowed whatever raised ValueError, so a DNS name pointing at the
    metadata endpoint passed, as did non-dotted-quad literal forms.
    """

    def test_a_name_resolving_to_link_local_is_refused(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import socket

        def fake(host, *a, **kw):
            return [(socket.AF_INET, None, None, "", ("169.254.169.254", 0))]

        monkeypatch.setattr(socket, "getaddrinfo", fake)
        with pytest.raises(ConfigError, match="SSRF"):
            _validate_url_not_internal("https://metadata.example.com/")

    def test_a_name_resolving_to_a_private_address_is_refused(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import socket

        def fake(host, *a, **kw):
            return [(socket.AF_INET, None, None, "", ("10.0.0.5", 0))]

        monkeypatch.setattr(socket, "getaddrinfo", fake)
        with pytest.raises(ConfigError, match="SSRF"):
            _validate_url_not_internal("https://internal.example.com/")

    def test_a_decimal_literal_is_refused(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ipaddress rejects the decimal form, so only the resolver sees it.

        getaddrinfo accepts '2852039166' and returns 169.254.169.254, which
        is why the guard has to resolve rather than only parse.
        """
        import socket

        def fake(host, *a, **kw):
            assert host == "2852039166"
            return [(socket.AF_INET, None, None, "", ("169.254.169.254", 0))]

        monkeypatch.setattr(socket, "getaddrinfo", fake)
        with pytest.raises(ConfigError, match="SSRF"):
            _validate_url_not_internal("https://2852039166/")

    def test_a_public_name_is_allowed(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import socket

        def fake(host, *a, **kw):
            return [(socket.AF_INET, None, None, "", ("140.82.121.6", 0))]

        monkeypatch.setattr(socket, "getaddrinfo", fake)
        assert _validate_url_not_internal("https://api.github.com/")

    def test_an_unresolvable_name_is_refused(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import socket

        def fake(host, *a, **kw):
            raise socket.gaierror("nope")

        monkeypatch.setattr(socket, "getaddrinfo", fake)
        with pytest.raises(ConfigError, match="resolve"):
            _validate_url_not_internal("https://nowhere.invalid/")

    def test_a_literal_internal_ip_is_still_refused(self) -> None:
        with pytest.raises(ConfigError, match="SSRF"):
            _validate_url_not_internal("https://169.254.169.254/")


class TestImplicitConfigSearchIsOptIn:
    """SEC-CRED-03: CI detection was an allowlist that failed open.

    _in_ci named three environment variables and the fallback was the unsafe
    branch, so any runner that sets none of them got the implicit search back.
    """

    def test_the_search_is_refused_by_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".prbot.toml").write_text("[prbot]\nconfidence_threshold = 61\n")
        assert load_toml_config(None, env_vars={}) == {}

    def test_it_is_allowed_when_opted_in(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".prbot.toml").write_text("[prbot]\nconfidence_threshold = 61\n")
        assert load_toml_config(
            None, env_vars={"PRBOT_ALLOW_IMPLICIT_CONFIG": "1"},
        ) == {"confidence_threshold": 61}

    def test_opting_in_does_not_help_inside_ci(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The reviewed tree is on disk there; the opt-in is for local use."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".prbot.toml").write_text("[prbot]\nconfidence_threshold = 61\n")
        assert load_toml_config(
            None,
            env_vars={
                "PRBOT_ALLOW_IMPLICIT_CONFIG": "1",
                "GITHUB_ACTIONS": "true",
            },
        ) == {}

    def test_an_explicit_path_needs_no_opt_in(self, tmp_path: Path) -> None:
        cfg = tmp_path / "c.toml"
        cfg.write_text("[prbot]\nconfidence_threshold = 55\n")
        assert load_toml_config(str(cfg), env_vars={}) == {
            "confidence_threshold": 55,
        }


class TestAgentRosterFromEnvironment:
    """The roster was unreachable in CI, so no repository could add an agent.

    `agents` is a list of objects, so the comma-splitting used for the other
    list fields cannot express it, and `config.py` refuses the implicit TOML
    search when it detects CI. Between them a containerised run was pinned to
    the shipped roster with no supported way to change it.
    """

    def test_agents_can_be_set_from_the_environment_as_json(self) -> None:
        config = build_config(
            cli_args={"platform": "github", "repo": "o/r", "pr_number": 1},
            env_vars={
                "PRBOT_AGENTS": (
                    '[{"name": "iac", "check_prefix": "IAC-"},'
                    ' {"name": "security", "check_prefix": "S-"}]'
                ),
            },
            toml_config={},
        )
        assert config.agents is not None
        assert [a.name for a in config.agents] == ["iac", "security"]
        assert config.agents[0].check_prefix == "IAC-"

    def test_invalid_json_is_rejected_with_the_variable_named(self) -> None:
        with pytest.raises(ConfigError, match="PRBOT_AGENTS"):
            build_config(
                cli_args={"platform": "github", "repo": "o/r", "pr_number": 1},
                env_vars={"PRBOT_AGENTS": "iac,security"},
                toml_config={},
            )

    def test_a_json_scalar_is_rejected(self) -> None:
        with pytest.raises(ConfigError, match="PRBOT_AGENTS"):
            build_config(
                cli_args={"platform": "github", "repo": "o/r", "pr_number": 1},
                env_vars={"PRBOT_AGENTS": '"iac"'},
                toml_config={},
            )

    def test_environment_beats_toml(self) -> None:
        config = build_config(
            cli_args={"platform": "github", "repo": "o/r", "pr_number": 1},
            env_vars={
                "PRBOT_AGENTS": '[{"name": "iac", "check_prefix": "IAC-"}]',
            },
            toml_config={
                "agents": [{"name": "general", "check_prefix": "Q-"}],
            },
        )
        assert config.agents is not None
        assert [a.name for a in config.agents] == ["iac"]


class TestContextLinesDefault:
    def test_context_is_on_by_default(self) -> None:
        """The retrieval existed and was switched off, so it never ran.

        `get_file_content` is called only when context_lines > 0, so a
        default of 0 meant every agent reviewed naked hunks and was then
        penalised for reasoning about lines it had never been shown.
        """
        config = build_config(
            cli_args={"platform": "github", "repo": "o/r", "pr_number": 1},
            env_vars={},
            toml_config={},
        )
        assert config.context_lines > 0
