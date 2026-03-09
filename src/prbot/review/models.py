"""Review data models (story-4-1).

Immutable dataclasses for findings, token usage, and agent outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# Severity levels ordered by importance
SEVERITY_LEVELS: list[str] = ["critical", "high", "medium", "low", "info"]


@dataclass(frozen=True)
class TokenUsage:
    """Token consumption and cost for a single agent invocation."""

    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float


@dataclass(frozen=True)
class Finding:
    """A single review finding from an agent."""

    id: str
    category: Literal["general", "security"]
    check_id: str
    title: str
    description: str
    file_path: str
    line_start: int
    line_end: int
    severity: Literal["critical", "high", "medium", "low", "info"]
    confidence: int  # 0-100
    suggestion: str = ""


@dataclass(frozen=True)
class AgentResult:
    """Successful result from a review agent."""

    agent: str
    findings: list[Finding] = field(default_factory=list)
    token_usage: TokenUsage = field(
        default_factory=lambda: TokenUsage(0, 0, 0.0),
    )
    latency_ms: int = 0
    model_id: str = ""


@dataclass(frozen=True)
class AgentError:
    """Error result from a review agent."""

    agent: str
    error_type: str
    message: str
    retryable: bool = False


# Type alias for agent outcomes — supports partial failure
AgentOutcome = AgentResult | AgentError


# JSON schema for Bedrock structured output
FINDING_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["findings"],
    "additionalProperties": False,
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "check_id", "title", "description",
                    "file_path", "line_start", "line_end",
                    "severity", "confidence", "suggestion",
                ],
                "additionalProperties": False,
                "properties": {
                    "check_id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "file_path": {"type": "string"},
                    "line_start": {"type": "integer", "minimum": 1},
                    "line_end": {"type": "integer", "minimum": 1},
                    "severity": {
                        "type": "string",
                        "enum": SEVERITY_LEVELS,
                    },
                    "confidence": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100,
                    },
                    "suggestion": {"type": "string"},
                },
            },
        },
    },
}
