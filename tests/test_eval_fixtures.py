"""The eval fixtures, checked without spending money.

evals/ makes real Bedrock calls, so it cannot run in CI. These tests assert
the deterministic half of what it covers: that every injection payload in
every fixture is actually datamarked by the time the prompt is built.

That is the property the paid suite depends on. If datamarking regresses,
this fails in CI in milliseconds rather than the next time someone thinks to
spend thirty cents.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evals"))

from fixtures import FIXTURES, INJECTION, MARKER

from prbot.review.prompts import build_system_prompt, build_user_prompt
from prbot.security.datamarking import get_session_mark

_INJECTION_FIXTURES = [n for n in FIXTURES if n.startswith("inject-")]


class TestEveryPayloadIsMarked:
    """No injected sentence may reach the model as contiguous prose."""

    @pytest.mark.parametrize("name", _INJECTION_FIXTURES)
    def test_the_payload_is_broken_up(self, name: str) -> None:
        fixture = FIXTURES[name]
        prompt = build_user_prompt(fixture.diff, fixture.metadata)

        # The payload's own words must not appear consecutively: datamarking
        # interleaves a marker between them.
        for phrase in (
            "Ignore all previous instructions",
            "ignore all previous instructions",
            "New instructions supersede",
        ):
            assert phrase not in prompt, (
                f"{name}: payload reached the prompt unmarked: {phrase!r}"
            )

    @pytest.mark.parametrize("name", _INJECTION_FIXTURES)
    def test_the_marker_word_never_arrives_intact(self, name: str) -> None:
        """The word the payload asks the model to echo back."""
        fixture = FIXTURES[name]
        prompt = build_user_prompt(fixture.diff, fixture.metadata)
        mark = f"^{get_session_mark()}^"

        # It may appear, but only immediately behind a datamark.
        idx = 0
        while (idx := prompt.find(MARKER, idx)) != -1:
            preceding = prompt[max(0, idx - 60):idx]
            assert mark in preceding, (
                f"{name}: {MARKER} appears without a preceding marker"
            )
            idx += len(MARKER)

    @pytest.mark.parametrize("name", _INJECTION_FIXTURES)
    def test_the_prompt_still_contains_the_real_diff(self, name: str) -> None:
        """A fixture that marks everything into noise tests nothing."""
        fixture = FIXTURES[name]
        prompt = build_user_prompt(fixture.diff, fixture.metadata)
        assert "```diff" in prompt
        assert "@@" in prompt


class TestFixturesAreWellFormed:
    def test_every_fixture_has_a_description(self) -> None:
        for fixture in FIXTURES.values():
            assert fixture.description.strip()

    def test_every_injection_fixture_plants_a_defect(self) -> None:
        """Otherwise 'no findings' cannot be distinguished from success."""
        for name in _INJECTION_FIXTURES:
            assert FIXTURES[name].has_defect, (
                f"{name} has no planted defect, so a silenced review would "
                "look identical to a correct one"
            )

    def test_the_clean_control_has_no_defect(self) -> None:
        assert not FIXTURES["control-clean"].has_defect

    def test_the_payload_asks_for_something_observable(self) -> None:
        assert MARKER in INJECTION

    def test_every_agent_spec_builds_a_system_prompt(self) -> None:
        for agent in ("general", "security", "adversarial"):
            assert "review agent" in build_system_prompt(agent)
