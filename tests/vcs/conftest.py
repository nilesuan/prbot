"""VCS test fixtures with concrete API response bodies (NG-29)."""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture
def github_pr_response() -> dict[str, Any]:
    """Concrete GitHub PR API response."""
    return {
        "number": 42,
        "title": "Add feature X",
        "body": "This PR adds feature X to the codebase.",
        "state": "open",
        "draft": False,
        "merged": False,
        "user": {"login": "octocat", "id": 1},
        "head": {
            "sha": "abcdef1234567890abcdef1234567890abcdef12",
            "ref": "feature-x",
            "repo": {"full_name": "owner/repo", "id": 100},
        },
        "base": {
            "sha": "1234567890abcdef1234567890abcdef12345678",
            "ref": "main",
            "repo": {"full_name": "owner/repo", "id": 100},
        },
    }


@pytest.fixture
def github_pr_fork_response(
    github_pr_response: dict[str, Any],
) -> dict[str, Any]:
    """GitHub PR from a fork."""
    resp = dict(github_pr_response)
    resp["head"] = dict(resp["head"])
    resp["head"]["repo"] = {"full_name": "forker/repo", "id": 200}
    return resp


@pytest.fixture
def github_pr_merged_response(
    github_pr_response: dict[str, Any],
) -> dict[str, Any]:
    """GitHub PR that is merged."""
    resp = dict(github_pr_response)
    resp["state"] = "closed"
    resp["merged"] = True
    return resp


@pytest.fixture
def github_diff_response() -> list[dict[str, Any]]:
    """Concrete GitHub List PR Files response."""
    return [
        {
            "sha": "aaaa1234567890abcdef1234567890abcdef1234",
            "filename": "src/main.py",
            "status": "modified",
            "additions": 10,
            "deletions": 3,
            "patch": (
                "@@ -1,3 +1,10 @@\n+import os\n"
                " import sys\n-import old\n+import new\n"
            ),
        },
        {
            "sha": "bbbb1234567890abcdef1234567890abcdef1234",
            "filename": "README.md",
            "status": "added",
            "additions": 5,
            "deletions": 0,
            "patch": "@@ -0,0 +1,5 @@\n+# Project\n+\n+Description\n",
        },
    ]


@pytest.fixture
def gitlab_mr_response() -> dict[str, Any]:
    """Concrete GitLab MR API response."""
    return {
        "iid": 99,
        "title": "Add feature Y",
        "description": "This MR adds feature Y.",
        "state": "opened",
        "draft": False,
        "work_in_progress": False,
        "sha": "abcdef1234567890abcdef1234567890abcdef12",
        "diff_refs": {
            "base_sha": "1234567890abcdef1234567890abcdef12345678",
            "head_sha": "abcdef1234567890abcdef1234567890abcdef12",
            "start_sha": "1234567890abcdef1234567890abcdef12345678",
        },
        "source_branch": "feature-y",
        "target_branch": "main",
        "source_project_id": 100,
        "target_project_id": 100,
        "author": {"username": "gitlab_user", "id": 1},
    }


@pytest.fixture
def gitlab_mr_fork_response(
    gitlab_mr_response: dict[str, Any],
) -> dict[str, Any]:
    """GitLab MR from a fork."""
    resp = dict(gitlab_mr_response)
    resp["source_project_id"] = 200
    return resp
