"""Structural tests for the Dependabot configuration.

A malformed .github/dependabot.yml does not fail loudly. Dependabot stops
opening pull requests and nothing in CI notices, which looks identical to
having no outstanding updates. These assert the shape instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG = _ROOT / ".github" / "dependabot.yml"

# Verified against the published dependabot v2 schema, which rejects any
# other value for these fields.
_APPLIES_TO = {"version-updates", "security-updates"}
_DEPENDENCY_TYPES = {"development", "production"}
_INTERVALS = {
    "daily", "weekly", "monthly", "quarterly", "semiannually", "yearly", "cron",
}


@pytest.fixture(scope="module")
def config() -> dict[str, Any]:
    assert _CONFIG.is_file(), f"dependabot config not found: {_CONFIG}"
    return yaml.safe_load(_CONFIG.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def updates(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {u["package-ecosystem"]: u for u in config["updates"]}


class TestConfigIsWellFormed:
    def test_schema_version_is_2(self, config: dict[str, Any]) -> None:
        assert config["version"] == 2

    def test_every_block_has_a_valid_interval(
        self, config: dict[str, Any],
    ) -> None:
        for update in config["updates"]:
            interval = update["schedule"]["interval"]
            assert interval in _INTERVALS, (
                f"{update['package-ecosystem']}: {interval!r} is not a "
                f"Dependabot interval, which silently disables the block"
            )

    def test_group_keys_use_valid_values(
        self, config: dict[str, Any],
    ) -> None:
        """A typo here is accepted by YAML and rejected by Dependabot."""
        for update in config["updates"]:
            for name, group in (update.get("groups") or {}).items():
                where = f"{update['package-ecosystem']}/{name}"
                if "applies-to" in group:
                    assert group["applies-to"] in _APPLIES_TO, where
                if "dependency-type" in group:
                    assert group["dependency-type"] in _DEPENDENCY_TYPES, where
                assert group.get("patterns"), f"{where}: matches nothing"


class TestEverythingThatCanGoStaleIsWatched:
    """Each of these has gone stale in this repository before."""

    @pytest.mark.parametrize(
        "ecosystem", ["uv", "docker", "github-actions"],
    )
    def test_ecosystem_is_covered(
        self, updates: dict[str, dict[str, Any]], ecosystem: str,
    ) -> None:
        assert ecosystem in updates, (
            f"nothing watches {ecosystem}; it will age silently"
        )

    def test_docker_is_watched_because_a_digest_pin_does_not_age_well(
        self, updates: dict[str, dict[str, Any]],
    ) -> None:
        """The base image reached 61 CRITICAL/HIGH findings unattended.

        A digest pin fixes what the image is built from and does nothing
        about the packages inside it.
        """
        dockerfiles = list(_ROOT.glob("Dockerfile"))
        assert dockerfiles, "no Dockerfile, so this test is watching nothing"
        assert "@sha256:" in dockerfiles[0].read_text(encoding="utf-8"), (
            "base image is no longer digest-pinned"
        )
        assert "docker" in updates


class TestRuntimeAndDevelopmentAreSeparated:
    """Only some dependencies reach the shipped container.

    Of the five advisories open in September 2026 exactly one, idna, was in
    the runtime closure. Pooling them makes a finding that reaches users look
    like one that cannot.
    """

    def test_security_updates_split_runtime_from_development(
        self, updates: dict[str, dict[str, Any]],
    ) -> None:
        groups = updates["uv"]["groups"]
        security = {
            g.get("dependency-type")
            for g in groups.values()
            if g.get("applies-to") == "security-updates"
        }
        assert security == _DEPENDENCY_TYPES, (
            "security updates must be grouped separately for runtime and "
            f"development dependencies; found {security}"
        )

    def test_the_pr_limit_clears_the_default(
        self, updates: dict[str, dict[str, Any]],
    ) -> None:
        """Five advisories were open at once against a default limit of 5."""
        assert updates["uv"]["open-pull-requests-limit"] > 5
