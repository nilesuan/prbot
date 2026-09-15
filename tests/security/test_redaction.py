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
        # A routable address. 203.0.113.0/24 is the documentation range and
        # is deliberately exempt now (B6).
        text = "Server at 8.8.8.8"
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
        # 10.0.0.1 is RFC1918 and no longer counts as PII (B6).
        text = "Email: user@test.com, IP: 8.8.4.4"
        _result, count = redact_pii(text)
        assert count >= 2

    def test_zero_ip_exempt(self) -> None:
        text = "bind to 0.0.0.0"
        result, count = redact_pii(text)
        assert "0.0.0.0" in result
        assert count == 0


class TestRedactionLeavesReviewProseAlone:
    """B6: the patterns fired on ordinary review text and missed real PII."""

    def test_the_words_bearer_token_are_not_a_token(self) -> None:
        out, count = redact_secrets(
            "Use a Bearer token from the Authorization header.",
        )
        assert out == "Use a Bearer token from the Authorization header."
        assert count == 0

    def test_a_short_bearer_value_is_not_a_token(self) -> None:
        out, _ = redact_secrets("The header reads Bearer abc, which is wrong.")
        assert "Bearer abc" in out

    def test_a_real_bearer_token_is_still_redacted(self) -> None:
        secret = "Bearer " + "A1b2C3d4E5f6G7h8I9j0K1l2"
        out, count = redact_secrets(f"Sends {secret} upstream")
        assert "A1b2C3d4" not in out
        assert count == 1

    def test_a_labelled_secret_keeps_its_label(self) -> None:
        out, count = redact_secrets("api_key=REPLACE_ME_WITH_REAL_VALUE_X")
        assert out.startswith("api_key=")
        assert "REPLACE_ME_WITH_REAL_VALUE_X" not in out
        assert count == 1

    def test_a_connection_string_keeps_its_scheme(self) -> None:
        out, _ = redact_secrets(
            "postgres://user:hunter2@db/app is hardcoded on line 12.",
        )
        assert out.startswith("postgres://user:")
        assert "hunter2" not in out
        assert "is hardcoded on line 12." in out


class TestPiiRedactionKeepsSecurityFindingsUseful:
    """B6: redacting private IPs destroyed the findings worth reading."""

    def test_rfc1918_address_survives(self) -> None:
        text = "Security group allows 10.0.0.0/8 inbound on port 22."
        out, count = redact_pii(text)
        assert out == text
        assert count == 0

    def test_private_class_c_survives(self) -> None:
        out, _ = redact_pii("Default gateway 192.168.1.1 is hardcoded.")
        assert "192.168.1.1" in out

    def test_documentation_range_survives(self) -> None:
        out, _ = redact_pii("The example uses 203.0.113.5 as the peer.")
        assert "203.0.113.5" in out

    def test_loopback_survives(self) -> None:
        out, _ = redact_pii("Binds 127.0.0.1 only.")
        assert "127.0.0.1" in out

    def test_a_public_address_is_still_redacted(self) -> None:
        out, count = redact_pii("Calls out to 8.8.8.8 on every request.")
        assert "8.8.8.8" not in out
        assert count == 1

    def test_a_hash_is_not_a_phone_number(self) -> None:
        text = "Hash 1234-5678-9012 is computed with MD5."
        out, count = redact_pii(text)
        assert out == text
        assert count == 0

    def test_an_offset_is_not_a_phone_number(self) -> None:
        text = "Use offset 12345678 for the header."
        assert redact_pii(text) == (text, 0)

    def test_a_version_is_not_a_phone_number(self) -> None:
        text = "Schema 2024-01-15 drops the user_id column."
        assert redact_pii(text) == (text, 0)

    def test_a_real_international_number_is_redacted(self) -> None:
        out, count = redact_pii("Escalate to +61 2 9999 8888 out of hours.")
        assert "9999 8888" not in out
        assert count == 1

    def test_an_email_is_still_redacted(self) -> None:
        out, count = redact_pii("Owner is team@example.com per CODEOWNERS.")
        assert "team@example.com" not in out
        assert count == 1


class TestPiiRedactionCoversEveryFindingField:
    """B6: only description was redacted; title and suggestion were not."""

    def test_all_prose_fields_are_redacted(self) -> None:
        from prbot.review.models import Finding
        from prbot.security.redaction import redact_finding_pii

        finding = Finding(
            id="general-1",
            category="general",
            check_id="Q-ERR-01",
            title="Contact alice@example.com about this",
            description="Also bob@example.com",
            file_path="src/app.py",
            line_start=1,
            line_end=1,
            severity="low",
            confidence=80,
            suggestion="Email carol@example.com",
        )
        cleaned, count = redact_finding_pii(finding)
        assert "alice@example.com" not in cleaned.title
        assert "bob@example.com" not in cleaned.description
        assert "carol@example.com" not in cleaned.suggestion
        assert count == 3

    def test_file_path_is_left_intact(self) -> None:
        from prbot.review.models import Finding
        from prbot.security.redaction import redact_finding_pii

        finding = Finding(
            id="general-1",
            category="general",
            check_id="Q-ERR-01",
            title="t",
            description="d",
            file_path="src/10.0.0.1/config.py",
            line_start=1,
            line_end=1,
            severity="low",
            confidence=80,
        )
        cleaned, _ = redact_finding_pii(finding)
        assert cleaned.file_path == "src/10.0.0.1/config.py"
