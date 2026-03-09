"""Tests for secret and PII redaction (story-5-5, story-6-7)."""

from __future__ import annotations

from prbot.security.redaction import (
    contains_secrets,
    redact_pii,
    redact_secrets,
)


class TestRedactSecrets:
    """Tests for secret redaction patterns."""

    def test_aws_access_key(self) -> None:
        text = "key: AKIAIOSFODNN7EXAMPLE"
        result, count = redact_secrets(text)
        assert "[REDACTED]" in result
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert count >= 1

    def test_github_classic_pat(self) -> None:
        text = "token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh1234"
        result, count = redact_secrets(text)
        assert "[REDACTED]" in result
        assert count >= 1

    def test_github_fine_grained_pat(self) -> None:
        text = "token: github_pat_abcdefghijklmnopqrstuvwxyz"
        result, count = redact_secrets(text)
        assert "[REDACTED]" in result
        assert count >= 1

    def test_gitlab_pat(self) -> None:
        text = "token: glpat-ABCDEFGHIJKLMNOPqrst"
        result, count = redact_secrets(text)
        assert "[REDACTED]" in result
        assert count >= 1

    def test_private_key_header(self) -> None:
        text = "-----BEGIN RSA PRIVATE KEY-----"
        result, count = redact_secrets(text)
        assert "[REDACTED]" in result
        assert count >= 1

    def test_bearer_token(self) -> None:
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.test.sig"
        result, count = redact_secrets(text)
        assert "[REDACTED]" in result
        assert count >= 1

    def test_connection_string(self) -> None:
        text = "postgres://user:password123@db.example.com:5432/mydb"
        result, count = redact_secrets(text)
        assert "[REDACTED]" in result
        assert count >= 1

    def test_no_secrets_unchanged(self) -> None:
        text = "This is normal text with no secrets."
        result, count = redact_secrets(text)
        assert result == text
        assert count == 0

    def test_aws_secret_key_requires_context(self) -> None:
        """AWS secret key regex requires label context (GAP-8)."""
        # Plain base64-like string should NOT match
        text = "hash: a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0"
        _, count = redact_secrets(text)
        assert count == 0

    def test_aws_secret_key_with_context(self) -> None:
        text = 'aws_secret_access_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"'
        result, count = redact_secrets(text)
        assert "[REDACTED]" in result
        assert count >= 1

    def test_multiple_secrets(self) -> None:
        text = (
            "AKIAIOSFODNN7EXAMPLE and "
            "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh1234"
        )
        _result, count = redact_secrets(text)
        assert count >= 2

    def test_contains_secrets_true(self) -> None:
        assert contains_secrets("AKIAIOSFODNN7EXAMPLE")

    def test_contains_secrets_false(self) -> None:
        assert not contains_secrets("normal text")


class TestRedactPii:
    """Tests for PII redaction patterns."""

    def test_email_redacted(self) -> None:
        text = "Contact user@example.com for help"
        result, count = redact_pii(text)
        assert "[PII REDACTED]" in result
        assert "user@example.com" not in result
        assert count == 1

    def test_ip_address_redacted(self) -> None:
        text = "Server at 203.0.113.1"
        result, count = redact_pii(text)
        assert "[PII REDACTED]" in result
        assert "203.0.113.1" not in result
        assert count == 1

    def test_loopback_ip_exempt(self) -> None:
        text = "localhost at 127.0.0.1"
        result, count = redact_pii(text)
        assert "127.0.0.1" in result
        assert count == 0

    def test_phone_redacted(self) -> None:
        text = "Call +1-555-123-4567"
        result, count = redact_pii(text)
        assert "[PII REDACTED]" in result
        assert count >= 1

    def test_no_pii_unchanged(self) -> None:
        text = "No PII here."
        result, count = redact_pii(text)
        assert result == text
        assert count == 0

    def test_multiple_pii_types(self) -> None:
        text = "Email: user@test.com, IP: 10.0.0.1"
        _result, count = redact_pii(text)
        assert count >= 2

    def test_zero_ip_exempt(self) -> None:
        text = "bind to 0.0.0.0"
        result, count = redact_pii(text)
        assert "0.0.0.0" in result
        assert count == 0
