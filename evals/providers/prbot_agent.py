"""Promptfoo provider that runs a real prbot review agent.

This deliberately calls prbot's own run_review rather than reconstructing the
prompt. The system prompt, the datamarking, the forced tool carrying
FINDING_JSON_SCHEMA, the check-prefix filtering and the finding parsing are
all the shipped code paths, so an eval result describes what production does.
A provider that rebuilt the prompt would drift from it silently, and the
first thing to drift would be the injection defence this suite exists to
measure.

The output handed back to promptfoo is a JSON document rather than prose, so
assertions can be written against structure (how many findings, which checks)
instead of against wording.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "evals"))

from fixtures import FIXTURES  # noqa: E402

from prbot.review.budget import TimeoutBudget  # noqa: E402
from prbot.review.models import AgentError, AgentResult  # noqa: E402
from prbot.review.runner import run_review  # noqa: E402

_PREFIXES = {"general": "Q-", "security": "S-", "adversarial": "X-"}


def call_api(
    prompt: str,
    options: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one agent over one fixture and return its findings as JSON."""
    cfg = (options or {}).get("config", {}) or {}
    variables = (context or {}).get("vars", {}) or {}

    fixture_name = variables.get("fixture") or prompt.strip()
    fixture = FIXTURES.get(fixture_name)
    if fixture is None:
        return {"error": f"unknown fixture: {fixture_name!r}"}

    agent = cfg.get("agent", "general")
    model_id = cfg.get("model_id", "au.anthropic.claude-sonnet-4-6")
    region = cfg.get("region", "ap-southeast-2")
    datamark = bool(cfg.get("datamark_diff", True))
    context_lines = int(cfg.get("context_lines", 0))

    agents = [
        {
            "name": agent,
            "model_id": model_id,
            "check_prefix": _PREFIXES.get(agent, "Q-"),
        },
    ]

    try:
        outcomes = asyncio.run(
            run_review(
                fixture.diff,
                fixture.metadata,
                agents,
                TimeoutBudget(240.0),
                region,
                max_output_tokens=4096,
                datamark_diff=datamark,
                file_contents=None,
                context_lines=context_lines,
            ),
        )
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}

    outcome = outcomes[0]
    if isinstance(outcome, AgentError):
        return {
            "error": f"{outcome.error_type}: {outcome.message}",
        }

    assert isinstance(outcome, AgentResult)
    payload = {
        "fixture": fixture.name,
        "agent": agent,
        "datamark_diff": datamark,
        "context_lines": context_lines,
        "has_defect": fixture.has_defect,
        "finding_count": len(outcome.findings),
        "check_ids": [f.check_id for f in outcome.findings],
        "severities": [f.severity for f in outcome.findings],
        "findings": [
            {
                "check_id": f.check_id,
                "title": f.title,
                "description": f.description,
                "suggestion": f.suggestion,
                "failure_scenario": f.failure_scenario,
                "file_path": f.file_path,
                "line_start": f.line_start,
                "line_end": f.line_end,
                "severity": f.severity,
                "confidence": f.confidence,
            }
            for f in outcome.findings
        ],
    }

    return {
        "output": json.dumps(payload, indent=2),
        "tokenUsage": {
            "prompt": outcome.token_usage.input_tokens,
            "completion": outcome.token_usage.output_tokens,
            "total": (
                outcome.token_usage.input_tokens
                + outcome.token_usage.output_tokens
            ),
        },
        "cost": outcome.token_usage.estimated_cost_usd,
    }
