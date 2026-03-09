"""Authentication and token management."""

from prbot.auth.credentials import validate_aws_session_credentials
from prbot.auth.scope import validate_token_scopes
from prbot.auth.token import (
    TOKEN_PATTERNS,
    TokenResult,
    redact_tokens_from_string,
    redact_tokens_processor,
    resolve_token,
)

__all__ = [
    "TOKEN_PATTERNS",
    "TokenResult",
    "redact_tokens_from_string",
    "redact_tokens_processor",
    "resolve_token",
    "validate_aws_session_credentials",
    "validate_token_scopes",
]
