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


class TestForkGating:
    """A4: the fork workflow's gates must actually stop the job."""

    def test_no_exit_zero_used_as_a_gate(self) -> None:
        """`exit 0` ends the step successfully; the job continues."""
        for name in ("prbot-fork.yml", "prbot.yml"):
            for step in _all_steps(_load(name)):
                run = step.get("run", "")
                assert "exit 0" not in run, (
                    f"{name} step {step.get('name')!r} uses `exit 0` as a gate, "
                    "which does not stop later steps from running"
                )

    def test_author_association_gate_is_job_level(self) -> None:
        workflow = _load("prbot-fork.yml")
        conditions = [
            str(job.get("if", "")) for job in workflow["jobs"].values()
        ]
        joined = " ".join(conditions)
        assert "author_association" in joined, (
            "fork review job has no job-level author_association condition"
        )
        assert "FIRST_TIME_CONTRIBUTOR" in joined
        assert "NONE" in joined

    def test_closed_pr_gate_is_job_level(self) -> None:
        workflow = _load("prbot-fork.yml")
        joined = " ".join(
            str(job.get("if", "")) for job in workflow["jobs"].values()
        )
        assert "state" in joined and "closed" in joined, (
            "fork review job does not skip closed pull requests at job level"
        )


class TestImageVerification:
    """A5: verification must be pinned, enforced, and cover what runs."""

    WORKFLOWS = ("prbot.yml", "prbot-fork.yml")

    @pytest.mark.parametrize("name", WORKFLOWS)
    def test_cosign_identity_is_pinned(self, name: str) -> None:
        for step in _all_steps(_load(name)):
            run = step.get("run", "")
            if "cosign verify" not in run:
                continue
            assert "--certificate-identity-regexp" in run
            match = re.search(
                r"--certificate-identity-regexp[=\s]+'?\"?([^'\"\s]+)",
                run,
            )
            assert match is not None, "identity regexp not parseable"
            # The regexp escapes dots; compare on the unescaped identity.
            pattern = match.group(1).replace("\\", "")
            assert pattern not in (".*", "^.*$"), (
                "cosign identity regexp accepts any signer"
            )
            assert "github.com/nilesuan/prbot" in pattern, (
                f"cosign identity regexp {pattern!r} is not pinned to this repo"
            )

    @pytest.mark.parametrize("name", WORKFLOWS)
    def test_verify_step_cannot_be_skipped(self, name: str) -> None:
        for step in _all_steps(_load(name)):
            if "cosign verify" in step.get("run", ""):
                assert step.get("continue-on-error") is not True, (
                    "verification failure does not stop the job"
                )

    @pytest.mark.parametrize("name", WORKFLOWS)
    def test_image_is_not_referenced_by_mutable_tag(self, name: str) -> None:
        text = (_WORKFLOWS / name).read_text(encoding="utf-8")
        assert "prbot:latest" not in text, (
            "job runs a mutable :latest tag, so the verified image and the "
            "executed image can differ"
        )

    @pytest.mark.parametrize("name", WORKFLOWS)
    def test_image_runs_by_digest(self, name: str) -> None:
        steps = _all_steps(_load(name))
        runs = " ".join(s.get("run", "") for s in steps)
        assert "docker run" in runs, (
            "expected an explicit `docker run` so the digest can be "
            "interpolated; `uses: docker://` does not accept expressions"
        )
        assert "imagetools inspect" in runs, (
            "no step resolves the tag to an immutable digest"
        )
        assert "${PRBOT_IMAGE}@${digest}" in runs, (
            "resolved reference is not pinned by digest"
        )
        assert "steps.image.outputs.ref" in runs, (
            "the container launched is not the reference that was verified"
        )
