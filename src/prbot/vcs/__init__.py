"""VCS adapter layer (GitHub + GitLab)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from prbot.config import _validate_url_not_internal
from prbot.exceptions import ConfigError
from prbot.vcs.protocol import VCSAdapter

if TYPE_CHECKING:
    from prbot.auth.token import TokenResult
    from prbot.config import PrBotConfig

__all__ = ["VCSAdapter", "create_vcs_adapter"]

# CI environment variables for API base URLs
_CI_API_VARS: dict[str, str] = {
    "github": "GITHUB_API_URL",
    "gitlab": "CI_API_V4_URL",
}

_DEFAULT_API_URLS: dict[str, str] = {
    "github": "https://api.github.com",
    "gitlab": "https://gitlab.com",
}


def _resolve_api_base_url(config: PrBotConfig) -> str:
    """Resolve API base URL with SSRF validation (G4-10).

    Priority: config.api_base_url > CI env var > platform default.
    SSRF validation applied to CI env var URLs.
    """
    # Highest priority: explicit config
    if config.api_base_url:
        return config.api_base_url  # Already SSRF-validated by config validator

    # CI environment variable
    env_var = _CI_API_VARS.get(config.platform)
    if env_var:
        env_url = os.environ.get(env_var)
        if env_url:
            _validate_url_not_internal(env_url)
            return env_url.rstrip("/")

    # Platform default
    default = _DEFAULT_API_URLS.get(config.platform)
    if default:
        return default

    raise ConfigError(f"No API URL for platform: {config.platform}")


def create_vcs_adapter(config: PrBotConfig, token: TokenResult) -> VCSAdapter:
    """Create the appropriate VCS adapter based on platform config.

    Returns GitHubAdapter or GitLabAdapter.
    """
    base_url = _resolve_api_base_url(config)

    if config.platform == "github":
        from prbot.vcs.github import GitHubAdapter

        return GitHubAdapter(
            token=token,
            repo=config.repo,
            pr_number=config.pr_number,
            base_url=base_url,
        )

    if config.platform == "gitlab":
        from prbot.vcs.gitlab import GitLabAdapter

        return GitLabAdapter(
            token=token,
            repo=config.repo,
            pr_number=config.pr_number,
            base_url=base_url,
        )

    raise ConfigError(f"Unsupported platform: {config.platform}")
