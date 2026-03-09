"""Tests for data residency validation (story-8-5)."""

from __future__ import annotations

import json

import pytest

from prbot.exceptions import ConfigError
from prbot.observability.logging import (
    clear_review_context,
    configure_logging,
)
from prbot.observability.residency import (
    log_data_flow,
    validate_data_residency,
)


class TestValidateDataResidency:
    """Tests for region and profile validation."""

    def test_allowed_region_passes(self) -> None:
        validate_data_residency(
            "ap-southeast-2", ["ap-southeast-2"],
        )

    def test_disallowed_region_raises(self) -> None:
        with pytest.raises(ConfigError, match="not in allowed"):
            validate_data_residency(
                "us-east-1", ["ap-southeast-2"],
            )

    def test_empty_allowed_passes_all(self) -> None:
        validate_data_residency("us-east-1", [])

    def test_us_profile_requires_us_region(self) -> None:
        with pytest.raises(ConfigError, match="US"):
            validate_data_residency(
                "ap-southeast-2",
                ["ap-southeast-2"],
                model_ids=[
                    "us.anthropic.claude-sonnet-4-20250514",
                ],
            )

    def test_us_profile_in_us_region_passes(self) -> None:
        validate_data_residency(
            "us-east-1",
            ["us-east-1"],
            model_ids=[
                "us.anthropic.claude-sonnet-4-20250514",
            ],
        )

    def test_non_prefixed_model_passes(self) -> None:
        validate_data_residency(
            "ap-southeast-2",
            ["ap-southeast-2"],
            model_ids=["anthropic.claude-sonnet-4-20250514"],
        )


class TestLogDataFlow:
    """Tests for data flow logging."""

    def setup_method(self) -> None:
        configure_logging("INFO")
        clear_review_context()

    def teardown_method(self) -> None:
        clear_review_context()

    def test_emits_data_flow_event(
        self, capsys: object,
    ) -> None:
        log_data_flow("github", "ap-southeast-2", "github")
        captured = capsys.readouterr()  # type: ignore[union-attr]
        data = json.loads(captured.out.strip())
        assert data["event"] == "data_flow.path"
        assert data["source_platform"] == "github"
        assert data["processing_region"] == "ap-southeast-2"
