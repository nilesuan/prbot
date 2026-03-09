"""Shared test fixtures (story-2-4, story-4-6)."""

from __future__ import annotations

from collections.abc import Generator

import boto3
import pytest
from moto import mock_aws

from prbot.vcs.models import FileDiff, PRDiff, PRMetadata

# Valid 40-char hex SHAs for test fixtures
_HEAD_SHA = "abcdef1234567890abcdef1234567890abcdef12"
_BASE_SHA = "1234567890abcdef1234567890abcdef12345678"


@pytest.fixture
def mock_boto3() -> Generator[boto3.client, None, None]:
    """Provide a moto-backed Secrets Manager with a pre-seeded token."""
    with mock_aws():
        client = boto3.client("secretsmanager", region_name="ap-southeast-2")
        client.create_secret(
            Name="prbot/github-token",
            SecretString="ghp_test_secret_token_value_1234567890abcdef",
        )
        yield client


class FakeVCSAdapter:
    """In-memory VCS adapter for integration testing (G4-05).

    Implements the VCSAdapter Protocol with call recording
    and configurable return values.
    """

    def __init__(
        self,
        metadata: PRMetadata | None = None,
        diff: PRDiff | None = None,
        bot_comment: tuple[int, str] | None = None,
        authenticated_user: str = "prbot[bot]",
    ) -> None:
        self.metadata = metadata or PRMetadata(
            title="Test PR",
            body="Test body",
            state="open",
            head_sha=_HEAD_SHA,
            base_sha=_BASE_SHA,
            head_ref="feature/test",
            base_ref="main",
            author="testuser",
            number=42,
        )
        self.diff = diff or PRDiff(
            files=[
                FileDiff(
                    path="src/example.py",
                    status="modified",
                    patch="@@ -1,3 +1,4 @@\n import os\n+import sys\n",
                    additions=1,
                    deletions=0,
                ),
            ],
            head_sha=_HEAD_SHA,
            base_sha=_BASE_SHA,
        )
        self.bot_comment = bot_comment
        self._authenticated_user = authenticated_user

        # Call recording
        self.calls: list[str] = []
        self.posted_comments: list[str] = []
        self.updated_comments: list[tuple[int, str]] = []
        self._next_comment_id = 100

    async def get_pr_metadata(self) -> PRMetadata:
        self.calls.append("get_pr_metadata")
        return self.metadata

    async def get_diff(self) -> PRDiff:
        self.calls.append("get_diff")
        return self.diff

    async def get_authenticated_user(self) -> str:
        self.calls.append("get_authenticated_user")
        return self._authenticated_user

    async def find_bot_comment(self) -> tuple[int, str] | None:
        self.calls.append("find_bot_comment")
        return self.bot_comment

    async def post_comment(self, body: str) -> int:
        self.calls.append("post_comment")
        self.posted_comments.append(body)
        comment_id = self._next_comment_id
        self._next_comment_id += 1
        return comment_id

    async def update_comment(self, comment_id: int, body: str) -> None:
        self.calls.append("update_comment")
        self.updated_comments.append((comment_id, body))

    async def post_or_update_comment(self, body: str) -> int:
        self.calls.append("post_or_update_comment")
        if self.bot_comment:
            comment_id = self.bot_comment[0]
            self.updated_comments.append((comment_id, body))
            return comment_id
        return await self.post_comment(body)

    async def close(self) -> None:
        self.calls.append("close")


@pytest.fixture
def fake_vcs() -> FakeVCSAdapter:
    """Pre-configured FakeVCSAdapter for testing."""
    return FakeVCSAdapter()
