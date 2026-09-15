"""Structural tests for CI workflow definitions (D1, A4, A5).

These assert properties that cannot be expressed in Python code but that
silently regress: whether a gate actually gates, whether signature
verification is pinned to a real identity, and whether the image the job
runs is the image that was verified.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS = _ROOT / ".github" / "workflows"


def _load(name: str) -> dict[str, Any]:
    path = _WORKFLOWS / name
    assert path.is_file(), f"workflow not found: {path}"
    # PyYAML parses the `on:` key as the boolean True; that is fine here
    # because no assertion below reads it.
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _all_steps(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for job in workflow.get("jobs", {}).values():
        steps.extend(job.get("steps", []) or [])
    return steps


class TestCIGate:
    """D1: tests and lint must run in CI."""

    def test_ci_workflow_exists(self) -> None:
        assert (_WORKFLOWS / "ci.yml").is_file(), (
            "no ci.yml workflow: nothing runs pytest or ruff on a pull request"
        )

    def test_ci_runs_ruff(self) -> None:
        steps = _all_steps(_load("ci.yml"))
        commands = " ".join(s.get("run", "") for s in steps)
        assert "ruff check" in commands

    def test_ci_runs_pytest(self) -> None:
        steps = _all_steps(_load("ci.yml"))
        commands = " ".join(s.get("run", "") for s in steps)
        assert "pytest" in commands

    def test_ci_triggers_on_pull_request(self) -> None:
        workflow = _load("ci.yml")
        # PyYAML turns the `on` key into True
        triggers = workflow.get("on") or workflow.get(True)
        assert triggers is not None
        assert "pull_request" in triggers
