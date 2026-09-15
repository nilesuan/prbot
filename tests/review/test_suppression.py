"""Tests for finding suppression (C4).

A nit that reappears on every push is the most common reason a team turns a
review bot off. Suppression has to be possible, and it has to be visible:
a control that silently removes findings is how a review bot becomes
decorative.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from prbot.config import PrBotConfig, SuppressionRule
from prbot.review.models import Finding
from prbot.review.scorer import apply_suppressions


def _finding(**overrides: Any) -> Finding:
    base: dict[str, Any] = {
        "id": "general-1",
        "category": "general",
        "check_id": "Q-MAINT-03",
        "title": "Function is long",
        "description": "d",
        "file_path": "src/legacy/parser.py",
        "line_start": 10,
        "line_end": 20,
        "severity": "low",
        "confidence": 80,
    }
    base.update(overrides)
    return Finding(**base)


def _rule(**overrides: Any) -> SuppressionRule:
    base: dict[str, Any] = {
        "check_id": "Q-MAINT-03",
        "reason": "Legacy module, rewrite tracked in PROJ-123",
    }
    base.update(overrides)
    return SuppressionRule(**base)


class TestMatching:
    def test_an_exact_check_id_is_suppressed(self) -> None:
        kept, suppressed = apply_suppressions([_finding()], [_rule()])
        assert kept == []
        assert len(suppressed) == 1

    def test_a_check_family_prefix_suppresses_the_family(self) -> None:
        kept, suppressed = apply_suppressions(
            [_finding(check_id="Q-MAINT-01"), _finding(check_id="Q-MAINT-04")],
            [_rule(check_id="Q-MAINT")],
        )
        assert kept == []
        assert len(suppressed) == 2

    def test_a_different_check_is_untouched(self) -> None:
        kept, suppressed = apply_suppressions(
            [_finding(check_id="Q-ERR-01")], [_rule()],
        )
        assert len(kept) == 1
        assert suppressed == []

    def test_a_path_narrows_the_rule(self) -> None:
        rule = _rule(path="src/legacy/**")
        kept, _ = apply_suppressions(
            [_finding(file_path="src/current/parser.py")], [rule],
        )
        assert len(kept) == 1

    def test_a_path_matches_with_gitignore_semantics(self) -> None:
        rule = _rule(path="**/legacy/**")
        _, suppressed = apply_suppressions(
            [_finding(file_path="web/legacy/a/b.py")], [rule],
        )
        assert len(suppressed) == 1

    def test_a_rule_without_a_path_applies_everywhere(self) -> None:
        _, suppressed = apply_suppressions(
            [_finding(file_path="anywhere/at/all.py")], [_rule()],
        )
        assert len(suppressed) == 1

    def test_a_severity_ceiling_limits_the_rule(self) -> None:
        """Suppressing nits must not suppress a critical of the same check."""
        rule = _rule(max_severity="medium")
        kept, _ = apply_suppressions(
            [_finding(severity="critical")], [rule],
        )
        assert len(kept) == 1

    def test_a_finding_under_the_ceiling_is_suppressed(self) -> None:
        rule = _rule(max_severity="medium")
        _, suppressed = apply_suppressions([_finding(severity="low")], [rule])
        assert len(suppressed) == 1

    def test_no_rules_suppresses_nothing(self) -> None:
        kept, suppressed = apply_suppressions([_finding()], [])
        assert len(kept) == 1
        assert suppressed == []


class TestSuppressionIsVisible:
    def test_the_reason_travels_with_the_finding(self) -> None:
        _, suppressed = apply_suppressions([_finding()], [_rule()])
        assert suppressed[0][1] == "Legacy module, rewrite tracked in PROJ-123"

    def test_the_footer_reports_the_count(self) -> None:
        from prbot.review.formatter import _format_footer

        footer = _format_footer(0, "", suppressed_count=3)
        assert "3" in footer
        assert "suppress" in footer.lower()

    def test_nothing_is_said_when_nothing_was_suppressed(self) -> None:
        from prbot.review.formatter import _format_footer

        assert "suppress" not in _format_footer(0, "").lower()


class TestRuleValidation:
    def test_a_reason_is_required(self) -> None:
        with pytest.raises(ValidationError):
            SuppressionRule(check_id="Q-MAINT-03")

    def test_an_empty_reason_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SuppressionRule(check_id="Q-MAINT-03", reason="   ")

    def test_a_check_id_is_required(self) -> None:
        with pytest.raises(ValidationError):
            SuppressionRule(reason="because")

    def test_rules_load_from_config(self) -> None:
        config = PrBotConfig(
            platform="github",
            repo="o/r",
            pr_number=1,
            general_model_id="anthropic.claude-sonnet-4-6",
            security_model_id="anthropic.claude-sonnet-4-6",
            suppress=[
                {
                    "check_id": "Q-MAINT",
                    "path": "src/legacy/**",
                    "reason": "rewrite tracked in PROJ-123",
                },
            ],
        )
        assert len(config.suppress) == 1
        assert config.suppress[0].path == "src/legacy/**"

    def test_no_suppressions_by_default(self) -> None:
        config = PrBotConfig(
            platform="github", repo="o/r", pr_number=1,
            general_model_id="anthropic.claude-sonnet-4-6",
            security_model_id="anthropic.claude-sonnet-4-6",
        )
        assert config.suppress == []
