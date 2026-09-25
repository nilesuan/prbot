"""Tests for budget and cost estimation (story-4-7)."""

from __future__ import annotations

import time

import pytest

from prbot.exceptions import (
    BudgetExceededError,
    TimeoutBudgetExhausted,
)
from prbot.review.budget import (
    DEFAULT_PRICING,
    PRICING,
    CostEstimate,
    TimeoutBudget,
    estimate_cost,
    get_model_pricing,
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

    def test_sonnet_5_priced(self) -> None:
        """Anthropic lists Sonnet 5 at $2/$10 per million tokens."""
        pricing = get_model_pricing("au.anthropic.claude-sonnet-5")
        assert pricing["input"] == 2.00
        assert pricing["output"] == 10.00

    @pytest.mark.parametrize(
        "field", ["general_model_id", "security_model_id"],
    )
    def test_default_models_are_priced(self, field: str) -> None:
        """Every default model must have a real entry, not the fallback.

        Without this, changing a default to a model the table does not know
        silently prices the run at the Opus upper bound, inflating every cost
        estimate and tripping the budget check for a reason no log explains.
        """
        from prbot.config import PrBotConfig

        model_id = PrBotConfig.model_fields[field].default
        assert model_id in PRICING, (
            f"default {field}={model_id!r} has no PRICING entry, so it would "
            f"fall back to {DEFAULT_PRICING}"
        )

    def test_fallback_is_an_upper_bound(self) -> None:
        """The fallback only works as a safe default if nothing exceeds it."""
        for model_id, pricing in PRICING.items():
            assert pricing["input"] <= DEFAULT_PRICING["input"], model_id
            assert pricing["output"] <= DEFAULT_PRICING["output"], model_id


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


class TestToolTurnsAreBudgeted:
    """Each tool turn re-sends the conversation and produces more output."""

    _MODEL = "au.anthropic.claude-sonnet-5"

    def _cost(self, turns: int) -> float:
        return estimate_cost(
            diff_text="x" * 400_000, model_ids=[self._MODEL],
            budget_limit_usd=100.0, tool_turns=turns,
        ).estimated_cost_usd

    def test_zero_turns_is_the_single_call_estimate(self) -> None:
        plain = estimate_cost(
            diff_text="x" * 400_000, model_ids=[self._MODEL],
            budget_limit_usd=100.0,
        ).estimated_cost_usd
        assert self._cost(0) == plain

    def test_turns_raise_the_estimate(self) -> None:
        assert self._cost(1) > self._cost(0)
        assert self._cost(3) > self._cost(1)

    def test_caching_keeps_turns_well_below_resending(self) -> None:
        """Three reads cost far less than four full calls, because of the cache."""
        assert self._cost(3) < 3 * self._cost(0)
