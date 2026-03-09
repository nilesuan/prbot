"""Container smoke tests — CI-only (story-7-5).

These tests require Docker and are skipped unless
the PRBOT_RUN_DOCKER env var is set.

Run with: PRBOT_RUN_DOCKER=1 pytest tests/test_container.py
"""

from __future__ import annotations

import os
import subprocess

import pytest

# Skip entire module unless explicitly opted in
pytestmark = pytest.mark.skipif(
    not os.environ.get("PRBOT_RUN_DOCKER"),
    reason="Set PRBOT_RUN_DOCKER=1 to run container tests",
)


class TestDockerfile:
    """Smoke tests for the production container image."""

    @pytest.fixture(scope="class")
    def built_image(self) -> str:
        """Build the Docker image once for all tests."""
        tag = "prbot:test-smoke"
        result = subprocess.run(
            ["docker", "build", "-t", tag, "."],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)),
            ),
        )
        if result.returncode != 0:
            pytest.fail(f"Docker build failed:\n{result.stderr}")
        return tag

    def test_builds_successfully(self, built_image: str) -> None:
        assert built_image == "prbot:test-smoke"

    def test_no_uv_in_runtime(self, built_image: str) -> None:
        """S86: No uv binary in final image."""
        result = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint",
             "which", built_image, "uv"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode != 0

    def test_no_pip_in_runtime(self, built_image: str) -> None:
        """S86: No pip binary in final image."""
        result = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint",
             "which", built_image, "pip"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode != 0

    def test_runs_as_non_root(self, built_image: str) -> None:
        result = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint",
             "whoami", built_image],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "prbot"

    def test_module_imports(self, built_image: str) -> None:
        result = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "python",
             built_image, "-c",
             "import prbot; print(prbot.__version__)"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "0.1.0" in result.stdout

    def test_entrypoint_help(self, built_image: str) -> None:
        result = subprocess.run(
            ["docker", "run", "--rm", built_image, "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "prbot" in result.stdout
