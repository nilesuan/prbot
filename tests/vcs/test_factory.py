"""Tests for adapter selection and API base URL resolution (D2).

vcs/__init__.py was 32% covered, so create_vcs_adapter and the SSRF-checked
base URL resolution were untested despite being the point every run passes
through before it can reach either platform.
"""

from __future__ import annotations

from typing import Any

import pytest

from prbot.auth.token import TokenResult
from prbot.config import PrBotConfig
from prbot.exceptions import ConfigError
from prbot.vcs import _resolve_api_base_url, create_vcs_adapter
from prbot.vcs.github import GitHubAdapter
from prbot.vcs.gitlab import GitLabAdapter


def _config(**overrides: Any) -> PrBotConfig:
    defaults: dict[str, Any] = {
        "platform": "github",
        "repo": "owner/repo",
        "pr_number": 7,
        "general_model_id": "anthropic.claude-sonnet-4-6",
        "security_model_id": "anthropic.claude-sonnet-4-6",
    }
    defaults.update(overrides)
    return PrBotConfig(**defaults)


def _token() -> TokenResult:
    return TokenResult(value="ghp_test", source="test")


class TestAdapterSelection:
    def test_github_platform_gets_the_github_adapter(self) -> None:
        adapter = create_vcs_adapter(_config(platform="github"), _token())
        assert isinstance(adapter, GitHubAdapter)

    def test_gitlab_platform_gets_the_gitlab_adapter(self) -> None:
        adapter = create_vcs_adapter(_config(platform="gitlab"), _token())
        assert isinstance(adapter, GitLabAdapter)

    def test_both_adapters_satisfy_the_protocol(self) -> None:
        from prbot.vcs.protocol import VCSAdapter

        for platform in ("github", "gitlab"):
            adapter = create_vcs_adapter(_config(platform=platform), _token())
            assert isinstance(adapter, VCSAdapter)


class TestBaseUrlResolution:
    def test_platform_default_for_github(self) -> None:
        assert _resolve_api_base_url(_config()) == "https://api.github.com"

    def test_platform_default_for_gitlab(self) -> None:
        url = _resolve_api_base_url(_config(platform="gitlab"))
        assert url == "https://gitlab.com"

    def test_explicit_config_wins(self) -> None:
        config = _config(api_base_url="https://ghe.example.com/api/v3")
        assert _resolve_api_base_url(config) == "https://ghe.example.com/api/v3"

    def test_ci_variable_is_used_when_config_is_silent(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GITHUB_API_URL", "https://ghe.example.com/api/v3")
        assert _resolve_api_base_url(_config()) == (
            "https://ghe.example.com/api/v3"
        )

    def test_gitlab_ci_variable_has_its_api_suffix_stripped(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CI_API_V4_URL includes /api/v4, which the adapter adds itself."""
        monkeypatch.setenv(
            "CI_API_V4_URL", "https://gitlab.example.com/api/v4",
        )
        url = _resolve_api_base_url(_config(platform="gitlab"))
        assert url == "https://gitlab.example.com"

    def test_a_ci_variable_pointing_at_metadata_is_refused(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GITHUB_API_URL", "https://169.254.169.254/latest")
        with pytest.raises(ConfigError, match="SSRF"):
            _resolve_api_base_url(_config())

    def test_the_http_escape_hatch_does_not_open_the_metadata_endpoint(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """PRBOT_ALLOW_HTTP relaxes the scheme check, nothing else."""
        monkeypatch.setenv("PRBOT_ALLOW_HTTP", "1")
        monkeypatch.setenv("GITHUB_API_URL", "http://169.254.169.254/latest")
        with pytest.raises(ConfigError, match="SSRF"):
            _resolve_api_base_url(_config())

    def test_the_http_escape_hatch_does_not_open_private_addresses(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("PRBOT_ALLOW_HTTP", "1")
        monkeypatch.setenv("GITHUB_API_URL", "http://10.0.0.5/api/v3")
        with pytest.raises(ConfigError, match="SSRF"):
            _resolve_api_base_url(_config())

    def test_a_ci_variable_pointing_at_a_private_address_is_refused(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GITHUB_API_URL", "https://10.0.0.5/api/v3")
        with pytest.raises(ConfigError, match="SSRF"):
            _resolve_api_base_url(_config())

    def test_a_ci_variable_over_plain_http_is_refused(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("PRBOT_ALLOW_HTTP", raising=False)
        monkeypatch.setenv("GITHUB_API_URL", "http://ghe.example.com/api/v3")
        with pytest.raises(ConfigError, match="HTTPS"):
            _resolve_api_base_url(_config())

    def test_trailing_slash_is_normalised(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GITHUB_API_URL", "https://ghe.example.com/api/v3/")
        assert _resolve_api_base_url(_config()) == (
            "https://ghe.example.com/api/v3"
        )
