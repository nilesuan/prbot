"""Tests for structured logging (story-8-5)."""

from __future__ import annotations

import json

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
