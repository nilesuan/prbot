"""Release metadata consistency (D7).

The version sat at 0.1.0 across twenty commits including a feature commit,
there was no CHANGELOG, and README linked to a LICENSE that did not exist.
Q-COMP-02 and Q-COMP-03, checks this project added itself, would each have
flagged the repository they were added to.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import prbot

_ROOT = Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    with (_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


class TestVersionIsConsistent:
    def test_package_and_pyproject_agree(self) -> None:
        assert prbot.__version__ == _pyproject_version()

    def test_version_is_semver(self) -> None:
        parts = prbot.__version__.split(".")
        assert len(parts) == 3
        assert all(part.isdigit() for part in parts)


class TestChangelog:
    def test_changelog_exists(self) -> None:
        assert (_ROOT / "CHANGELOG.md").is_file()

    def test_current_version_has_an_entry(self) -> None:
        text = (_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        assert f"## [{prbot.__version__}]" in text, (
            f"CHANGELOG.md has no entry for {prbot.__version__}"
        )


class TestReadmeLinksResolve:
    def test_no_link_to_a_missing_local_file(self) -> None:
        import re

        text = (_ROOT / "README.md").read_text(encoding="utf-8")
        targets = re.findall(r"\]\((?!https?://|#)([^)]+)\)", text)
        missing = [t for t in targets if not (_ROOT / t).exists()]
        assert not missing, f"README links to missing files: {missing}"
