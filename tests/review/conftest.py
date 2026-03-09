"""Review pipeline test fixtures (story-4-7, NG-16)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from prbot.review.models import AgentResult, Finding, TokenUsage

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "bedrock"


def _load_fixture(name: str) -> dict[str, Any]:
    """Load a Bedrock response fixture JSON file."""
    return json.loads((_FIXTURES_DIR / name).read_text())


@pytest.fixture
def general_response() -> dict[str, Any]:
    """Concrete general review Bedrock response."""
    return _load_fixture("general_review_response.json")


@pytest.fixture
def security_response() -> dict[str, Any]:
    """Concrete security review Bedrock response."""
    return _load_fixture("security_review_response.json")


@pytest.fixture
def mock_bedrock(general_response: dict[str, Any]):
    """Patch _invoke_bedrock to return a configurable response.

    Yields a MagicMock whose return_value can be changed per-test.
    Default returns the general review response.
    """
    mock = MagicMock(return_value=general_response)
    with patch("prbot.review.runner._invoke_bedrock", mock):
        yield mock


def make_finding(
    *,
    agent: str = "general",
    index: int = 1,
    check_id: str = "Q-ARCH-01",
    severity: str = "medium",
    confidence: int = 75,
    file_path: str = "src/example.py",
) -> Finding:
    """Factory for test Finding instances."""
    category = "security" if agent == "security" else "general"
    return Finding(
        id=f"{agent}-{index}",
        category=category,
        check_id=check_id,
        title="Test finding",
        description="Test description",
        file_path=file_path,
        line_start=1,
        line_end=10,
        severity=severity,
        confidence=confidence,
        suggestion="Fix it",
    )


def make_agent_result(
    *,
    agent: str = "general",
    findings: list[Finding] | None = None,
    input_tokens: int = 1000,
    output_tokens: int = 200,
) -> AgentResult:
    """Factory for test AgentResult instances."""
    return AgentResult(
        agent=agent,
        findings=findings or [],
        token_usage=TokenUsage(input_tokens, output_tokens, 0.0),
        latency_ms=500,
        model_id="us.anthropic.claude-sonnet-4-20250514",
    )
