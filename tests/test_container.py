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
    @classmethod
    def built_image(cls) -> str:
        """Build the Docker image once for all tests.

        A class-scoped fixture must be a classmethod: it runs once per class
        while each test gets a fresh instance, so anything it set on ``self``
        would be invisible to the tests. pytest 9 raises on the instance-method
        form, and this suite turns warnings into errors.
        """
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

    @pytest.mark.parametrize("installer", ["pip", "setuptools", "wheel"])
    def test_installers_are_not_on_disk(
        self, built_image: str, installer: str,
    ) -> None:
        """S86, the half `which` cannot see.

        Removing /usr/local/bin/pip takes the binary off PATH and leaves the
        package in the system interpreter's site-packages. That gap is not
        cosmetic: pip vendors its own copy of msgpack, and Trivy scans files
        rather than PATH, so a surviving pip brings its advisories with it.

        This checks the filesystem, not importability. `import pip` fails in
        this image either way, because PATH puts the virtual environment's
        interpreter first and it cannot see system site-packages, so an
        import check passes even when every file is still there.

        The Dockerfile once hardcoded the python3.12 site-packages path, so
        a base image bump to 3.14 pointed every rm at a path that did not
        exist. rm -rf returns success on a missing path, the build stayed
        green, and the image gained two HIGH advisories.
        """
        result = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "sh", built_image,
             "-c", f"ls -d /usr/local/lib/python3.*/site-packages/{installer}* "
                   f"2>/dev/null"],
            capture_output=True, text=True, timeout=30,
        )
        found = result.stdout.strip()
        assert not found, (
            f"{installer} is still in the image at:\n{found}\n"
            f"the site-packages strip did not run against this "
            f"interpreter's path"
        )

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
        from prbot import __version__

        assert result.returncode == 0
        assert __version__ in result.stdout

    def test_entrypoint_help(self, built_image: str) -> None:
        result = subprocess.run(
            ["docker", "run", "--rm", built_image, "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "prbot" in result.stdout
