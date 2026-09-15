"""Tests for auth module (story-2-4).

Covers: token resolution, scope validation, exposure prevention,
structlog redaction, OIDC detection, and Secrets Manager fallback.
"""

from __future__ import annotations

import logging
import os
from unittest.mock import patch

import httpx
import pytest
import respx

from prbot.auth.credentials import validate_aws_session_credentials
from prbot.auth.token import (
    TokenResult,
    redact_tokens_from_string,
    resolve_token,
)
from prbot.config import PrBotConfig
from prbot.exceptions import AuthError, InsufficientScopesError

_GH_VARS = ("GH_TOKEN", "GITHUB_TOKEN")


def _make_config(**overrides: object) -> PrBotConfig:
    """Create a minimal PrBotConfig for testing."""
    defaults: dict[str, object] = {
        "platform": "github",
        "repo": "owner/repo",
        "pr_number": 1,
    }
    defaults.update(overrides)
    return PrBotConfig(**defaults)  # type: ignore[arg-type]


def _env_without_gh() -> dict[str, str]:
    """Return current env without GitHub token vars."""
    return {
        k: v for k, v in os.environ.items()
        if k not in _GH_VARS
    }


class TestResolveToken:
    """Test resolve_token with validate-then-fallback pattern."""

    @pytest.mark.asyncio
    async def test_env_var_github(self) -> None:
        config = _make_config(platform="github")
        with patch.dict(os.environ, {"GH_TOKEN": "ghp_test123"}):
            token = await resolve_token(config)
            assert token.reveal() == "ghp_test123"
            assert token.source == "env:GH_TOKEN"

    @pytest.mark.asyncio
    async def test_env_var_gitlab(self) -> None:
        config = _make_config(platform="gitlab")
        with patch.dict(os.environ, {"GITLAB_TOKEN": "glpat-test"}):
            token = await resolve_token(config)
            assert token.reveal() == "glpat-test"
            assert token.source == "env:GITLAB_TOKEN"

    @pytest.mark.asyncio
    async def test_invalid_env_var_no_fallback(self) -> None:
        """Present but empty env var should fail fast."""
        config = _make_config(
            platform="github", secret_name="prbot/github-token"
        )
        with (
            patch.dict(os.environ, {"GH_TOKEN": "  "}),
            pytest.raises(AuthError, match="empty"),
        ):
            await resolve_token(config)

    @pytest.mark.asyncio
    async def test_absent_falls_back_to_sm(
        self, mock_boto3: object,
    ) -> None:
        """No env var should fall back to Secrets Manager."""
        config = _make_config(
            platform="github",
            secret_name="prbot/github-token",
        )
        with patch.dict(os.environ, _env_without_gh(), clear=True):
            token = await resolve_token(config)
            assert "ghp_test_secret" in token.reveal()
            assert "secretsmanager:" in token.source

    @pytest.mark.asyncio
    async def test_no_source_raises(self) -> None:
        """No env var and no secret_name should raise."""
        config = _make_config(platform="github")
        with (
            patch.dict(os.environ, _env_without_gh(), clear=True),
            pytest.raises(AuthError, match="No VCS token found"),
        ):
            await resolve_token(config)

    @pytest.mark.asyncio
    async def test_sm_not_found(self, mock_boto3: object) -> None:
        """Secret not in SM should raise AuthError."""
        config = _make_config(
            platform="github",
            secret_name="prbot/nonexistent",
        )
        with (
            patch.dict(os.environ, _env_without_gh(), clear=True),
            pytest.raises(AuthError, match="not found"),
        ):
            await resolve_token(config)

    @pytest.mark.asyncio
    async def test_github_token_fallback_order(self) -> None:
        """GH_TOKEN takes priority over GITHUB_TOKEN."""
        config = _make_config(platform="github")
        env = {"GH_TOKEN": "first", "GITHUB_TOKEN": "second"}
        with patch.dict(os.environ, env):
            token = await resolve_token(config)
            assert token.reveal() == "first"
            assert token.source == "env:GH_TOKEN"


class TestValidateTokenScopes:
    """Test scope validation with mocked HTTP."""

    @respx.mock
    @pytest.mark.asyncio
    async def test_github_repo_scope_present(self) -> None:
        from prbot.auth.scope import validate_token_scopes

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(
                200, headers={"x-oauth-scopes": "repo, read:org"},
            )
        )
        token = TokenResult(value="ghp_test", source="test")
        await validate_token_scopes(token, "github")

    @respx.mock
    @pytest.mark.asyncio
    async def test_github_missing_scope(self) -> None:
        from prbot.auth.scope import validate_token_scopes

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(
                200, headers={"x-oauth-scopes": "read:org"},
            )
        )
        token = TokenResult(value="ghp_test", source="test")
        with pytest.raises(InsufficientScopesError, match="repo"):
            await validate_token_scopes(token, "github")

    @respx.mock
    @pytest.mark.asyncio
    async def test_github_timeout(self) -> None:
        from prbot.auth.scope import validate_token_scopes

        respx.get("https://api.github.com/user").mock(
            side_effect=httpx.ReadTimeout("timeout"),
        )
        token = TokenResult(value="ghp_test", source="test")
        with pytest.raises(AuthError, match="timed out"):
            await validate_token_scopes(token, "github")

    @respx.mock
    @pytest.mark.asyncio
    async def test_gitlab_api_scope(self) -> None:
        from prbot.auth.scope import validate_token_scopes

        url = "https://gitlab.com/api/v4/personal_access_tokens/self"
        respx.get(url).mock(
            return_value=httpx.Response(
                200, json={"scopes": ["api", "read_user"]},
            )
        )
        token = TokenResult(value="glpat-test", source="test")
        await validate_token_scopes(token, "gitlab")

    @respx.mock
    @pytest.mark.asyncio
    async def test_gitlab_missing_scope(self) -> None:
        from prbot.auth.scope import validate_token_scopes

        url = "https://gitlab.com/api/v4/personal_access_tokens/self"
        respx.get(url).mock(
            return_value=httpx.Response(
                200, json={"scopes": ["read_user"]},
            )
        )
        token = TokenResult(value="glpat-test", source="test")
        with pytest.raises(InsufficientScopesError, match="api"):
            await validate_token_scopes(token, "gitlab")

    @pytest.mark.asyncio
    async def test_ssrf_rejected(self) -> None:
        from prbot.auth.scope import validate_token_scopes
        from prbot.exceptions import ConfigError

        token = TokenResult(value="ghp_test", source="test")
        with pytest.raises(ConfigError, match="SSRF"):
            await validate_token_scopes(
                token, "github",
                api_base_url="https://169.254.169.254/",
            )


class TestTokenResult:
    """Test TokenResult exposure prevention."""

    def test_repr_redacted(self) -> None:
        t = TokenResult(value="ghp_secret123", source="env:GH_TOKEN")
        assert "ghp_secret123" not in repr(t)
        assert "<REDACTED>" in repr(t)

    def test_str_redacted(self) -> None:
        t = TokenResult(value="ghp_secret123", source="env:GH_TOKEN")
        assert "ghp_secret123" not in str(t)
        assert "<REDACTED>" in str(t)

    def test_redacted_property(self) -> None:
        t = TokenResult(value="ghp_secretvalue123", source="test")
        assert t.redacted == "ghp_..."

    def test_redacted_short_token(self) -> None:
        t = TokenResult(value="abcd", source="test")
        assert t.redacted == "****"

    def test_mask_in_ci_github(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        t = TokenResult(value="ghp_secret123", source="test")
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            t.mask_in_ci()
        captured = capsys.readouterr()
        assert "::add-mask::ghp_secret123" in captured.err
        assert captured.out == ""

    def test_mask_in_ci_not_github(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        t = TokenResult(value="ghp_secret123", source="test")
        with patch.dict(os.environ, {}, clear=True):
            t.mask_in_ci()
        captured = capsys.readouterr()
        assert captured.err == ""
        assert captured.out == ""

    def test_empty_token_raises(self) -> None:
        with pytest.raises(AuthError, match="empty"):
            TokenResult(value="", source="test")

    def test_bearer_header(self) -> None:
        t = TokenResult(value="my_token", source="test")
        assert t.as_bearer_header() == "Bearer my_token"

    def test_private_token(self) -> None:
        t = TokenResult(value="my_token", source="test")
        assert t.as_private_token() == "my_token"


class TestRedactTokensFromString:
    """Test token redaction patterns."""

    def test_ghp_classic_pat(self) -> None:
        token = "ghp_" + "A" * 36
        result = redact_tokens_from_string(f"token: {token}")
        assert "[REDACTED]" in result
        assert token not in result

    def test_github_fine_grained_pat(self) -> None:
        token = "github_pat_" + "a" * 82
        result = redact_tokens_from_string(f"Bearer {token}")
        assert "[REDACTED]" in result
        assert token not in result

    def test_ghs_app_token(self) -> None:
        token = "ghs_" + "A" * 36
        assert "[REDACTED]" in redact_tokens_from_string(token)

    def test_glpat_gitlab(self) -> None:
        token = "glpat-" + "a" * 20
        assert "[REDACTED]" in redact_tokens_from_string(token)

    def test_no_tokens_unchanged(self) -> None:
        text = "This has no tokens at all"
        assert redact_tokens_from_string(text) == text

    def test_multiple_tokens(self) -> None:
        ghp = "ghp_" + "a" * 36
        glpat = "glpat-" + "b" * 20
        text = f"first={ghp} second={glpat}"
        result = redact_tokens_from_string(text)
        assert result.count("[REDACTED]") == 2

    def test_token_in_url(self) -> None:
        token = "ghp_" + "x" * 36
        text = f"https://api.github.com?token={token}"
        result = redact_tokens_from_string(text)
        assert token not in result
        assert "[REDACTED]" in result


class TestLogRedactionProcessor:
    """A3: the processor that is actually installed by configure_logging."""

    def test_redacts_event_string(self) -> None:
        from prbot.observability.logging import _redact_processor

        token = "ghp_" + "a" * 36
        event_dict: dict[str, object] = {"event": f"Using token {token}"}
        result = _redact_processor(None, "info", event_dict)
        assert token not in str(result["event"])
        assert "[REDACTED]" in str(result["event"])

    def test_redacts_arbitrary_string_values(self) -> None:
        from prbot.observability.logging import _redact_processor

        token = "glpat-" + "b" * 20
        event_dict: dict[str, object] = {
            "event": "auth",
            "header": f"PRIVATE-TOKEN: {token}",
        }
        result = _redact_processor(None, "info", event_dict)
        assert token not in str(result["header"])

    def test_ignores_non_string_values(self) -> None:
        from prbot.observability.logging import _redact_processor

        event_dict: dict[str, object] = {"event": "test", "count": 42}
        result = _redact_processor(None, "info", event_dict)
        assert result["count"] == 42


class TestOidcValidation:
    """Test AWS credential detection."""

    def test_session_token_present(self) -> None:
        env = {
            "AWS_ACCESS_KEY_ID": "AKIAIOSFODNN7EXAMPLE",
            "AWS_SECRET_ACCESS_KEY": "secret",
            "AWS_SESSION_TOKEN": "session-token",
        }
        with patch.dict(os.environ, env, clear=True):
            assert validate_aws_session_credentials() is True

    def test_long_lived_warning(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        env = {
            "AWS_ACCESS_KEY_ID": "AKIAIOSFODNN7EXAMPLE",
            "AWS_SECRET_ACCESS_KEY": "secret",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            caplog.at_level(logging.WARNING),
        ):
            result = validate_aws_session_credentials()
            assert result is False
            assert "long-lived" in caplog.text

    def test_no_credentials(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert validate_aws_session_credentials() is False


class TestScopeValidationDegradesHonestly:
    """A9: scope validation must run, and must not break token types that
    cannot report scopes.

    A GitHub Actions GITHUB_TOKEN is an installation token and a
    fine-grained PAT is not an OAuth token; neither returns an
    X-OAuth-Scopes header. Treating an absent header as "no scopes granted"
    would fail every GitHub Actions run, which is the primary path.
    """

    @respx.mock
    @pytest.mark.asyncio
    async def test_github_absent_scope_header_is_not_a_failure(self) -> None:
        from prbot.auth.scope import validate_token_scopes

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, json={"login": "prbot[bot]"}),
        )
        token = TokenResult(value="ghs_installation", source="test")
        await validate_token_scopes(token, "github")

    @respx.mock
    @pytest.mark.asyncio
    async def test_github_empty_scope_header_is_not_a_failure(self) -> None:
        """A fine-grained PAT returns the header with an empty value."""
        from prbot.auth.scope import validate_token_scopes

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(200, headers={"x-oauth-scopes": ""}),
        )
        token = TokenResult(value="github_pat_x", source="test")
        await validate_token_scopes(token, "github")

    @respx.mock
    @pytest.mark.asyncio
    async def test_github_populated_header_is_still_enforced(self) -> None:
        from prbot.auth.scope import validate_token_scopes

        respx.get("https://api.github.com/user").mock(
            return_value=httpx.Response(
                200, headers={"x-oauth-scopes": "gist, read:org"},
            ),
        )
        token = TokenResult(value="ghp_classic", source="test")
        with pytest.raises(InsufficientScopesError, match="repo"):
            await validate_token_scopes(token, "github")

    @respx.mock
    @pytest.mark.asyncio
    async def test_gitlab_job_token_cannot_introspect(self) -> None:
        """CI_JOB_TOKEN gets 401 from the PAT endpoint; that is not a fault."""
        from prbot.auth.scope import validate_token_scopes

        url = "https://gitlab.com/api/v4/personal_access_tokens/self"
        respx.get(url).mock(return_value=httpx.Response(401))
        token = TokenResult(value="job-token", source="test")
        await validate_token_scopes(token, "gitlab")

    @respx.mock
    @pytest.mark.asyncio
    async def test_gitlab_missing_endpoint_is_not_a_failure(self) -> None:
        """Older self-hosted GitLab has no /personal_access_tokens/self."""
        from prbot.auth.scope import validate_token_scopes

        url = "https://gitlab.com/api/v4/personal_access_tokens/self"
        respx.get(url).mock(return_value=httpx.Response(404))
        token = TokenResult(value="glpat-test", source="test")
        await validate_token_scopes(token, "gitlab")

    @respx.mock
    @pytest.mark.asyncio
    async def test_gitlab_reported_scopes_are_still_enforced(self) -> None:
        from prbot.auth.scope import validate_token_scopes

        url = "https://gitlab.com/api/v4/personal_access_tokens/self"
        respx.get(url).mock(
            return_value=httpx.Response(200, json={"scopes": ["read_user"]}),
        )
        token = TokenResult(value="glpat-test", source="test")
        with pytest.raises(InsufficientScopesError, match="api"):
            await validate_token_scopes(token, "gitlab")
