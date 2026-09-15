"""Tests for the configurable agent roster (C5).

cli.py hardcoded exactly two agents and prompts.py hardcoded the matching
name set, so adding a reviewer meant editing three modules. A roster driven
by configuration turns "add an infrastructure agent" into configuration, and
lets a repository layer its own checks on the built-ins.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from prbot.config import AgentSpec, PrBotConfig
from prbot.exceptions import ConfigError


def _config(**overrides: object) -> PrBotConfig:
    defaults: dict[str, object] = {
        "platform": "github",
        "repo": "o/r",
        "pr_number": 1,
        "general_model_id": "anthropic.claude-sonnet-4-6",
        "security_model_id": "anthropic.claude-sonnet-4-6",
    }
    defaults.update(overrides)
    return PrBotConfig(**defaults)


class TestDefaultRoster:
    def test_two_agents_by_default(self) -> None:
        roster = _config().agent_roster()
        assert [a.name for a in roster] == ["general", "security"]

    def test_legacy_model_settings_still_drive_the_defaults(self) -> None:
        config = _config(
            general_model_id="anthropic.model-a",
            security_model_id="anthropic.model-b",
        )
        roster = config.agent_roster()
        assert roster[0].model_id == "anthropic.model-a"
        assert roster[1].model_id == "anthropic.model-b"

    def test_default_agents_carry_their_check_prefix(self) -> None:
        roster = _config().agent_roster()
        assert roster[0].check_prefix == "Q-"
        assert roster[1].check_prefix == "S-"


class TestCustomRoster:
    def test_an_agent_can_be_added(self) -> None:
        config = _config(
            agents=[
                {"name": "general", "check_prefix": "Q-"},
                {"name": "security", "check_prefix": "S-"},
                {"name": "adversarial", "check_prefix": "X-"},
            ],
        )
        assert [a.name for a in config.agent_roster()] == [
            "general", "security", "adversarial",
        ]

    def test_an_agent_can_be_disabled(self) -> None:
        config = _config(
            agents=[
                {"name": "general", "check_prefix": "Q-"},
                {"name": "security", "check_prefix": "S-", "enabled": False},
            ],
        )
        assert [a.name for a in config.agent_roster()] == ["general"]

    def test_an_agent_without_a_model_uses_the_default(self) -> None:
        config = _config(
            general_model_id="anthropic.fallback",
            agents=[{"name": "general", "check_prefix": "Q-"}],
        )
        assert config.agent_roster()[0].model_id == "anthropic.fallback"

    def test_an_agent_can_override_its_model(self) -> None:
        config = _config(
            agents=[
                {
                    "name": "security",
                    "check_prefix": "S-",
                    "model_id": "anthropic.something-bigger",
                },
            ],
        )
        assert config.agent_roster()[0].model_id == "anthropic.something-bigger"

    def test_an_empty_roster_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _config(agents=[])

    def test_every_agent_disabled_is_rejected(self) -> None:
        with pytest.raises(ConfigError, match="at least one"):
            _config(
                agents=[
                    {"name": "general", "check_prefix": "Q-", "enabled": False},
                ],
            ).agent_roster()

    def test_duplicate_names_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _config(
                agents=[
                    {"name": "general", "check_prefix": "Q-"},
                    {"name": "general", "check_prefix": "R-"},
                ],
            )


class TestAgentNameIsSafeAsAPath:
    """The name selects a prompt file, so it must not escape the directory."""

    @pytest.mark.parametrize(
        "name",
        ["../etc/passwd", "a/b", "..", "", "A" * 64, "Général", "with space"],
    )
    def test_unsafe_names_are_rejected(self, name: str) -> None:
        with pytest.raises(ValidationError):
            AgentSpec(name=name, check_prefix="Q-")

    @pytest.mark.parametrize("name", ["general", "security", "iac", "perf-2"])
    def test_safe_names_are_accepted(self, name: str) -> None:
        assert AgentSpec(name=name, check_prefix="Q-").name == name


class TestCheckPrefix:
    @pytest.mark.parametrize("prefix", ["Q-", "S-", "X-", "IAC-"])
    def test_valid_prefixes(self, prefix: str) -> None:
        assert AgentSpec(name="a", check_prefix=prefix).check_prefix == prefix

    @pytest.mark.parametrize("prefix", ["", "q-", "Q", "Q_", "Q-1-"])
    def test_invalid_prefixes(self, prefix: str) -> None:
        with pytest.raises(ValidationError):
            AgentSpec(name="a", check_prefix=prefix)
