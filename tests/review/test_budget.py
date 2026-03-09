"""Tests for budget and cost estimation (story-4-7)."""

from __future__ import annotations

import time

import pytest

from prbot.exceptions import (
    BudgetExceededError,
    DiffTooLargeError,
    TimeoutBudgetExhausted,
)
from prbot.review.budget import (
    CostEstimate,
    TimeoutBudget,
    estimate_cost,
    get_model_pricing,
    validate_diff_size,
)


class TestGetModelPricing:
    """Tests for model pricing lookup."""

    def test_known_model(self) -> None:
        pricing = get_model_pricing("us.anthropic.claude-sonnet-4-20250514")
        assert pricing["input"] == 3.00
        assert pricing["output"] == 15.00

    def test_unknown_model_falls_back(self) -> None:
        pricing = get_model_pricing("unknown-model-xyz")
        # Falls back to Opus pricing as upper bound
        assert pricing["input"] == 15.00
        assert pricing["output"] == 75.00


class TestCostEstimate:
    """Tests for estimate_cost pre-flight check."""

    def test_within_budget(self) -> None:
        estimate = estimate_cost(
            diff_text="x" * 400,
            model_ids=["us.anthropic.claude-sonnet-4-20250514"],
            budget_limit_usd=10.0,
        )
        assert isinstance(estimate, CostEstimate)
        assert estimate.within_budget is True
        assert estimate.estimated_cost_usd > 0

    def test_exceeds_budget_raises(self) -> None:
        with pytest.raises(BudgetExceededError, match="exceeds budget"):
            estimate_cost(
                diff_text="x" * 400,
                model_ids=["us.anthropic.claude-sonnet-4-20250514"],
                budget_limit_usd=0.0000001,
            )

    def test_multiple_models_sum_cost(self) -> None:
        single = estimate_cost(
            diff_text="x" * 400,
            model_ids=["us.anthropic.claude-sonnet-4-20250514"],
            budget_limit_usd=10.0,
        )
        double = estimate_cost(
            diff_text="x" * 400,
            model_ids=[
                "us.anthropic.claude-sonnet-4-20250514",
                "us.anthropic.claude-sonnet-4-20250514",
            ],
            budget_limit_usd=10.0,
        )
        assert double.estimated_cost_usd > single.estimated_cost_usd


class TestValidateDiffSize:
    """Tests for validate_diff_size."""

    def test_within_limit(self) -> None:
        tokens = validate_diff_size("small diff", max_diff_tokens=100000)
        assert tokens > 0

    def test_exceeds_limit_raises(self) -> None:
        with pytest.raises(DiffTooLargeError, match="exceeds"):
            validate_diff_size("x" * 10000, max_diff_tokens=1)

    def test_returns_token_count(self) -> None:
        # 400 chars / 4 * 1.5 = 150 tokens
        tokens = validate_diff_size("x" * 400, max_diff_tokens=200)
        assert tokens == 150


class TestTimeoutBudget:
    """Tests for TimeoutBudget time tracking."""

    def test_initial_state(self) -> None:
        budget = TimeoutBudget(60.0)
        assert budget.remaining_seconds > 59.0
        assert budget.is_expired is False

    def test_allocate_within_budget(self) -> None:
        budget = TimeoutBudget(60.0)
        allocated = budget.allocate(30.0)
        assert allocated == 30.0

    def test_allocate_capped_to_remaining(self) -> None:
        budget = TimeoutBudget(5.0)
        allocated = budget.allocate(120.0)
        assert allocated <= 5.0

    def test_expired_budget_raises(self) -> None:
        budget = TimeoutBudget(0.0)
        with pytest.raises(TimeoutBudgetExhausted):
            budget.allocate(1.0)

    def test_elapsed_increases(self) -> None:
        budget = TimeoutBudget(60.0)
        t1 = budget.elapsed_seconds
        # Small busy wait
        time.sleep(0.01)
        t2 = budget.elapsed_seconds
        assert t2 > t1
