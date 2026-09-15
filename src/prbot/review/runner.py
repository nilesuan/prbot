"""2-Agent async review runner (story-4-4).

Runs general and security review agents concurrently via Bedrock Converse API.
Handles partial failures, retries throttling, and validates findings.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from prbot.exceptions import BedrockError
from prbot.review.budget import TimeoutBudget, get_model_pricing
from prbot.review.models import (
    FINDING_JSON_SCHEMA,
    AgentError,
    AgentOutcome,
    AgentResult,
    Finding,
    TokenUsage,
)
from prbot.review.prompts import build_system_prompt, build_user_prompt
from prbot.vcs.models import PRDiff, PRMetadata

logger = logging.getLogger(__name__)

# Default cap on a single agent response. Explicit so that output length
# is a decision rather than a Bedrock default the cost estimate cannot see.
_DEFAULT_MAX_OUTPUT_TOKENS = 8192

# Retry config for throttled requests
_MAX_RETRIES = 3
_RETRY_BASE_SECONDS = 1.0

# Retryable Bedrock error types
_RETRYABLE_ERRORS = frozenset({
    "throttled",
    "service_unavailable",
    "internal_error",
})


async def run_review(
    pr_diff: PRDiff,
    metadata: PRMetadata,
    agents: list[dict[str, str]],
    budget: TimeoutBudget,
    aws_region: str,
    max_output_tokens: int = _DEFAULT_MAX_OUTPUT_TOKENS,
) -> list[AgentOutcome]:
    """Run review agents concurrently (S1, S88).

    Args:
        pr_diff: PR diff data.
        metadata: PR metadata.
        agents: List of dicts with 'name' and 'model_id' keys.
        budget: Time budget for the review.
        aws_region: AWS region for Bedrock calls.

    Returns:
        List of AgentOutcome (AgentResult or AgentError) for each agent.
    """
    user_prompt = build_user_prompt(pr_diff, metadata)

    tasks = [
        _run_single_agent(
            agent_name=agent["name"],
            model_id=agent["model_id"],
            user_prompt=user_prompt,
            budget=budget,
            aws_region=aws_region,
            max_output_tokens=max_output_tokens,
        )
        for agent in agents
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    outcomes: list[AgentOutcome] = []
    for i, result in enumerate(results):
        if isinstance(result, BaseException):
            outcomes.append(AgentError(
                agent=agents[i]["name"],
                error_type="unhandled_exception",
                message=str(result),
                retryable=False,
            ))
        else:
            outcomes.append(result)

    return outcomes


async def _run_single_agent(
    agent_name: str,
    model_id: str,
    user_prompt: str,
    budget: TimeoutBudget,
    aws_region: str,
    max_output_tokens: int = _DEFAULT_MAX_OUTPUT_TOKENS,
) -> AgentOutcome:
    """Run a single review agent with retry and timeout (S20, S48).

    Retries throttling errors up to 3 times with exponential backoff.
    """
    system_prompt = build_system_prompt(agent_name)
    start_time = time.monotonic()

    for attempt in range(_MAX_RETRIES + 1):
        try:
            timeout = budget.allocate(120.0)

            response = await asyncio.wait_for(
                asyncio.to_thread(
                    _invoke_bedrock,
                    model_id=model_id,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    aws_region=aws_region,
                    max_output_tokens=max_output_tokens,
                ),
                timeout=timeout,
            )

            token_usage = _extract_token_usage(response, model_id)
            findings = _parse_findings(response, agent_name)
            latency_ms = int((time.monotonic() - start_time) * 1000)

            return AgentResult(
                agent=agent_name,
                findings=findings,
                token_usage=token_usage,
                latency_ms=latency_ms,
                model_id=model_id,
            )

        except ValueError as e:
            logger.error(str(e))
            return AgentError(
                agent=agent_name,
                error_type="invalid_response",
                message=str(e),
                retryable=False,
            )

        except TimeoutError:
            return AgentError(
                agent=agent_name,
                error_type="timeout",
                message=(
                    f"Agent {agent_name} timed out after "
                    f"{time.monotonic() - start_time:.1f}s"
                ),
                retryable=False,
            )

        except BedrockError as e:
            error_type = _classify_error(e)
            if _is_retryable(error_type) and attempt < _MAX_RETRIES:
                wait = _RETRY_BASE_SECONDS * (2 ** attempt)
                logger.warning(
                    "Agent %s got %s (attempt %d/%d), retrying in %.1fs",
                    agent_name, error_type, attempt + 1,
                    _MAX_RETRIES, wait,
                )
                await asyncio.sleep(wait)
                continue

            return AgentError(
                agent=agent_name,
                error_type=error_type,
                message=str(e),
                retryable=_is_retryable(error_type),
            )

        except Exception as e:
            return AgentError(
                agent=agent_name,
                error_type="unhandled",
                message=f"{type(e).__name__}: {e}",
                retryable=False,
            )

    # Should not reach here, but just in case
    return AgentError(
        agent=agent_name,
        error_type="max_retries_exceeded",
        message=f"Agent {agent_name} failed after {_MAX_RETRIES} retries",
        retryable=False,
    )


FINDINGS_TOOL_NAME = "report_findings"


def _findings_tool_config() -> dict[str, Any]:
    """Force the model to answer through the findings schema (B5).

    FINDING_JSON_SCHEMA was written and never sent. Without it the output
    shape was only a request in the prompt, so the parser needed three
    fallback strategies and a hard failure when all three missed.
    """
    return {
        "tools": [
            {
                "toolSpec": {
                    "name": FINDINGS_TOOL_NAME,
                    "description": (
                        "Report every review finding. Call this exactly "
                        "once, with an empty array if there is nothing "
                        "to report."
                    ),
                    "inputSchema": {"json": FINDING_JSON_SCHEMA},
                },
            },
        ],
        "toolChoice": {"tool": {"name": FINDINGS_TOOL_NAME}},
    }


def _invoke_bedrock(
    model_id: str,
    system_prompt: str,
    user_prompt: str,
    aws_region: str,
    max_output_tokens: int = _DEFAULT_MAX_OUTPUT_TOKENS,
) -> dict[str, Any]:
    """Invoke Bedrock Converse API synchronously (S16, B5).

    Called via asyncio.to_thread to avoid blocking the event loop.

    maxTokens and temperature are explicit. Left unset they are whatever
    Bedrock defaults to for the model, which is neither reproducible nor
    something the cost estimate can rely on.
    """
    import boto3
    from botocore.exceptions import ClientError

    client = boto3.client("bedrock-runtime", region_name=aws_region)

    try:
        response = client.converse(
            modelId=model_id,
            messages=[
                {
                    "role": "user",
                    "content": [{"text": user_prompt}],
                },
            ],
            system=[{"text": system_prompt}],
            toolConfig=_findings_tool_config(),
            inferenceConfig={
                "maxTokens": max_output_tokens,
                "temperature": 0.0,
            },
        )
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        raise BedrockError(
            f"Bedrock API error ({code}): {e}"
        ) from e

    return response


def _extract_token_usage(
    response: dict[str, Any],
    model_id: str,
) -> TokenUsage:
    """Extract token usage and cost from a Bedrock response (G4-13, A8).

    Returns TokenUsage(0, 0, 0.0) with a warning if data is missing.

    The cost was previously left at 0.0 with a note saying it would be
    calculated later, and nothing ever calculated it. The audit record
    therefore reported real token counts against zero spend, and
    budget_limit_usd was only ever compared with a pre-flight character
    count heuristic, never with what the run actually cost.
    """
    usage = response.get("usage")
    if not isinstance(usage, dict):
        logger.warning("Bedrock response missing 'usage' field")
        return TokenUsage(0, 0, 0.0)

    input_tokens = usage.get("inputTokens", 0)
    output_tokens = usage.get("outputTokens", 0)

    if not isinstance(input_tokens, int):
        input_tokens = 0
    if not isinstance(output_tokens, int):
        output_tokens = 0

    pricing = get_model_pricing(model_id)
    cost = (
        (input_tokens / 1_000_000) * pricing["input"]
        + (output_tokens / 1_000_000) * pricing["output"]
    )

    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_usd=cost,
    )


def _parse_findings(
    response: dict[str, Any],
    agent_name: str,
) -> list[Finding]:
    """Parse findings from Bedrock response with validation (G-08, G-18).

    Drops findings with:
    - Path traversal in file_path (G-18)
    - Invalid line ranges
    - Unknown check_id prefixes (G-08)
    """
    output = response.get("output", {})
    message = output.get("message", {}) if isinstance(output, dict) else {}
    content_blocks = message.get("content", [])

    # B5: the forced tool returns the object already parsed and schema
    # checked. Prose that the model emits alongside it is ignored.
    data: dict[str, Any] | None = None
    for block in content_blocks:
        if not isinstance(block, dict):
            continue
        tool_use = block.get("toolUse")
        if isinstance(tool_use, dict) and isinstance(
            tool_use.get("input"), dict,
        ):
            data = tool_use["input"]
            break

    if data is None:
        # Fallback for a response that arrived as text anyway, for instance
        # from a model or endpoint that ignored toolChoice.
        text = ""
        for block in content_blocks:
            if isinstance(block, dict) and "text" in block:
                text = block["text"]
                break

        if not text:
            raise ValueError(
                f"Agent {agent_name} returned no findings and no text content"
            )

        data = _try_parse_json(text)
        if data is None:
            raise ValueError(
                f"Agent {agent_name} returned non-JSON response: {text[:500]}"
            )

    raw_findings = data.get("findings", [])
    if not isinstance(raw_findings, list):
        return []

    category = "security" if agent_name == "security" else "general"
    valid_prefixes = {"S-"} if category == "security" else {"Q-"}

    findings: list[Finding] = []
    for i, f in enumerate(raw_findings):
        if not isinstance(f, dict):
            continue

        file_path = f.get("file_path", "")

        # G-18: Reject path traversal
        if ".." in file_path or file_path.startswith("/"):
            logger.warning(
                "Dropping finding with suspicious file_path: %s",
                file_path,
            )
            continue

        # Validate line range
        line_start = f.get("line_start", 0)
        line_end = f.get("line_end", 0)
        if (
            not isinstance(line_start, int)
            or not isinstance(line_end, int)
            or line_start < 1
            or line_end < line_start
        ):
            logger.warning(
                "Dropping finding with invalid line range: %s-%s",
                line_start, line_end,
            )
            continue

        # G-08: Validate check_id prefix
        check_id = f.get("check_id", "")
        if not any(check_id.startswith(p) for p in valid_prefixes):
            logger.warning(
                "Dropping finding with unknown check_id: %s",
                check_id,
            )
            continue

        severity = f.get("severity", "info")
        if severity not in (
            "critical", "high", "medium", "low", "info",
        ):
            severity = "info"

        confidence = f.get("confidence", 0)
        if not isinstance(confidence, int):
            confidence = 0
        confidence = max(0, min(100, confidence))

        findings.append(Finding(
            id=f"{agent_name}-{i + 1}",
            category=category,
            check_id=check_id,
            title=f.get("title", ""),
            description=f.get("description", ""),
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
            severity=severity,
            confidence=confidence,
            suggestion=f.get("suggestion", ""),
        ))

    return findings


def _try_parse_json(text: str) -> dict[str, Any] | None:
    """Try to parse JSON from raw text or markdown-fenced code blocks.

    Claude models often wrap JSON in ```json ... ``` fences or add preamble.
    This extracts the JSON object from common response formats.
    """
    # Try raw parse first
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code fences: ```json ... ``` or ``` ... ```
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    # Try finding the first { ... } block (outermost braces)
    start = text.find("{")
    if start != -1:
        # Find matching closing brace by counting
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(text[start:i + 1])
                        if isinstance(data, dict):
                            return data
                    except json.JSONDecodeError:
                        pass
                    break

    return None


def _classify_error(error: Exception) -> str:
    """Classify a Bedrock error into a type string (G4-01)."""
    msg = str(error).lower()
    if "throttling" in msg or "too many requests" in msg:
        return "throttled"
    if "validation" in msg:
        return "validation_error"
    if "model" in msg and "not found" in msg:
        return "model_not_found"
    if "service" in msg and "unavailable" in msg:
        return "service_unavailable"
    if "internal" in msg:
        return "internal_error"
    if "access denied" in msg or "unauthorized" in msg:
        return "access_denied"
    return "unknown"


def _is_retryable(error_type: str) -> bool:
    """Determine if an error type is retryable."""
    return error_type in _RETRYABLE_ERRORS
