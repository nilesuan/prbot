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
    # The agent that reported it. Not an enum: the roster is configurable
    # and a repository may add its own agents (C5).
    category: str
    check_id: str
    title: str
    description: str
    file_path: str
    line_start: int
    line_end: int
    severity: Literal["critical", "high", "medium", "low", "info"]
    confidence: int  # 0-100
    suggestion: str = ""
    # The concrete trigger and the wrong outcome. Rendered as the Impact
    # line of every comment, so it is asked of every agent and not only of
    # the adversarial one: a finding whose consequence cannot be stated is
    # one the reader cannot weigh. Still defaulted, because the renderer
    # drops the line rather than printing an empty label when a model
    # ignores the instruction.
    failure_scenario: str = ""
    # Which agents reported this defect. Two agents arriving at the same
    # finding independently is evidence, and it is the only way to tell a
    # merged finding from a single-agent one after deduplication (B1).
    reported_by: tuple[str, ...] = ()
    # What the verification pass concluded: "confirmed", "refuted",
    # "uncertain", or empty when it did not run or gave no verdict.
    verification: str = ""
    # The agent's own confidence, kept when a verdict replaced it, so the
    # audit shows what a finding was before it moved (SEC-LOG-01).
    confidence_before_verification: int | None = None


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
    # What the agent was billed for before it failed: a tool loop can spend
    # several turns before one fails (SEC-LOG-01).
    token_usage: TokenUsage = field(
        default_factory=lambda: TokenUsage(0, 0, 0.0),
    )


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
                    "failure_scenario", "file_path",
                    "line_start", "line_end",
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
                    "failure_scenario": {
                        "type": "string",
                        "description": (
                            "Concrete trigger then wrong outcome, in that "
                            "order. Rendered as the Impact line of the "
                            "comment, so every finding needs one."
                        ),
                    },
                },
            },
        },
    },
}
