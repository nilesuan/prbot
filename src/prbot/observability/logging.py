"""Structured logging with structlog (story-8-1).

JSON output to stdout. Credential patterns redacted. Context variables for
repo/PR/commit/review_id correlation.

A3: stdlib records take the same path as structlog records. Sixteen modules
in this package use logging.getLogger and two use structlog.get_logger. When
the redaction processor was wired into structlog alone and stdlib logging was
left on basicConfig, the great majority of prbot's own log lines went to
stderr as unredacted plain text.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from prbot.security.redaction import redact_secrets

_REDACTED = "<REDACTED>"

# Third-party loggers that print request signing material at DEBUG. These are
# capped regardless of the configured level: PRBOT_LOG_LEVEL=DEBUG is meant to
# show more of prbot, not to dump credential derivation from botocore.
_CAPPED_LOGGERS = ("boto3", "botocore", "urllib3", "httpx", "httpcore", "s3transfer")
_CAP_LEVEL = logging.INFO


def redact_secrets_in_text(text: str) -> str:
    """Replace every known credential shape in a string.

    Shares SECRET_PATTERNS with the comment redactor so a shape can never be
    known to one and unknown to the other, which is how ghs_, gho_ and
    github_pat_ came to be redacted from review comments but not from logs.
    """
    return redact_secrets(text)[0]


def _redact_value(value: Any) -> Any:
    """Redact credential patterns in a string, or in every string inside."""
    if isinstance(value, str):
        return redact_secrets_in_text(value)
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_redact_value(v) for v in value)
    return value


def _redact_processor(
    _logger: object,
    _method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Scan values and redact credential patterns.

    Nested values too (SEC-LOG-01): the audit record's findings arrive as a
    list of dicts, and a top-level scan passed them through as they were.
    """
    for key, value in event_dict.items():
        event_dict[key] = _redact_value(value)
    return event_dict


class _StdoutHandler(logging.StreamHandler):
    """Write to whatever sys.stdout is at emit time.

    logging.StreamHandler binds its stream at construction. Resolving it
    lazily keeps output going to the real stdout if the stream is replaced
    after configure_logging has run, which is what an embedding process or a
    test harness does.
    """

    _prbot_handler = True

    def __init__(self) -> None:
        super().__init__(stream=sys.stdout)

    @property
    def stream(self) -> Any:
        return sys.stdout

    @stream.setter
    def stream(self, _value: Any) -> None:
        """Ignore assignment; StreamHandler.__init__ and setStream use it."""


def _shared_processors() -> list[Any]:
    """Processor chain applied to structlog and stdlib records alike."""
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        _redact_processor,
    ]


def configure_logging(log_level: str = "INFO") -> None:
    """Configure structlog and stdlib logging for JSON output to stdout.

    Both paths run the same processor chain, so a record emitted through
    logging.getLogger is redacted, timestamped and correlated exactly like
    one emitted through structlog.get_logger.

    Safe to call more than once: the root handler is replaced, not appended.
    """
    shared = _shared_processors()

    structlog.configure(
        processors=[
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        # Records that did not come from structlog are pushed through the
        # same chain before rendering.
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    handler = _StdoutHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    # Replace only handlers this module installed. Handlers belonging to a
    # test harness or an embedding application are left alone, so calling
    # configure_logging twice cannot double up our own output and cannot
    # tear down someone else's capture.
    for existing in list(root.handlers):
        if getattr(existing, "_prbot_handler", False):
            root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    for name in _CAPPED_LOGGERS:
        logging.getLogger(name).setLevel(
            max(_CAP_LEVEL, root.level),
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
