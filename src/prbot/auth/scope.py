"""Token scope validation with timeout and SSRF guard (story-2-2).

Validates that VCS tokens have the required scopes before use.
GitHub: checks X-OAuth-Scopes header from GET /user.
GitLab: checks scopes from GET /personal_access_tokens/self.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

from prbot.config import _validate_url_not_internal
from prbot.exceptions import AuthError, InsufficientScopesError

if TYPE_CHECKING:
    from prbot.auth.token import TokenResult

logger = logging.getLogger(__name__)

# Required scopes per platform
_REQUIRED_SCOPES: dict[str, set[str]] = {
    "github": {"repo"},
    "gitlab": {"api"},
}

# Default API base URLs per platform
_DEFAULT_API_URLS: dict[str, str] = {
    "github": "https://api.github.com",
    "gitlab": "https://gitlab.com",
}


async def validate_token_scopes(
    token: TokenResult,
    platform: str,
    api_base_url: str | None = None,
    timeout: float = 10.0,
) -> None:
    """Validate that a token has the required scopes for the platform.

    Args:
        token: Resolved token to validate.
        platform: 'github' or 'gitlab'.
        api_base_url: Override API base URL (defaults per platform).
        timeout: HTTP request timeout in seconds.

    Raises:
        InsufficientScopesError: Token lacks required scopes.
        AuthError: HTTP errors (timeout, auth failure, unreachable).
        ConfigError: SSRF — api_base_url points to internal address.
    """
    base_url = api_base_url or _DEFAULT_API_URLS.get(platform, "")
    if not base_url:
        raise AuthError(f"No API base URL for platform: {platform}")

    # SSRF defense-in-depth (NG-32) — re-validate even if config already checked
    _validate_url_not_internal(base_url)

    required = _REQUIRED_SCOPES.get(platform, set())
    if not required:
        logger.info("No scope requirements defined for platform %s", platform)
        return

    if platform == "github":
        await _validate_github_scopes(token, base_url, required, timeout)
    elif platform == "gitlab":
        await _validate_gitlab_scopes(token, base_url, required, timeout)


async def _validate_github_scopes(
    token: TokenResult,
    base_url: str,
    required: set[str],
    timeout: float,
) -> None:
    """Check GitHub token scopes via GET /user response headers."""
    url = f"{base_url.rstrip('/')}/user"
    headers = {"Authorization": token.as_bearer_header()}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=timeout)
    except httpx.TimeoutException as e:
        raise AuthError(
            f"Token scope validation timed out after {timeout}s "
            f"connecting to {base_url}"
        ) from e
    except httpx.ConnectError as e:
        raise AuthError(
            f"Token scope validation failed: {base_url} unreachable"
        ) from e

    if response.status_code == 401:
        raise AuthError("Token scope validation failed: token is invalid (401)")
    if response.status_code == 403:
        raise AuthError("Token scope validation failed: forbidden (403)")
    if response.status_code >= 400:
        raise AuthError(
            f"Token scope validation failed: HTTP {response.status_code}"
        )

    scopes_header = response.headers.get("x-oauth-scopes", "")
    granted = {s.strip() for s in scopes_header.split(",") if s.strip()}

    missing = required - granted
    if missing:
        raise InsufficientScopesError(
            f"GitHub token missing required scopes: {sorted(missing)}. "
            f"Granted: {sorted(granted)}"
        )
    logger.info("GitHub token scopes validated: %s", sorted(granted))


async def _validate_gitlab_scopes(
    token: TokenResult,
    base_url: str,
    required: set[str],
    timeout: float,
) -> None:
    """Check GitLab token scopes via GET /personal_access_tokens/self."""
    url = f"{base_url.rstrip('/')}/api/v4/personal_access_tokens/self"
    headers = {"PRIVATE-TOKEN": token.as_private_token()}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=timeout)
    except httpx.TimeoutException as e:
        raise AuthError(
            f"Token scope validation timed out after {timeout}s "
            f"connecting to {base_url}"
        ) from e
    except httpx.ConnectError as e:
        raise AuthError(
            f"Token scope validation failed: {base_url} unreachable"
        ) from e

    if response.status_code == 401:
        raise AuthError("Token scope validation failed: token is invalid (401)")
    if response.status_code == 403:
        raise AuthError("Token scope validation failed: forbidden (403)")
    if response.status_code >= 400:
        raise AuthError(
            f"Token scope validation failed: HTTP {response.status_code}"
        )

    data = response.json()
    granted = set(data.get("scopes", []))

    missing = required - granted
    if missing:
        raise InsufficientScopesError(
            f"GitLab token missing required scopes: {sorted(missing)}. "
            f"Granted: {sorted(granted)}"
        )
    logger.info("GitLab token scopes validated: %s", sorted(granted))
