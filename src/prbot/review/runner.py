"""2-Agent async review runner (story-4-4).

Runs general and security review agents concurrently via Bedrock Converse API.
Handles partial failures, retries throttling, and validates findings.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from collections.abc import Callable
from typing import Any

from prbot.exceptions import BedrockError, TimeoutBudgetExhausted
from prbot.review.budget import (
    CACHE_READ_MULTIPLIER,
    CACHE_WRITE_MULTIPLIER,
    TimeoutBudget,
    get_model_pricing,
)
from prbot.review.models import (
    FINDING_JSON_SCHEMA,
    AgentError,
    AgentOutcome,
    AgentResult,
    Finding,
    TokenUsage,
)
from prbot.review.prompts import build_system_prompt, build_user_prompt
from prbot.review.tools import READ_FILE_TOOL_NAME, FileReader
from prbot.vcs.models import PRDiff, PRMetadata

logger = logging.getLogger(__name__)

# A check id is a short code from a check spec, such as IAC-REPLACE-01.
_CHECK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")

# Default cap on a single agent response. Explicit so that output length
# is a decision rather than a Bedrock default the cost estimate cannot see.
_DEFAULT_MAX_OUTPUT_TOKENS = 8192


# SEC-DESIGN-05: one turn could ask for any number of reads, and reads ran
# outside the review's time budget.
MAX_READS_PER_TURN = 5
READ_TIMEOUT_SECONDS = 30.0

# Retry config for throttled requests
_MAX_RETRIES = 3
_RETRY_BASE_SECONDS = 1.0


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff with jitter (D9).

    Without jitter every agent throttled by the same Bedrock quota retries
    on the same beat and throttles again together. The jitter is half the
    interval, which is enough to spread a small fan-out without making the
    worst case materially longer.
    """
    base = _RETRY_BASE_SECONDS * (2**attempt)
    return base + random.uniform(0.0, base / 2)

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
    datamark_diff: bool = True,
    file_contents: dict[str, str] | None = None,
    context_lines: int = 0,
    all_paths: list[str] | None = None,
    temperature: float | None = None,
    reader_factory: Callable[[], FileReader] | None = None,
    tool_turns: int = 0,
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
    user_prompt = build_user_prompt(
        pr_diff, metadata,
        datamark_diff=datamark_diff,
        file_contents=file_contents,
        context_lines=context_lines,
        all_paths=all_paths,
    )

    tasks = [
        _run_single_agent(
            agent_name=agent["name"],
            model_id=agent["model_id"],
            check_prefix=agent["check_prefix"],
            user_prompt=user_prompt,
            budget=budget,
            aws_region=aws_region,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            # Each agent its own reader, so one agent's reading cannot
            # spend another's budget.
            reader=(
                reader_factory()
                if reader_factory is not None and tool_turns > 0
                else None
            ),
            tool_turns=tool_turns,
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


def _fail(
    agent_name: str,
    error_type: str,
    message: str,
    *,
    retryable: bool = False,
    token_usage: TokenUsage | None = None,
) -> AgentError:
    """Record an agent failure and say why in the log.

    The audit record keeps only status="error:AgentError" and the verdict
    logs "Both agents failed" with no cause, so without this a failing run
    gives no way to tell a permission problem from a rejected parameter
    without reproducing it by hand.
    """
    logger.error("agent.failed name=%s type=%s: %s",
                 agent_name, error_type, message)
    return AgentError(
        agent=agent_name,
        error_type=error_type,
        message=message,
        retryable=retryable,
        token_usage=token_usage or TokenUsage(0, 0, 0.0),
    )


def _failure(
    agent_name: str,
    error: Exception,
    start_time: float,
    usage: TokenUsage | None = None,
) -> AgentError:
    """The AgentError for an exception raised while an agent ran."""
    if isinstance(error, ValueError):
        logger.error(str(error))
        return _fail(
            agent_name, "invalid_response", str(error), token_usage=usage,
        )
    if isinstance(error, (TimeoutError, TimeoutBudgetExhausted)):
        return _fail(
            agent_name, "timeout",
            f"Agent {agent_name} timed out after "
            f"{time.monotonic() - start_time:.1f}s",
            token_usage=usage,
        )
    if isinstance(error, BedrockError):
        error_type = _classify_error(error)
        return _fail(
            agent_name, error_type, str(error),
            retryable=_is_retryable(error_type), token_usage=usage,
        )
    return _fail(
        agent_name, "unhandled", f"{type(error).__name__}: {error}",
        token_usage=usage,
    )


async def _run_single_agent(
    agent_name: str,
    model_id: str,
    check_prefix: str,
    user_prompt: str,
    budget: TimeoutBudget,
    aws_region: str,
    max_output_tokens: int = _DEFAULT_MAX_OUTPUT_TOKENS,
    temperature: float | None = None,
    reader: FileReader | None = None,
    tool_turns: int = 0,
) -> AgentOutcome:
    """Run a single review agent with retry and timeout (S20, S48).

    Retries throttling errors up to 3 times with exponential backoff.
    """
    if reader is not None and tool_turns > 0:
        return await _run_agent_with_tools(
            agent_name=agent_name, model_id=model_id,
            check_prefix=check_prefix, user_prompt=user_prompt,
            budget=budget, aws_region=aws_region,
            max_output_tokens=max_output_tokens, temperature=temperature,
            reader=reader, tool_turns=tool_turns,
        )

    start_time = time.monotonic()
    try:
        response = await _converse_with_retry(
            agent_name,
            budget,
            model_id=model_id,
            system_prompt=build_system_prompt(agent_name),
            user_prompt=user_prompt,
            aws_region=aws_region,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
        )
        token_usage = _extract_token_usage(response, model_id)
        return AgentResult(
            agent=agent_name,
            findings=_parse_findings(response, agent_name, check_prefix),
            token_usage=token_usage,
            latency_ms=int((time.monotonic() - start_time) * 1000),
            model_id=model_id,
        )
    except Exception as e:
        return _failure(agent_name, e, start_time)


async def _run_agent_with_tools(
    *,
    agent_name: str,
    model_id: str,
    check_prefix: str,
    user_prompt: str,
    budget: TimeoutBudget,
    aws_region: str,
    max_output_tokens: int,
    temperature: float | None,
    reader: FileReader,
    tool_turns: int,
) -> AgentOutcome:
    """Review with read_file available for up to tool_turns turns.

    Every turn re-sends the whole conversation, so the system prompt and the
    diff are marked as a cache prefix: the first call writes them and each
    later turn reads them at a fraction of the input price. The final turn
    forces report_findings, so a model that keeps reading still reports.
    """
    system_prompt = build_system_prompt(agent_name, tools_enabled=True)
    start_time = time.monotonic()
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": [
            {"text": user_prompt}, {"cachePoint": {"type": "default"}},
        ]},
    ]
    usage = TokenUsage(0, 0, 0.0)

    try:
        for turn in range(tool_turns + 1):
            response = await _converse_with_retry(
                agent_name,
                budget,
                model_id=model_id,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                aws_region=aws_region,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
                messages=messages,
                tool_config=_review_tool_config(
                    read_allowed=turn < tool_turns,
                ),
                cache=True,
            )
            usage = _add_usage(usage, _extract_token_usage(response, model_id))
            blocks = _content_blocks(response)
            uses = [b["toolUse"] for b in blocks if isinstance(
                b.get("toolUse"), dict,
            )]
            report = next(
                (u for u in uses if u.get("name") == FINDINGS_TOOL_NAME),
                None,
            )
            if report is not None or not uses:
                logger.info(
                    "agent.tools name=%s turns=%d lines_read=%d",
                    agent_name, turn + 1, reader.lines_read,
                )
                return AgentResult(
                    agent=agent_name,
                    findings=_reported_findings(
                        report, response, agent_name, check_prefix,
                    ),
                    token_usage=usage,
                    latency_ms=int((time.monotonic() - start_time) * 1000),
                    model_id=model_id,
                )

            messages.append({"role": "assistant", "content": blocks})
            messages.append({
                "role": "user",
                "content": await _answer_tool_uses(uses, reader, budget),
            })
    except Exception as e:
        return _failure(agent_name, e, start_time, usage)

    # The final turn forces report_findings, so this is reached only when
    # the model ignores toolChoice.
    return _fail(
        agent_name, "invalid_response",
        f"Agent {agent_name} did not report after {tool_turns + 1} turns",
        token_usage=usage,
    )


def _reported_findings(
    report: dict[str, Any] | None,
    response: dict[str, Any],
    agent_name: str,
    check_prefix: str,
) -> list[Finding]:
    """The findings a turn reported.

    A report_findings call, or an answer with no tool call at all, which the
    text fallback in _parse_findings still understands.
    """
    parsed_from = (
        {"output": {"message": {"content": [{"toolUse": report}]}}}
        if report is not None else response
    )
    return _parse_findings(parsed_from, agent_name, check_prefix)


def _content_blocks(response: dict[str, Any]) -> list[dict[str, Any]]:
    """The content blocks of a Converse response's message."""
    message = (response.get("output") or {}).get("message") or {}
    return [b for b in message.get("content") or [] if isinstance(b, dict)]


async def _answer_tool_uses(
    uses: list[dict[str, Any]],
    reader: FileReader,
    budget: TimeoutBudget,
) -> list[dict[str, Any]]:
    """A toolResult for every toolUse, which the next call requires.

    SEC-DESIGN-05: at most MAX_READS_PER_TURN are carried out, each inside
    the review's time budget. The rest are answered with an error the model
    can act on.
    """
    results = []
    for i, use in enumerate(uses):
        if i >= MAX_READS_PER_TURN:
            results.append(_tool_error(
                use,
                f"Only {MAX_READS_PER_TURN} reads are answered per turn; "
                f"ask for this one again next turn.",
            ))
            continue
        try:
            results.append(await asyncio.wait_for(
                _answer_tool_use(use, reader),
                timeout=budget.allocate(READ_TIMEOUT_SECONDS),
            ))
        except (TimeoutError, TimeoutBudgetExhausted):
            results.append(_tool_error(
                use, "The read ran out of time; report with what you have.",
            ))
    return results


async def _answer_tool_use(
    use: dict[str, Any],
    reader: FileReader,
) -> dict[str, Any]:
    """A toolResult block answering one toolUse request."""
    if use.get("name") != READ_FILE_TOOL_NAME:
        return _tool_error(use, f"Unknown tool {use.get('name')!r}.")
    args = use.get("input") if isinstance(use.get("input"), dict) else {}

    def _int(value: Any) -> int | None:
        return value if isinstance(value, int) else None

    text = await reader.read(
        str(args.get("path", "")),
        _int(args.get("start_line")),
        _int(args.get("end_line")),
    )
    return {"toolResult": {
        "toolUseId": use.get("toolUseId", ""), "content": [{"text": text}],
    }}


def _tool_error(use: dict[str, Any], text: str) -> dict[str, Any]:
    """An error toolResult for one toolUse request."""
    return {"toolResult": {
        "toolUseId": use.get("toolUseId", ""),
        "content": [{"text": text}],
        "status": "error",
    }}


async def _converse_with_retry(
    agent_name: str,
    budget: TimeoutBudget,
    **kwargs: Any,
) -> dict[str, Any]:
    """One Converse call with the same timeout and retry as a single review."""
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_invoke_bedrock, **kwargs),
                timeout=budget.allocate(120.0),
            )
        except BedrockError as e:
            error_type = _classify_error(e)
            if _is_retryable(error_type) and attempt < _MAX_RETRIES:
                wait = _backoff_seconds(attempt)
                logger.warning(
                    "Agent %s got %s (attempt %d/%d), retrying in %.1fs",
                    agent_name, error_type, attempt + 1, _MAX_RETRIES, wait,
                )
                await asyncio.sleep(wait)
                continue
            raise
    raise BedrockError(f"Agent {agent_name} failed after {_MAX_RETRIES} retries")


def _add_usage(a: TokenUsage, b: TokenUsage) -> TokenUsage:
    return TokenUsage(
        input_tokens=a.input_tokens + b.input_tokens,
        output_tokens=a.output_tokens + b.output_tokens,
        estimated_cost_usd=a.estimated_cost_usd + b.estimated_cost_usd,
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


def _review_tool_config(*, read_allowed: bool) -> dict[str, Any]:
    """Both tools, with a free choice while reading is still allowed."""
    from prbot.review.tools import read_file_tool_spec

    config = _findings_tool_config()
    config["tools"] = [*config["tools"], read_file_tool_spec()]
    if read_allowed:
        config["toolChoice"] = {"any": {}}
    return config


def _rejects_sampling_params(error: Any) -> bool:
    """Does this ValidationException name a sampling parameter?

    Matched on the parameter name rather than a model list, which would go
    stale on the next release. Bedrock reports these as, for example,
    "`temperature` is deprecated for this model."
    """
    err = getattr(error, "response", {}).get("Error", {})
    if err.get("Code") != "ValidationException":
        return False
    message = err.get("Message", "").lower()
    return any(p in message for p in ("temperature", "top_p", "topp"))


def _invoke_bedrock(
    model_id: str,
    system_prompt: str,
    user_prompt: str,
    aws_region: str,
    max_output_tokens: int = _DEFAULT_MAX_OUTPUT_TOKENS,
    temperature: float | None = None,
    messages: list[dict[str, Any]] | None = None,
    tool_config: dict[str, Any] | None = None,
    cache: bool = False,
) -> dict[str, Any]:
    """Invoke Bedrock Converse API synchronously (S16, B5).

    Called via asyncio.to_thread to avoid blocking the event loop.

    maxTokens is always explicit: left unset it is whatever Bedrock defaults
    to for the model, which the cost estimate cannot rely on. temperature is
    sent only when configured. The default model, Claude Sonnet 5, rejects
    it outright, so sending it by default cost every agent a failed call
    before the real one on every review.
    """
    import boto3
    from botocore.exceptions import ClientError

    client = boto3.client("bedrock-runtime", region_name=aws_region)

    system: list[dict[str, Any]] = [{"text": system_prompt}]
    if cache:
        system.append({"cachePoint": {"type": "default"}})
    conversation = messages or [
        {"role": "user", "content": [{"text": user_prompt}]},
    ]

    def _call(inference_config: dict[str, Any]) -> dict[str, Any]:
        return client.converse(
            modelId=model_id,
            messages=conversation,
            system=system,
            toolConfig=tool_config or _findings_tool_config(),
            inferenceConfig=inference_config,
        )

    if temperature is None:
        try:
            return _call({"maxTokens": max_output_tokens})
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            raise BedrockError(f"Bedrock API error ({code}): {e}") from e

    try:
        return _call(
            {"maxTokens": max_output_tokens, "temperature": temperature},
        )
    except ClientError as e:
        if not _rejects_sampling_params(e):
            code = e.response.get("Error", {}).get("Code", "")
            raise BedrockError(f"Bedrock API error ({code}): {e}") from e

        # Claude Sonnet 5 and later refuse temperature and top_p outright,
        # answering ValidationException rather than ignoring them, so sending
        # temperature fails the agent before a single token is produced.
        # Asking without it is the only thing the model will accept, and the
        # determinism temperature bought is not available to trade for.
        # maxTokens and the forced tool both stay: one bounds the spend, the
        # other is what keeps the reply parseable.
        logger.warning(
            "Model %s rejects the configured temperature; retrying without "
            "it. Unset PRBOT_TEMPERATURE for this model to save the call.",
            model_id,
        )
        try:
            return _call({"maxTokens": max_output_tokens})
        except ClientError as e2:
            code = e2.response.get("Error", {}).get("Code", "")
            raise BedrockError(f"Bedrock API error ({code}): {e2}") from e2


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

    def _count(key: str) -> int:
        value = usage.get(key, 0)
        return value if isinstance(value, int) else 0

    # With a cache point, inputTokens counts only what was neither read from
    # nor written to the cache, so the other two have to be added back for
    # the total to mean what it meant before caching.
    uncached = _count("inputTokens")
    cache_read = _count("cacheReadInputTokens")
    cache_write = _count("cacheWriteInputTokens")
    output_tokens = _count("outputTokens")
    input_tokens = uncached + cache_read + cache_write

    pricing = get_model_pricing(model_id)
    billed_input = (
        uncached
        + cache_write * CACHE_WRITE_MULTIPLIER
        + cache_read * CACHE_READ_MULTIPLIER
    )
    cost = (
        (billed_input / 1_000_000) * pricing["input"]
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
    check_prefix: str = "Q-",
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

    category = agent_name

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

        # G-08: Validate check_id prefix. SEC-LOG-01: and its shape, since it
        # is echoed into the posted header, read back out of it, and written
        # to the audit log.
        check_id = f.get("check_id", "")
        if not check_id.startswith(check_prefix) or not _CHECK_ID.fullmatch(
            check_id,
        ):
            logger.warning(
                "Dropping finding with unknown check_id: %s",
                check_id[:64],
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

        # A finding whose whole purpose is a reproducible trigger is not
        # a finding without one. The adversarial spec says so; this enforces
        # it, so a model that hedges produces nothing rather than noise.
        failure_scenario = f.get("failure_scenario", "")
        if not isinstance(failure_scenario, str):
            failure_scenario = ""
        if check_prefix == "X-" and not failure_scenario.strip():
            logger.warning(
                "Dropping %s finding with no failure_scenario: %s",
                check_prefix, check_id,
            )
            continue

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
            failure_scenario=failure_scenario,
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
