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
        # A9, GitHub half. secrets.GITHUB_TOKEN is an app installation token
        # and GET /user is not available to one: GitHub answers 403, not the
        # 200 the scope-header logic below assumed. A 401 means the
        # credential is bad; a 403 means it authenticated and this endpoint
        # is simply not for it, which says nothing about scopes. The GitLab
        # path already makes the same allowance for CI_JOB_TOKEN.
        #
        # GitHub also answers 403 when the primary rate limit is exhausted,
        # so only skip when the budget is not the reason.
        if response.headers.get("x-ratelimit-remaining") == "0":
            raise AuthError(
                "Token scope validation failed: GitHub API rate limit "
                "exceeded (403)"
            )
        logger.info(
            "Token cannot introspect its own scopes (HTTP 403, installation "
            "or otherwise restricted token); skipping scope check",
        )
        return
    if response.status_code >= 400:
        raise AuthError(
            f"Token scope validation failed: HTTP {response.status_code}"
        )

    # A9: only OAuth tokens and classic PATs report scopes. A GitHub Actions
    # GITHUB_TOKEN is an app installation token and a fine-grained PAT is not
    # an OAuth token; neither sends X-OAuth-Scopes, and neither is missing
    # anything. Treating silence as "no scopes granted" would fail every
    # GitHub Actions run. The 200 above already proves the token authenticates;
    # what it can reach is then enforced by the API itself.
    if "x-oauth-scopes" not in response.headers:
        logger.info(
            "Token does not report OAuth scopes (installation or "
            "fine-grained token); skipping scope check",
        )
        return

    scopes_header = response.headers.get("x-oauth-scopes", "")
    granted = {s.strip() for s in scopes_header.split(",") if s.strip()}

    if not granted:
        logger.info(
            "Token reports an empty scope list (fine-grained token); "
            "skipping scope check",
        )
        return

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

    # A9: a CI_JOB_TOKEN is not a personal access token and cannot
    # introspect itself, and older self-hosted GitLab has no such endpoint.
    # Neither case means the token lacks scopes.
    if response.status_code in (401, 403, 404):
        logger.info(
            "Token cannot introspect its own scopes (HTTP %d); skipping "
            "scope check",
            response.status_code,
        )
        return
    if response.status_code >= 400:
        raise AuthError(
            f"Token scope validation failed: HTTP {response.status_code}"
        )

    try:
        data = response.json()
    except ValueError:
        logger.info("Scope endpoint returned a non-JSON body; skipping check")
        return
    if not isinstance(data, dict) or "scopes" not in data:
        logger.info("Scope endpoint did not report scopes; skipping check")
        return
    granted = set(data.get("scopes", []))

    missing = required - granted
    if missing:
        raise InsufficientScopesError(
            f"GitLab token missing required scopes: {sorted(missing)}. "
            f"Granted: {sorted(granted)}"
        )
    logger.info("GitLab token scopes validated: %s", sorted(granted))
