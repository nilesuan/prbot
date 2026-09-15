"""Tests for the adversarial agent and its failure_scenario field.

The two shipped agents are category checklists, which find what resembles a
listed pattern and miss what fits no category. Every defect in section A of
the improvement register was of the second kind. The adversarial agent asks
for a concrete trigger instead of a category match, which makes each finding
checkable in seconds rather than arguable.
"""

from __future__ import annotations

from typing import Any

from prbot.config import PrBotConfig
from prbot.review.models import FINDING_JSON_SCHEMA, Finding
from prbot.review.prompts import build_system_prompt, load_check_spec
from prbot.review.runner import _parse_findings


def _config(**overrides: Any) -> PrBotConfig:
    defaults: dict[str, Any] = {
        "platform": "github",
        "repo": "o/r",
        "pr_number": 1,
        "general_model_id": "anthropic.claude-sonnet-4-6",
        "security_model_id": "anthropic.claude-sonnet-4-6",
    }
    defaults.update(overrides)
    return PrBotConfig(**defaults)


class TestSpecIsShipped:
    def test_the_spec_loads(self) -> None:
        spec = load_check_spec("adversarial")
        assert "X-NOOP-01" in spec

    def test_it_covers_the_category_a_checklist_cannot_see(self) -> None:
        spec = load_check_spec("adversarial")
        assert "Silent no-ops" in spec

    def test_it_requires_a_concrete_trigger(self) -> None:
        spec = load_check_spec("adversarial")
        assert "failure_scenario" in spec
        assert "required" in spec

    def test_it_stays_out_of_the_other_agents_lanes(self) -> None:
        spec = load_check_spec("adversarial")
        assert "Do not report naming" in spec

    def test_the_system_prompt_builds(self) -> None:
        prompt = build_system_prompt("adversarial")
        assert "adversarial review agent" in prompt
        assert "X-ORDER-02" in prompt


class TestFailureScenarioReachesTheSchema:
    def test_the_schema_offers_the_field(self) -> None:
        props = FINDING_JSON_SCHEMA["properties"]["findings"]["items"][
            "properties"
        ]
        assert "failure_scenario" in props

    def test_a_finding_carries_it(self) -> None:
        response = {
            "output": {
                "message": {
                    "content": [
                        {
                            "toolUse": {
                                "name": "report_findings",
                                "input": {
                                    "findings": [
                                        {
                                            "check_id": "X-NOOP-03",
                                            "title": "Env var overwritten",
                                            "description": "d",
                                            "failure_scenario": (
                                                "Set PRBOT_DRY_RUN=true "
                                                "without --dry-run: the CLI "
                                                "layer writes False over it "
                                                "and the comment is posted."
                                            ),
                                            "file_path": "src/app.py",
                                            "line_start": 5,
                                            "line_end": 6,
                                            "severity": "high",
                                            "confidence": 95,
                                            "suggestion": "s",
                                        },
                                    ],
                                },
                            },
                        },
                    ],
                },
            },
        }
        findings = _parse_findings(response, "adversarial", "X-")
        assert len(findings) == 1
        assert "PRBOT_DRY_RUN" in findings[0].failure_scenario

    def test_a_finding_without_a_trigger_is_dropped(self) -> None:
        """The whole point is that the scenario is not optional."""
        response = {
            "output": {
                "message": {
                    "content": [
                        {
                            "toolUse": {
                                "name": "report_findings",
                                "input": {
                                    "findings": [
                                        {
                                            "check_id": "X-NOOP-03",
                                            "title": "Something feels off",
                                            "description": "d",
                                            "file_path": "src/app.py",
                                            "line_start": 5,
                                            "line_end": 6,
                                            "severity": "high",
                                            "confidence": 95,
                                        },
                                    ],
                                },
                            },
                        },
                    ],
                },
            },
        }
        assert _parse_findings(response, "adversarial", "X-") == []

    def test_other_agents_do_not_require_a_trigger(self) -> None:
        response = {
            "output": {
                "message": {
                    "content": [
                        {
                            "toolUse": {
                                "name": "report_findings",
                                "input": {
                                    "findings": [
                                        {
                                            "check_id": "Q-ERR-01",
                                            "title": "Bare except",
                                            "description": "d",
                                            "file_path": "src/app.py",
                                            "line_start": 5,
                                            "line_end": 6,
                                            "severity": "medium",
                                            "confidence": 80,
                                        },
                                    ],
                                },
                            },
                        },
                    ],
                },
            },
        }
        assert len(_parse_findings(response, "general", "Q-")) == 1

    def test_the_default_is_empty_not_missing(self) -> None:
        finding = Finding(
            id="general-1",
            category="general",
            check_id="Q-ERR-01",
            title="t",
            description="d",
            file_path="a.py",
            line_start=1,
            line_end=1,
            severity="low",
            confidence=60,
        )
        assert finding.failure_scenario == ""


class TestEnablingTheAgent:
    def test_it_is_one_configuration_entry(self) -> None:
        config = _config(
            agents=[
                {"name": "general", "check_prefix": "Q-"},
                {"name": "security", "check_prefix": "S-"},
                {"name": "adversarial", "check_prefix": "X-"},
            ],
        )
        roster = config.agent_roster()
        assert [a.name for a in roster] == [
            "general", "security", "adversarial",
        ]

    def test_it_is_not_in_the_default_roster(self) -> None:
        """A third agent is roughly 50% more spend per review."""
        assert "adversarial" not in [a.name for a in _config().agent_roster()]

    def test_it_can_run_on_its_own(self) -> None:
        config = _config(
            agents=[{"name": "adversarial", "check_prefix": "X-"}],
        )
        assert [a.name for a in config.agent_roster()] == ["adversarial"]


class TestFormatterShowsTheScenario:
    def test_the_scenario_appears_in_the_comment(self) -> None:
        from prbot.review.formatter import _format_findings_table
        from prbot.review.scorer import ScoredFinding

        scored = ScoredFinding(
            finding=Finding(
                id="adversarial-1",
                category="adversarial",
                check_id="X-NOOP-02",
                title="Glob matches nothing",
                description="The pattern never matches.",
                file_path="src/filter.py",
                line_start=83,
                line_end=83,
                severity="high",
                confidence=95,
                suggestion="Use gitignore semantics.",
                failure_scenario=(
                    "With vendor/** configured, vendor/lib/x.go is reviewed "
                    "anyway because PurePosixPath.match compares from the "
                    "right."
                ),
            ),
            band="reported",
            deduction=14.25,
        )
        out = _format_findings_table([scored])
        assert "How it breaks" in out
        assert "vendor/lib/x.go is reviewed" in out
