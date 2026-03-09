"""Structured logging with structlog (story-8-1).

JSON output to stdout. Token patterns redacted. Context
variables for repo/PR/commit/review_id correlation.
"""

from __future__ import annotations

import logging
import re

import structlog

# Patterns to redact from log values
_TOKEN_PATTERNS = re.compile(
    r"(?:"
    r"ghp_[A-Za-z0-9_]{36,}"          # GitHub PAT
    r"|glpat-[A-Za-z0-9_-]{20,}"      # GitLab PAT
    r"|(?:AKIA|ASIA)[A-Z0-9]{16}"     # AWS access key
    r"|Bearer\s+[A-Za-z0-9_.=-]+"     # Bearer tokens
    r")",
)

_REDACTED = "<REDACTED>"


def _redact_processor(
    _logger: object,
    _method_name: str,
    event_dict: dict[str, object],
) -> dict[str, object]:
    """Scan string values and redact token patterns."""
    for key, value in event_dict.items():
        if isinstance(value, str):
            event_dict[key] = _TOKEN_PATTERNS.sub(_REDACTED, value)
    return event_dict


def configure_logging(log_level: str = "INFO") -> None:
    """Configure structlog for JSON output to stdout.

    Processor chain:
    1. contextvars (for review context)
    2. add_log_level
    3. TimeStamper (ISO)
    4. StackInfoRenderer
    5. format_exc_info
    6. UnicodeDecoder
    7. _redact_processor (token scrubbing)
    8. JSONRenderer
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            _redact_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )

    # Configure stdlib logging for boto3/httpx
    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, log_level.upper(), logging.INFO),
    )


def bind_review_context(
    repo: str,
    pr_number: int,
    commit_sha: str,
    review_id: str | None = None,
) -> None:
    """Bind review context to structlog contextvars."""
    ctx: dict[str, object] = {
        "repo": repo,
        "pr_number": pr_number,
        "commit_sha": commit_sha,
    }
    if review_id is not None:
        ctx["review_id"] = review_id
    structlog.contextvars.bind_contextvars(**ctx)


def clear_review_context() -> None:
    """Clear all bound context variables."""
    structlog.contextvars.clear_contextvars()
