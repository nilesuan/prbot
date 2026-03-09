"""VCS data models with strict validation (story-3-1).

All models are frozen dataclasses — immutable after construction.
SHA fields are validated against git SHA-1 (40 hex) and SHA-256 (64 hex) formats.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from prbot.exceptions import VCSResponseError

# SHA validation: 40-char (SHA-1) or 64-char (SHA-256) hex
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")

# State record marker for HTML comments
_STATE_MARKER = "<!-- prbot:state:"
_STATE_END_MARKER = " -->"
_STATE_MAX_SIZE = 10 * 1024  # 10KB limit


def _validate_sha(value: str, field_name: str) -> None:
    """Validate a git SHA hash format."""
    if not _SHA_PATTERN.match(value):
        raise ValueError(
            f"{field_name} must be a 40 or 64 character hex SHA: "
            f"{value!r}"
        )


@dataclass(frozen=True)
class PRMetadata:
    """Pull/merge request metadata."""

    title: str
    body: str
    state: str  # "open", "closed", "merged"
    head_sha: str
    base_sha: str
    head_ref: str
    base_ref: str
    author: str
    number: int
    is_draft: bool = False
    is_fork: bool = False

    def __post_init__(self) -> None:
        _validate_sha(self.head_sha, "head_sha")
        _validate_sha(self.base_sha, "base_sha")


@dataclass(frozen=True)
class FileDiff:
    """A single file's diff within a PR."""

    path: str
    status: str  # "added", "modified", "removed", "renamed"
    patch: str = ""
    additions: int = 0
    deletions: int = 0
    previous_path: str | None = None


@dataclass(frozen=True)
class PRDiff:
    """Complete diff for a PR."""

    files: list[FileDiff] = field(default_factory=list)
    head_sha: str = ""
    base_sha: str = ""
    truncated: bool = False

    def __post_init__(self) -> None:
        if self.head_sha:
            _validate_sha(self.head_sha, "head_sha")
        if self.base_sha:
            _validate_sha(self.base_sha, "base_sha")


@dataclass(frozen=True)
class ReviewStateRecord:
    """State record embedded in PR comments for idempotent updates.

    Serializes to/from HTML comments that are invisible in rendered markdown.
    Uses HMAC-SHA256 keyed with review_id for findings_hash integrity.
    """

    review_id: str  # UUID4
    head_sha: str
    score: int
    verdict: str
    findings_hash: str
    timestamp: str  # ISO 8601

    def __post_init__(self) -> None:
        # Validate UUID4 format
        try:
            parsed = uuid.UUID(self.review_id, version=4)
            if str(parsed) != self.review_id:
                raise ValueError
        except (ValueError, AttributeError):
            raise ValueError(
                f"review_id must be a valid UUID4: {self.review_id!r}"
            ) from None

        _validate_sha(self.head_sha, "head_sha")

        if not 0 <= self.score <= 100:
            raise ValueError(
                f"score must be 0-100: {self.score}"
            )

        # Validate ISO 8601 timestamp
        try:
            datetime.fromisoformat(self.timestamp)
        except (ValueError, TypeError):
            raise ValueError(
                f"timestamp must be ISO 8601: {self.timestamp!r}"
            ) from None

    def to_html_comment(self) -> str:
        """Serialize to an HTML comment invisible in rendered markdown."""
        payload = json.dumps({
            "review_id": self.review_id,
            "head_sha": self.head_sha,
            "score": self.score,
            "verdict": self.verdict,
            "findings_hash": self.findings_hash,
            "timestamp": self.timestamp,
        }, separators=(",", ":"))
        return f"{_STATE_MARKER}{payload}{_STATE_END_MARKER}"

    @classmethod
    def from_html_comment(cls, text: str) -> ReviewStateRecord | None:
        """Deserialize from an HTML comment string.

        Returns None if:
        - Text exceeds 10KB size limit
        - No state marker found
        - JSON is malformed
        - Field validation fails (UUID4, SHA, score, timestamp)
        """
        if len(text) > _STATE_MAX_SIZE:
            return None

        start = text.find(_STATE_MARKER)
        if start == -1:
            return None

        json_start = start + len(_STATE_MARKER)
        end = text.find(_STATE_END_MARKER, json_start)
        if end == -1:
            return None

        try:
            data = json.loads(text[json_start:end])
            return cls(
                review_id=data["review_id"],
                head_sha=data["head_sha"],
                score=data["score"],
                verdict=data["verdict"],
                findings_hash=data["findings_hash"],
                timestamp=data["timestamp"],
            )
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            return None

    @staticmethod
    def compute_findings_hash(
        findings: list[dict[str, Any]], review_id: str,
    ) -> str:
        """Compute HMAC-SHA256 hash of findings keyed with review_id.

        Different review_ids produce different hashes for the same findings,
        preventing hash replay attacks.
        """
        payload = json.dumps(
            findings, sort_keys=True, separators=(",", ":"),
        ).encode()
        return hmac.new(
            review_id.encode(), payload, hashlib.sha256,
        ).hexdigest()


def validate_response(
    data: dict[str, Any], required_keys: list[str],
) -> None:
    """Validate API response has all required keys (dot-notation).

    Args:
        data: Response dict to validate.
        required_keys: List of dot-notation paths, e.g. ["head.sha", "base.ref"].

    Raises:
        VCSResponseError: Lists all missing keys.
    """
    missing = []
    for key_path in required_keys:
        parts = key_path.split(".")
        current: Any = data
        for part in parts:
            if not isinstance(current, dict) or part not in current:
                missing.append(key_path)
                break
            current = current[part]

    if missing:
        raise VCSResponseError(
            f"API response missing required fields: {missing}"
        )
