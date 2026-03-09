"""Exception hierarchy with exit code mapping (overview section 12).

Exit codes:
    2 — Configuration / user error (fixable by the caller)
    3 — Infrastructure / runtime error (transient or external)
"""

from __future__ import annotations


class PrBotError(Exception):
    """Base exception for all prbot errors."""

    exit_code: int = 3

    def __init__(self, message: str) -> None:
        super().__init__(message)


# --- Configuration ---


class ConfigError(PrBotError):
    """Invalid configuration (bad TOML, missing required fields, validation failure)."""

    exit_code = 2


# --- Authentication ---


class AuthError(PrBotError):
    """Authentication failure (missing token, Secrets Manager unreachable)."""

    exit_code = 3


class InsufficientScopesError(AuthError):
    """Token lacks required scopes (e.g., missing repo or api scope)."""

    exit_code = 2


# --- VCS ---


class VCSError(PrBotError):
    """VCS API communication failure."""

    exit_code = 3


class VCSAuthError(VCSError):
    """VCS API returned 401/403 — token invalid or insufficient permissions."""

    exit_code = 2


class VCSNotFoundError(VCSError):
    """VCS API returned 404 — repo or PR not found."""

    exit_code = 2


class VCSRateLimitError(VCSError):
    """VCS API returned 429 — rate limit exceeded."""

    exit_code = 3


class VCSServerError(VCSError):
    """VCS API returned 5xx — server-side failure."""

    exit_code = 3


class VCSResponseError(VCSError):
    """VCS API returned an unexpected response shape."""

    exit_code = 3


# --- Review Pipeline ---


class ReviewError(PrBotError):
    """Review pipeline failure."""

    exit_code = 3


class BudgetExceededError(ReviewError):
    """Estimated cost exceeds budget_limit_usd."""

    exit_code = 2


class DiffTooLargeError(ReviewError):
    """Diff exceeds max_diff_tokens limit."""

    exit_code = 2


class TimeoutBudgetExhausted(ReviewError):
    """Pipeline timeout budget exhausted during review."""

    exit_code = 3


# --- AWS Bedrock ---


class BedrockError(PrBotError):
    """AWS Bedrock API failure (throttling, model error, timeout)."""

    exit_code = 3
