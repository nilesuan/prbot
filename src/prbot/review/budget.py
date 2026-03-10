"""Token budget and cost estimation (story-4-3).

Pre-flight checks for diff size and cost before invoking Bedrock.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from prbot.exceptions import (
    BudgetExceededError,
    DiffTooLargeError,
    TimeoutBudgetExhausted,
)
from prbot.review.prompts import estimate_prompt_tokens

logger = logging.getLogger(__name__)

# Per-million-token pricing (USD)
PRICING: dict[str, dict[str, float]] = {
    "au.anthropic.claude-sonnet-4-6": {
        "input": 3.00,
        "output": 15.00,
    },
    "au.anthropic.claude-opus-4-6-v1": {
        "input": 15.00,
        "output": 75.00,
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

# Fallback pricing — uses Opus rates as upper bound (G-09)
DEFAULT_PRICING: dict[str, float] = {
    "input": 15.00,
    "output": 75.00,
}

# Single-file dominance threshold — warn if one file is > 80% of diff
_DOMINANCE_THRESHOLD = 0.8


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


def estimate_cost(
    diff_text: str,
    model_ids: list[str],
    budget_limit_usd: float,
    estimated_output_tokens: int = 4096,
) -> CostEstimate:
    """Estimate cost before running review (S14).

    Args:
        diff_text: Combined prompt text for token estimation.
        model_ids: Models that will be used (cost summed).
        budget_limit_usd: Maximum allowed cost.
        estimated_output_tokens: Expected output tokens per agent.

    Returns:
        CostEstimate with within_budget flag.

    Raises:
        BudgetExceededError: If estimated cost exceeds budget.
    """
    input_tokens = estimate_prompt_tokens(diff_text)
    total_cost = 0.0

    for model_id in model_ids:
        pricing = get_model_pricing(model_id)
        input_cost = (input_tokens / 1_000_000) * pricing["input"]
        output_cost = (
            estimated_output_tokens / 1_000_000
        ) * pricing["output"]
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


def validate_diff_size(
    diff_text: str,
    max_diff_tokens: int,
    file_sizes: list[tuple[str, int]] | None = None,
) -> int:
    """Validate diff size before review (S4).

    Args:
        diff_text: Full diff text.
        max_diff_tokens: Maximum allowed tokens.
        file_sizes: Optional list of (path, token_count) for dominance check.

    Returns:
        Estimated token count.

    Raises:
        DiffTooLargeError: If diff exceeds max_diff_tokens.
    """
    tokens = estimate_prompt_tokens(diff_text)

    if tokens > max_diff_tokens:
        raise DiffTooLargeError(
            f"Diff size ({tokens} estimated tokens) exceeds "
            f"max_diff_tokens ({max_diff_tokens})"
        )

    # Check single-file dominance
    if file_sizes and tokens > 0:
        for path, file_tokens in file_sizes:
            ratio = file_tokens / tokens
            if ratio > _DOMINANCE_THRESHOLD:
                logger.warning(
                    "Single file %s dominates diff (%.0f%% of tokens)",
                    path,
                    ratio * 100,
                )

    return tokens


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
