"""Token resolution with validate-then-fallback pattern (story-2-1).

Resolution priority:
    1. Environment variable (GH_TOKEN / GITLAB_TOKEN)
    2. AWS Secrets Manager fallback (if secret_name configured)

Validate-then-fallback (S6): If env var is present but invalid,
raise immediately — do NOT fall back to Secrets Manager.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from typing import TYPE_CHECKING, Any

from prbot.exceptions import AuthError

if TYPE_CHECKING:
    from prbot.config import PrBotConfig

logger = logging.getLogger(__name__)

# --- Token patterns for redaction ---

TOKEN_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ghp_[a-zA-Z0-9]{36,}"),          # GitHub PAT (classic)
    re.compile(r"github_pat_[a-zA-Z0-9_]{82,}"),   # GitHub PAT (fine-grained)
    re.compile(r"ghs_[a-zA-Z0-9]{36,}"),           # GitHub App token
    re.compile(r"gho_[a-zA-Z0-9]{36,}"),           # GitHub OAuth token
    re.compile(r"glpat-[a-zA-Z0-9_-]{20,}"),       # GitLab PAT
]

# Env var names per platform
_PLATFORM_TOKEN_VARS: dict[str, list[str]] = {
    "github": ["GH_TOKEN", "GITHUB_TOKEN"],
    "gitlab": ["GITLAB_TOKEN", "CI_JOB_TOKEN"],
}


def redact_tokens_from_string(text: str) -> str:
    """Replace all recognized token patterns in text with <REDACTED>."""
    for pattern in TOKEN_PATTERNS:
        text = pattern.sub("<REDACTED>", text)
    return text


class TokenResult:
    """Wrapper for VCS tokens that never exposes values in repr/str.

    Access the actual token only through explicit methods:
    - as_bearer_header() → "Bearer {token}"
    - as_private_token() → the raw token (for GitLab PRIVATE-TOKEN header)
    - reveal() → the raw token (explicit opt-in)
    """

    __slots__ = ("_source", "_value")

    def __init__(self, value: str, source: str) -> None:
        if not value or not value.strip():
            raise AuthError(f"Token from {source} is empty")
        self._value = value
        self._source = source

    @property
    def source(self) -> str:
        """Where the token was resolved from (e.g., 'env:GH_TOKEN')."""
        return self._source

    @property
    def redacted(self) -> str:
        """First 4 characters followed by '...' for diagnostics."""
        if len(self._value) <= 4:
            return "****"
        return self._value[:4] + "..."

    def as_bearer_header(self) -> str:
        """Return 'Bearer {token}' for Authorization header."""
        return f"Bearer {self._value}"

    def as_private_token(self) -> str:
        """Return raw token for GitLab PRIVATE-TOKEN header."""
        return self._value

    def reveal(self) -> str:
        """Return the raw token value. Use with caution."""
        return self._value

    def mask_in_ci(self) -> None:
        """Write ::add-mask:: to stderr if running in GitHub Actions (G4-07).

        This prevents the token value from appearing in CI log output.
        """
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(f"::add-mask::{self._value}", file=sys.stderr)

    def __repr__(self) -> str:
        return f"TokenResult(source={self._source!r}, value=<REDACTED>)"

    def __str__(self) -> str:
        return f"TokenResult({self._source}, <REDACTED>)"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, TokenResult):
            return self._value == other._value and self._source == other._source
        return NotImplemented

    def __hash__(self) -> int:
        return hash((self._value, self._source))


async def resolve_token(config: PrBotConfig) -> TokenResult:
    """Resolve VCS token using validate-then-fallback pattern (S6).

    Priority:
        1. Platform-specific env var (GH_TOKEN, GITLAB_TOKEN, etc.)
        2. Secrets Manager fallback (if config.secret_name is set)

    If an env var is present but empty/invalid, raises immediately
    without attempting Secrets Manager fallback.
    """
    # Try platform-specific env vars
    var_names = _PLATFORM_TOKEN_VARS.get(config.platform, [])

    for var_name in var_names:
        value = os.environ.get(var_name)
        if value is not None:
            # Validate-then-fallback: present but invalid → fail fast
            if not value.strip():
                raise AuthError(
                    f"{var_name} is set but empty. "
                    "Remove it or set a valid token."
                )
            logger.info("Token resolved from environment variable %s", var_name)
            return TokenResult(value=value.strip(), source=f"env:{var_name}")

    # Fallback to Secrets Manager
    if config.secret_name:
        logger.info(
            "No env var token found, trying Secrets Manager: %s",
            config.secret_name,
        )
        value = await _fetch_from_secrets_manager(
            secret_name=config.secret_name,
            region=config.aws_region,
        )
        return TokenResult(value=value, source=f"secretsmanager:{config.secret_name}")

    raise AuthError(
        f"No VCS token found. Set one of {var_names} "
        f"or configure secret_name in .prbot.toml"
    )


async def _fetch_from_secrets_manager(
    secret_name: str,
    region: str,
    timeout_seconds: float = 5.0,
) -> str:
    """Fetch token from AWS Secrets Manager with timeout.

    Uses boto3 synchronous client wrapped in asyncio.to_thread
    to avoid blocking the event loop. 5s connect+read timeout.

    Raises AuthError with classified error type on failure.
    """
    import asyncio

    import boto3
    from botocore.config import Config as BotoConfig
    from botocore.exceptions import ClientError

    def _sync_fetch() -> str:
        client = boto3.client(
            "secretsmanager",
            region_name=region,
            config=BotoConfig(
                connect_timeout=timeout_seconds,
                read_timeout=timeout_seconds,
                retries={"max_attempts": 1},
            ),
        )
        try:
            response = client.get_secret_value(SecretId=secret_name)
            value = response.get("SecretString", "")
            if not value:
                raise AuthError(
                    f"Secret '{secret_name}' exists but has no string value"
                )
            return value
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code == "ResourceNotFoundException":
                raise AuthError(
                    f"Secret '{secret_name}' not found in Secrets Manager "
                    f"(region: {region})"
                ) from e
            if code in ("AccessDeniedException", "UnauthorizedAccess"):
                raise AuthError(
                    f"Access denied to secret '{secret_name}'. "
                    "Check IAM permissions for secretsmanager:GetSecretValue"
                ) from e
            raise AuthError(
                f"Failed to fetch secret '{secret_name}': {code}: {e}"
            ) from e
        except Exception as e:
            raise AuthError(
                f"Failed to fetch secret '{secret_name}' from Secrets Manager: "
                f"{type(e).__name__}: {e}"
            ) from e

    return await asyncio.to_thread(_sync_fetch)


def redact_tokens_processor(
    _logger: Any, _method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Structlog processor that redacts token patterns from all string values (NG-20).

    Scans every string value in the event dict and replaces recognized
    token patterns with <REDACTED>.
    """
    for key, value in event_dict.items():
        if isinstance(value, str):
            event_dict[key] = redact_tokens_from_string(value)
    return event_dict
