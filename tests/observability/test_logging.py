"""Tests for structured logging (story-8-5)."""

from __future__ import annotations

import json
from typing import ClassVar

import structlog

from prbot.observability.logging import (
    _redact_processor,
    bind_review_context,
    clear_review_context,
    configure_logging,
)


class TestRedactProcessor:
    """Tests for token redaction in log values."""

    def test_redacts_github_pat(self) -> None:
        event = {"token": "ghp_abc123def456ghi789jkl012mno345pqr678"}
        result = _redact_processor(None, "", event)
        assert "<REDACTED>" in result["token"]
        assert "ghp_" not in result["token"]

    def test_redacts_gitlab_pat(self) -> None:
        event = {"token": "glpat-abcdef1234567890abcd"}
        result = _redact_processor(None, "", event)
        assert "<REDACTED>" in result["token"]

    def test_redacts_aws_key(self) -> None:
        event = {"key": "AKIAIOSFODNN7EXAMPLE"}
        result = _redact_processor(None, "", event)
        assert "<REDACTED>" in result["key"]

    def test_redacts_bearer_token(self) -> None:
        event = {"auth": "Bearer eyJhbGciOiJIUzI1NiJ9.abc"}
        result = _redact_processor(None, "", event)
        assert "<REDACTED>" in result["auth"]

    def test_preserves_non_sensitive(self) -> None:
        event = {"msg": "hello world", "count": 42}
        result = _redact_processor(None, "", event)
        assert result["msg"] == "hello world"
        assert result["count"] == 42


class TestConfigureLogging:
    """Tests for logging configuration."""

    def test_configures_structlog(self, capsys: object) -> None:
        configure_logging("INFO")
        log = structlog.get_logger()
        log.info("test.event", key="value")
        # structlog is configured — no assertion on output
        # format since PrintLoggerFactory goes to stdout

    def test_debug_level(self) -> None:
        configure_logging("DEBUG")
        log = structlog.get_logger()
        # Should not raise
        log.debug("debug.event", detail="test")


class TestReviewContext:
    """Tests for context variable binding."""

    def setup_method(self) -> None:
        configure_logging("INFO")
        clear_review_context()

    def teardown_method(self) -> None:
        clear_review_context()

    def test_bind_and_clear(self, capsys: object) -> None:
        bind_review_context(
            "owner/repo", 42,
            "abcdef1234567890abcdef1234567890abcdef12",
            review_id="test-uuid",
        )
        log = structlog.get_logger()
        log.info("test.bound")
        captured = capsys.readouterr()
        data = json.loads(captured.out.strip())
        assert data["repo"] == "owner/repo"
        assert data["pr_number"] == 42
        assert data["review_id"] == "test-uuid"

    def test_clear_removes_context(self, capsys: object) -> None:
        bind_review_context("owner/repo", 42, "abc" * 14)
        clear_review_context()
        log = structlog.get_logger()
        log.info("test.cleared")
        captured = capsys.readouterr()
        data = json.loads(captured.out.strip())
        assert "repo" not in data


class TestStdlibLoggingIsRedacted:
    """A3: stdlib records must take the same path as structlog records.

    Sixteen modules use logging.getLogger and two use structlog.get_logger.
    configure_logging previously wired the redaction processor into structlog
    only and then called logging.basicConfig, so the pipeline's own log lines
    went to stderr as unredacted plain text.
    """

    @staticmethod
    def _emit(capsys, message: str, level: str = "INFO") -> tuple[str, str]:
        import logging as stdlib_logging

        configure_logging(level)
        stdlib_logging.getLogger("prbot.test").info(message)
        for handler in stdlib_logging.getLogger().handlers:
            handler.flush()
        captured = capsys.readouterr()
        return captured.out, captured.err

    def test_stdlib_records_go_to_stdout(self, capsys) -> None:
        out, err = self._emit(capsys, "hello from the pipeline")
        assert "hello from the pipeline" in out
        assert "hello from the pipeline" not in err

    def test_stdlib_records_are_json(self, capsys) -> None:
        out, _ = self._emit(capsys, "structured please")
        payload = json.loads(out.strip().splitlines()[-1])
        assert payload["event"] == "structured please"
        assert payload["level"] == "info"
        assert "timestamp" in payload

    def test_stdlib_records_are_redacted(self, capsys) -> None:
        secret = "ghs_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        out, err = self._emit(capsys, f"auth token={secret}")
        assert secret not in out
        assert secret not in err
        assert "REDACTED" in out

    def test_review_context_reaches_stdlib_records(self, capsys) -> None:
        import logging as stdlib_logging

        configure_logging("INFO")
        bind_review_context("o/r", 7, "a" * 40, review_id="rid-1")
        try:
            stdlib_logging.getLogger("prbot.test").info("with context")
            for handler in stdlib_logging.getLogger().handlers:
                handler.flush()
            payload = json.loads(
                capsys.readouterr().out.strip().splitlines()[-1],
            )
        finally:
            clear_review_context()
        assert payload["repo"] == "o/r"
        assert payload["pr_number"] == 7
        assert payload["review_id"] == "rid-1"

    def test_configure_logging_is_idempotent(self, capsys) -> None:
        """Calling it twice must not duplicate every line."""
        import logging as stdlib_logging

        configure_logging("INFO")
        configure_logging("INFO")
        stdlib_logging.getLogger("prbot.test").info("once only")
        for handler in stdlib_logging.getLogger().handlers:
            handler.flush()
        out = capsys.readouterr().out
        assert out.count("once only") == 1

    def test_boto_wire_logging_stays_capped(self, capsys) -> None:
        """botocore DEBUG prints request signing material."""
        import logging as stdlib_logging

        configure_logging("DEBUG")
        assert stdlib_logging.getLogger("botocore").level >= stdlib_logging.INFO
        assert stdlib_logging.getLogger("urllib3").level >= stdlib_logging.INFO


class TestRedactorCoversEveryKnownTokenShape:
    """A3: the structlog list omitted shapes auth/token.py already knew."""

    SAMPLES: ClassVar[list[str]] = [
        "ghp_" + "a" * 36,
        "ghs_" + "b" * 36,
        "gho_" + "c" * 36,
        "github_pat_" + "d" * 30,
        "glpat-" + "e" * 20,
        "AKIAIOSFODNN7EXAMPLE",
        "ASIAIOSFODNN7EXAMPLE",
    ]

    def test_every_shape_is_redacted(self) -> None:
        for sample in self.SAMPLES:
            result = _redact_processor(
                None, "", {"event": f"saw {sample} in a header"},
            )
            assert sample not in result["event"], (
                f"{sample[:12]}... survived redaction"
            )
