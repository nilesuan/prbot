"""Tests for review runner (story-4-7)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from prbot.exceptions import BedrockError
from prbot.review.budget import TimeoutBudget
from prbot.review.models import AgentError, AgentResult
from prbot.review.runner import (
    _classify_error,
    _extract_token_usage,
    _is_retryable,
    _parse_findings,
    run_review,
)
from prbot.vcs.models import FileDiff, PRDiff, PRMetadata

_HEAD_SHA = "abcdef1234567890abcdef1234567890abcdef12"
_BASE_SHA = "1234567890abcdef1234567890abcdef12345678"


def _make_metadata() -> PRMetadata:
    return PRMetadata(
        title="Test PR",
        body="Test body",
        state="open",
        head_sha=_HEAD_SHA,
        base_sha=_BASE_SHA,
        head_ref="feature/test",
        base_ref="main",
        author="testuser",
        number=42,
    )


def _make_diff() -> PRDiff:
    return PRDiff(
        files=[
            FileDiff(
                path="src/example.py",
                status="modified",
                patch="@@ -1,3 +1,4 @@\n import os\n+import sys\n",
                additions=1,
                deletions=0,
            ),
        ],
        head_sha=_HEAD_SHA,
        base_sha=_BASE_SHA,
    )


def _make_bedrock_response(
    findings: list[dict[str, Any]],
    input_tokens: int = 1500,
    output_tokens: int = 200,
) -> dict[str, Any]:
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {"text": json.dumps({"findings": findings})},
                ],
            },
        },
        "usage": {
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
        },
    }


def _make_finding_dict(
    *,
    check_id: str = "Q-ARCH-01",
    severity: str = "medium",
    confidence: int = 75,
    file_path: str = "src/example.py",
    line_start: int = 10,
    line_end: int = 25,
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "title": "Test finding",
        "description": "Test description",
        "file_path": file_path,
        "line_start": line_start,
        "line_end": line_end,
        "severity": severity,
        "confidence": confidence,
        "suggestion": "Fix it",
    }


class TestRunReview:
    """Tests for run_review() concurrent execution."""

    @pytest.mark.asyncio
    async def test_both_agents_succeed(self, mock_bedrock: MagicMock) -> None:
        agents = [
            {"name": "general", "model_id": "us.anthropic.claude-sonnet-4-20250514"},
            {"name": "security", "model_id": "us.anthropic.claude-opus-4-0-20250514"},
        ]
        budget = TimeoutBudget(300.0)
        outcomes = await run_review(
            _make_diff(), _make_metadata(), agents, budget, "us-east-1",
        )
        assert len(outcomes) == 2
        assert all(isinstance(o, AgentResult) for o in outcomes)

    @pytest.mark.asyncio
    async def test_partial_failure(self) -> None:
        """One agent succeeds, one raises — partial failure handled."""
        response = _make_bedrock_response([_make_finding_dict()])

        call_count = 0

        def side_effect(**kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if kwargs.get("model_id", "").endswith("opus-4-0-20250514"):
                raise BedrockError("Throttling: rate exceeded")
            return response

        with patch("prbot.review.runner._invoke_bedrock", side_effect=side_effect):
            agents = [
                {
                    "name": "general",
                    "model_id": "us.anthropic.claude-sonnet-4-20250514",
                },
                {
                    "name": "security",
                    "model_id": "us.anthropic.claude-opus-4-0-20250514",
                },
            ]
            budget = TimeoutBudget(300.0)
            outcomes = await run_review(
                _make_diff(), _make_metadata(), agents, budget, "us-east-1",
            )
        assert len(outcomes) == 2
        result_types = {type(o) for o in outcomes}
        assert AgentResult in result_types
        assert AgentError in result_types

    @pytest.mark.asyncio
    async def test_both_agents_produce_results(
        self, mock_bedrock: MagicMock,
    ) -> None:
        """Both agents produce AgentResult outcomes."""
        agents = [
            {"name": "general", "model_id": "us.anthropic.claude-sonnet-4-20250514"},
            {"name": "security", "model_id": "us.anthropic.claude-opus-4-0-20250514"},
        ]
        budget = TimeoutBudget(300.0)
        outcomes = await run_review(
            _make_diff(), _make_metadata(), agents, budget, "us-east-1",
        )
        assert len(outcomes) == 2
        agents_returned = {o.agent for o in outcomes}
        assert agents_returned == {"general", "security"}

    @pytest.mark.asyncio
    async def test_unhandled_exception_captured(self) -> None:
        """Unhandled exceptions become AgentError in gather."""
        with patch(
            "prbot.review.runner._invoke_bedrock",
            side_effect=RuntimeError("boom"),
        ):
            agents = [
                {
                    "name": "general",
                    "model_id": "us.anthropic.claude-sonnet-4-20250514",
                },
            ]
            budget = TimeoutBudget(300.0)
            outcomes = await run_review(
                _make_diff(), _make_metadata(), agents, budget, "us-east-1",
            )
        assert len(outcomes) == 1
        assert isinstance(outcomes[0], AgentError)
        assert outcomes[0].error_type == "unhandled"


class TestRunSingleAgent:
    """Tests for _run_single_agent retry, timeout, and parsing."""

    @pytest.mark.asyncio
    async def test_successful_invocation(
        self, mock_bedrock: MagicMock,
    ) -> None:
        agents = [
            {"name": "general", "model_id": "us.anthropic.claude-sonnet-4-20250514"},
        ]
        budget = TimeoutBudget(300.0)
        outcomes = await run_review(
            _make_diff(), _make_metadata(), agents, budget, "us-east-1",
        )
        result = outcomes[0]
        assert isinstance(result, AgentResult)
        assert result.agent == "general"
        assert result.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_retry_on_throttle(self) -> None:
        """Retries on throttling errors with backoff."""
        response = _make_bedrock_response([_make_finding_dict()])
        call_count = 0

        def side_effect(**kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise BedrockError("ThrottlingException: Rate exceeded")
            return response

        with patch(
            "prbot.review.runner._invoke_bedrock",
            side_effect=side_effect,
        ), patch("prbot.review.runner.asyncio.sleep"):
            agents = [
                {
                    "name": "general",
                    "model_id": "us.anthropic.claude-sonnet-4-20250514",
                },
            ]
            budget = TimeoutBudget(300.0)
            outcomes = await run_review(
                _make_diff(), _make_metadata(), agents, budget, "us-east-1",
            )
        assert isinstance(outcomes[0], AgentResult)
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_no_retry_on_validation_error(self) -> None:
        """Validation errors are not retried."""
        call_count = 0

        def side_effect(**kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            raise BedrockError("ValidationException: invalid model")

        with patch(
            "prbot.review.runner._invoke_bedrock",
            side_effect=side_effect,
        ):
            agents = [
                {
                    "name": "general",
                    "model_id": "us.anthropic.claude-sonnet-4-20250514",
                },
            ]
            budget = TimeoutBudget(300.0)
            outcomes = await run_review(
                _make_diff(), _make_metadata(), agents, budget, "us-east-1",
            )
        assert isinstance(outcomes[0], AgentError)
        assert outcomes[0].error_type == "validation_error"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self) -> None:
        """Timeout produces AgentError with timeout type."""
        import time as _time

        def slow_invoke(**kwargs: Any) -> dict[str, Any]:
            _time.sleep(10)
            return {}

        with patch(
            "prbot.review.runner._invoke_bedrock",
            side_effect=slow_invoke,
        ):
            agents = [
                {
                    "name": "general",
                    "model_id": "us.anthropic.claude-sonnet-4-20250514",
                },
            ]
            # Very short budget to trigger timeout
            budget = TimeoutBudget(0.05)
            outcomes = await run_review(
                _make_diff(), _make_metadata(), agents, budget, "us-east-1",
            )
        assert isinstance(outcomes[0], AgentError)
        assert outcomes[0].error_type == "timeout"

    @pytest.mark.asyncio
    async def test_token_usage_extracted(
        self, mock_bedrock: MagicMock,
    ) -> None:
        agents = [
            {"name": "general", "model_id": "us.anthropic.claude-sonnet-4-20250514"},
        ]
        budget = TimeoutBudget(300.0)
        outcomes = await run_review(
            _make_diff(), _make_metadata(), agents, budget, "us-east-1",
        )
        result = outcomes[0]
        assert isinstance(result, AgentResult)
        assert result.token_usage.input_tokens == 1500
        assert result.token_usage.output_tokens == 200

    @pytest.mark.asyncio
    async def test_findings_parsed(self, mock_bedrock: MagicMock) -> None:
        response = _make_bedrock_response([_make_finding_dict()])
        mock_bedrock.return_value = response
        agents = [
            {"name": "general", "model_id": "us.anthropic.claude-sonnet-4-20250514"},
        ]
        budget = TimeoutBudget(300.0)
        outcomes = await run_review(
            _make_diff(), _make_metadata(), agents, budget, "us-east-1",
        )
        result = outcomes[0]
        assert isinstance(result, AgentResult)
        assert len(result.findings) == 1
        assert result.findings[0].check_id == "Q-ARCH-01"

    @pytest.mark.asyncio
    async def test_model_id_recorded(self, mock_bedrock: MagicMock) -> None:
        agents = [
            {"name": "general", "model_id": "us.anthropic.claude-sonnet-4-20250514"},
        ]
        budget = TimeoutBudget(300.0)
        outcomes = await run_review(
            _make_diff(), _make_metadata(), agents, budget, "us-east-1",
        )
        result = outcomes[0]
        assert isinstance(result, AgentResult)
        assert result.model_id == "us.anthropic.claude-sonnet-4-20250514"


class TestParseFindings:
    """Tests for _parse_findings validation logic."""

    def test_valid_general_finding(self) -> None:
        response = _make_bedrock_response([_make_finding_dict()])
        findings = _parse_findings(response, "general")
        assert len(findings) == 1
        assert findings[0].category == "general"
        assert findings[0].id == "general-1"

    def test_rejects_path_traversal(self) -> None:
        finding = _make_finding_dict(file_path="../../../etc/passwd")
        response = _make_bedrock_response([finding])
        findings = _parse_findings(response, "general")
        assert len(findings) == 0

    def test_rejects_absolute_path(self) -> None:
        finding = _make_finding_dict(file_path="/etc/passwd")
        response = _make_bedrock_response([finding])
        findings = _parse_findings(response, "general")
        assert len(findings) == 0

    def test_rejects_wrong_check_id_prefix(self) -> None:
        finding = _make_finding_dict(check_id="S-CRED-01")
        response = _make_bedrock_response([finding])
        findings = _parse_findings(response, "general")
        assert len(findings) == 0

    def test_accepts_security_prefix_for_security_agent(self) -> None:
        finding = _make_finding_dict(check_id="S-INPUT-01")
        response = _make_bedrock_response([finding])
        findings = _parse_findings(response, "security")
        assert len(findings) == 1
        assert findings[0].category == "security"

    def test_rejects_invalid_line_range(self) -> None:
        finding = _make_finding_dict(line_start=25, line_end=10)
        response = _make_bedrock_response([finding])
        findings = _parse_findings(response, "general")
        assert len(findings) == 0

    def test_clamps_confidence(self) -> None:
        finding = _make_finding_dict(confidence=150)
        response = _make_bedrock_response([finding])
        findings = _parse_findings(response, "general")
        assert len(findings) == 1
        assert findings[0].confidence == 100

    def test_normalizes_unknown_severity(self) -> None:
        finding = _make_finding_dict(severity="extreme")
        response = _make_bedrock_response([finding])
        findings = _parse_findings(response, "general")
        assert len(findings) == 1
        assert findings[0].severity == "info"

    def test_non_json_response(self) -> None:
        response = {
            "output": {
                "message": {
                    "content": [{"text": "This is not JSON"}],
                },
            },
        }
        findings = _parse_findings(response, "general")
        assert len(findings) == 0

    def test_empty_content(self) -> None:
        response = {"output": {"message": {"content": []}}}
        findings = _parse_findings(response, "general")
        assert len(findings) == 0


class TestExtractTokenUsage:
    """Tests for _extract_token_usage."""

    def test_valid_usage(self) -> None:
        response = {
            "usage": {"inputTokens": 1000, "outputTokens": 500},
        }
        usage = _extract_token_usage(response)
        assert usage.input_tokens == 1000
        assert usage.output_tokens == 500

    def test_missing_usage(self) -> None:
        usage = _extract_token_usage({})
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0

    def test_non_int_tokens(self) -> None:
        response = {
            "usage": {"inputTokens": "not_int", "outputTokens": None},
        }
        usage = _extract_token_usage(response)
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0


class TestClassifyError:
    """Tests for _classify_error and _is_retryable."""

    def test_throttling(self) -> None:
        assert _classify_error(Exception("ThrottlingException")) == "throttled"

    def test_too_many_requests(self) -> None:
        assert _classify_error(Exception("Too Many Requests")) == "throttled"

    def test_validation(self) -> None:
        assert _classify_error(
            Exception("ValidationException"),
        ) == "validation_error"

    def test_model_not_found(self) -> None:
        assert _classify_error(
            Exception("Model xyz not found"),
        ) == "model_not_found"

    def test_access_denied(self) -> None:
        assert _classify_error(
            Exception("Access denied for operation"),
        ) == "access_denied"

    def test_unknown(self) -> None:
        assert _classify_error(Exception("something else")) == "unknown"

    def test_retryable_types(self) -> None:
        assert _is_retryable("throttled") is True
        assert _is_retryable("service_unavailable") is True
        assert _is_retryable("internal_error") is True
        assert _is_retryable("validation_error") is False
        assert _is_retryable("unknown") is False
