"""Token budget and cost estimation (story-4-3).

Pre-flight checks for diff size and cost before invoking Bedrock.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from prbot.exceptions import BudgetExceededError, TimeoutBudgetExhausted
from prbot.review.prompts import estimate_prompt_tokens
from prbot.review.tools import MAX_CHARS_TOTAL

logger = logging.getLogger(__name__)

# USD per million tokens, Anthropic list price. Bedrock regional endpoints
# (the au.* profiles, which guarantee Australian routing) carry a documented
# 10% premium over global for Sonnet 4.5 and later, so these are a floor
# rather than an exact figure. AWS does not publish Anthropic rates through
# the Price List API, so the premium is not tracked here.
PRICING: dict[str, dict[str, float]] = {
    "au.anthropic.claude-sonnet-5": {
        "input": 2.00,
        "output": 10.00,
    },
    "au.anthropic.claude-sonnet-4-6": {
        "input": 3.00,
        "output": 15.00,
    },
    "au.anthropic.claude-opus-4-6-v1": {
        "input": 5.00,
        "output": 25.00,
    },
    "us.anthropic.claude-sonnet-4-20250514": {
        "input": 3.00,
        "output": 15.00,
    },
    "us.anthropic.claude-opus-4-0-20250514": {
        "input": 15.00,
        "output": 75.00,
    },
}

# Fallback for a model the table does not know (G-09). The point is that it
# bounds every entry above, not that it matches any current model: these are
# the retired Opus 4.1 rates, and today's most expensive Claude is well under
# them. Do not "correct" it down to a current price - that would make an
# unknown model cheaper than a known one and understate the spend it is there
# to catch. test_fallback_is_an_upper_bound enforces the property.
DEFAULT_PRICING: dict[str, float] = {
    "input": 15.00,
    "output": 75.00,
}

def get_model_pricing(model_id: str) -> dict[str, float]:
    """Look up per-million-token pricing for a model.

    Returns DEFAULT_PRICING with warning for unknown models (G-09).
    """
    pricing = PRICING.get(model_id)
    if pricing is None:
        logger.warning(
            "Unknown model %s — using Opus pricing as upper bound",
            model_id,
        )
        return DEFAULT_PRICING
    return pricing


@dataclass(frozen=True)
class CostEstimate:
    """Pre-flight cost estimate for a review run."""

    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost_usd: float
    budget_limit_usd: float
    within_budget: bool


# Prompt caching prices, as multiples of the model's input price: writing a
# five-minute cache entry costs 1.25 times the input rate and reading one
# 0.1 times. These are Anthropic's published ratios for Claude, which
# Bedrock's per-model cache pricing follows; they are not read from any API.
CACHE_WRITE_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.1

# What one agent's reads can add to its prompt: the reader's own character
# limit, so the estimate is a bound rather than a guess (SEC-DESIGN-04).
READ_CHARS_PRICED = MAX_CHARS_TOTAL


def estimate_cost(
    diff_text: str,
    model_ids: list[str],
    budget_limit_usd: float,
    estimated_output_tokens: int = 4096,
    tool_turns: int = 0,
) -> CostEstimate:
    """Estimate cost before running review (S14).

    Args:
        diff_text: Combined prompt text for token estimation.
        model_ids: Models that will be used (cost summed).
        budget_limit_usd: Maximum allowed cost.
        estimated_output_tokens: Expected output tokens per agent.
        tool_turns: read_file turns each agent may take. Priced as the
            worst case, every turn used: the prompt written to the cache
            once and read back on each later turn, the whole read budget
            re-sent uncached on every turn, and a full response per turn.

    Returns:
        CostEstimate with within_budget flag.

    Raises:
        BudgetExceededError: If estimated cost exceeds budget.
    """
    input_tokens = estimate_prompt_tokens(diff_text)
    total_cost = 0.0

    if tool_turns > 0:
        billed_prompt = input_tokens * (
            CACHE_WRITE_MULTIPLIER + CACHE_READ_MULTIPLIER * tool_turns
        )
        read_tokens = estimate_prompt_tokens("x" * READ_CHARS_PRICED)
        billed_input = billed_prompt + read_tokens * tool_turns
        output_tokens = estimated_output_tokens * (tool_turns + 1)
    else:
        billed_input = input_tokens
        output_tokens = estimated_output_tokens

    for model_id in model_ids:
        pricing = get_model_pricing(model_id)
        input_cost = (billed_input / 1_000_000) * pricing["input"]
        output_cost = (output_tokens / 1_000_000) * pricing["output"]
        total_cost += input_cost + output_cost

    within_budget = total_cost <= budget_limit_usd

    estimate = CostEstimate(
        estimated_input_tokens=input_tokens,
        estimated_output_tokens=estimated_output_tokens * len(model_ids),
        estimated_cost_usd=total_cost,
        budget_limit_usd=budget_limit_usd,
        within_budget=within_budget,
    )

    if not within_budget:
        raise BudgetExceededError(
            f"Estimated cost ${total_cost:.4f} exceeds budget "
            f"${budget_limit_usd:.2f}"
        )

    return estimate


class TimeoutBudget:
    """Track remaining time budget for the pipeline (S48)."""

    def __init__(self, total_seconds: float) -> None:
        self._total = total_seconds
        self._start = time.monotonic()

    @property
    def elapsed_seconds(self) -> float:
        """Seconds elapsed since budget creation."""
        return time.monotonic() - self._start

    @property
    def remaining_seconds(self) -> float:
        """Seconds remaining in the budget."""
        return max(0.0, self._total - self.elapsed_seconds)

    @property
    def is_expired(self) -> bool:
        """Whether the budget has been exhausted."""
        return self.remaining_seconds <= 0

    def allocate(self, requested_seconds: float) -> float:
        """Allocate time from the budget.

        Returns the lesser of requested and remaining seconds.

        Raises:
            TimeoutBudgetExhausted: If no time remains.
        """
        remaining = self.remaining_seconds
        if remaining <= 0:
            raise TimeoutBudgetExhausted(
                f"Timeout budget exhausted ({self._total}s total, "
                f"{self.elapsed_seconds:.1f}s elapsed)"
            )
        return min(requested_seconds, remaining)
