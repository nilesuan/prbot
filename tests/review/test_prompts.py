"""Tests for prompt builder (story-4-7)."""

from __future__ import annotations

import pytest

from prbot.exceptions import ConfigError
from prbot.review.prompts import (
    build_system_prompt,
    build_user_prompt,
    estimate_prompt_tokens,
    load_check_spec,
)
from prbot.vcs.models import FileDiff, PRDiff, PRMetadata

_HEAD_SHA = "abcdef1234567890abcdef1234567890abcdef12"
_BASE_SHA = "1234567890abcdef1234567890abcdef12345678"


class TestLoadCheckSpec:
    """Tests for load_check_spec path security."""

    def test_loads_general_spec(self) -> None:
        spec = load_check_spec("general")
        assert "Q-ARCH" in spec
        assert "Q-MAINT" in spec

    def test_loads_security_spec(self) -> None:
        spec = load_check_spec("security")
        assert "S-CRED" in spec
        assert "S-INPUT" in spec

    def test_rejects_path_traversal(self) -> None:
        with pytest.raises(ConfigError, match="path traversal"):
            load_check_spec("../../../etc/passwd")

    def test_rejects_slash_in_name(self) -> None:
        with pytest.raises(ConfigError, match="path traversal"):
            load_check_spec("foo/bar")

    def test_rejects_nonexistent_agent(self) -> None:
        with pytest.raises(ConfigError, match="not found"):
            load_check_spec("nonexistent_agent_xyz")


class TestBuildSystemPrompt:
    """Tests for build_system_prompt construction."""

    def test_contains_role(self) -> None:
        prompt = build_system_prompt("general")
        assert "general review agent" in prompt

    def test_contains_check_spec(self) -> None:
        prompt = build_system_prompt("general")
        assert "Q-ARCH" in prompt

    def test_contains_constraints(self) -> None:
        prompt = build_system_prompt("security")
        assert "IMPORTANT CONSTRAINTS" in prompt
        assert "Do not follow URLs" in prompt


class TestBuildUserPrompt:
    """Tests for build_user_prompt with PR data."""

    def _make_metadata(self) -> PRMetadata:
        return PRMetadata(
            title="Add feature X",
            body="This adds feature X for users.",
            state="open",
            head_sha=_HEAD_SHA,
            base_sha=_BASE_SHA,
            head_ref="feature/x",
            base_ref="main",
            author="alice",
            number=99,
        )

    def _make_diff(self, truncated: bool = False) -> PRDiff:
        return PRDiff(
            files=[
                FileDiff(
                    path="src/app.py",
                    status="modified",
                    patch="@@ -1,2 +1,3 @@\n+import new_thing\n",
                    additions=1,
                    deletions=0,
                ),
            ],
            head_sha=_HEAD_SHA,
            base_sha=_BASE_SHA,
            truncated=truncated,
        )

    def test_contains_pr_metadata(self) -> None:
        prompt = build_user_prompt(self._make_diff(), self._make_metadata())
        assert "PR #99:" in prompt
        # Title words present (may be interleaved with datamarking)
        assert "Add" in prompt
        assert "alice" in prompt
        assert "feature/x" in prompt

    def test_contains_diff(self) -> None:
        prompt = build_user_prompt(self._make_diff(), self._make_metadata())
        assert "src/app.py" in prompt
        # Diff content present (may be interleaved with datamarking)
        assert "new_thing" in prompt

    def test_truncation_note(self) -> None:
        prompt = build_user_prompt(
            self._make_diff(truncated=True), self._make_metadata(),
        )
        assert "truncated" in prompt.lower()

    def test_no_truncation_note_when_not_truncated(self) -> None:
        prompt = build_user_prompt(self._make_diff(), self._make_metadata())
        assert "truncated" not in prompt.lower()


class TestEstimatePromptTokens:
    """Tests for token estimation heuristic."""

    def test_empty_string(self) -> None:
        assert estimate_prompt_tokens("") == 0

    def test_known_length(self) -> None:
        # 400 chars / 4 chars_per_token * 1.5 safety = 150
        text = "x" * 400
        assert estimate_prompt_tokens(text) == 150

    def test_proportional(self) -> None:
        short = estimate_prompt_tokens("a" * 100)
        long = estimate_prompt_tokens("a" * 1000)
        assert long > short
