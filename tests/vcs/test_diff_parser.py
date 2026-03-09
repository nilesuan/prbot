"""Tests for diff parser and data models (story-3-7)."""

from __future__ import annotations

import uuid

import pytest

from prbot.exceptions import VCSResponseError
from prbot.vcs.diff_parser import (
    detect_truncation,
    parse_unified_diff,
)
from prbot.vcs.models import (
    PRDiff,
    PRMetadata,
    ReviewStateRecord,
    validate_response,
)


class TestParseUnifiedDiff:
    """Test unified diff parsing to coordinates."""

    def test_simple_hunk(self) -> None:
        patch = "@@ -1,3 +1,5 @@\n+import os\n import sys\n-import old\n+import new\n"
        coords = parse_unified_diff(patch, "main.py")
        # Added: +import os (new_line=1), +import new (new_line=3)
        # Deleted: -import old (old_line=2)
        additions = [c for c in coords if c.old_line is None]
        deletions = [c for c in coords if c.new_line is None]
        assert len(additions) == 2
        assert len(deletions) == 1
        assert additions[0].new_line == 1
        assert deletions[0].old_line == 2

    def test_empty_patch(self) -> None:
        assert parse_unified_diff("", "file.py") == []

    def test_diff_positions_are_sequential(self) -> None:
        patch = "@@ -1,2 +1,3 @@\n+new1\n context\n+new2\n"
        coords = parse_unified_diff(patch, "file.py")
        positions = [c.diff_position for c in coords]
        assert positions == sorted(positions)
        assert all(p > 0 for p in positions)

    def test_multiple_hunks(self) -> None:
        patch = (
            "@@ -1,2 +1,3 @@\n+added1\n context\n"
            "@@ -10,2 +11,3 @@\n+added2\n context\n"
        )
        coords = parse_unified_diff(patch, "file.py")
        additions = [c for c in coords if c.old_line is None]
        assert len(additions) == 2
        assert additions[0].new_line == 1
        assert additions[1].new_line == 11


class TestDetectTruncation:
    """Test truncation detection."""

    def test_github_at_limit(self) -> None:
        assert detect_truncation(3000, "github") is True

    def test_github_below_limit(self) -> None:
        assert detect_truncation(2999, "github") is False

    def test_github_above_limit(self) -> None:
        assert detect_truncation(3001, "github") is True

    def test_gitlab_at_limit(self) -> None:
        assert detect_truncation(1000, "gitlab") is True

    def test_unknown_platform_uses_default(self) -> None:
        assert detect_truncation(3000, "unknown") is True
        assert detect_truncation(2999, "unknown") is False


class TestPRMetadata:
    """Test PRMetadata SHA validation."""

    def test_valid_sha1(self) -> None:
        meta = PRMetadata(
            title="Test", body="", state="open",
            head_sha="a" * 40, base_sha="b" * 40,
            head_ref="main", base_ref="develop",
            author="user", number=1,
        )
        assert meta.head_sha == "a" * 40

    def test_invalid_sha_raises(self) -> None:
        with pytest.raises(ValueError, match="head_sha"):
            PRMetadata(
                title="Test", body="", state="open",
                head_sha="not-a-sha", base_sha="b" * 40,
                head_ref="main", base_ref="develop",
                author="user", number=1,
            )

    def test_sha256_accepted(self) -> None:
        meta = PRMetadata(
            title="Test", body="", state="open",
            head_sha="a" * 64, base_sha="b" * 64,
            head_ref="main", base_ref="develop",
            author="user", number=1,
        )
        assert len(meta.head_sha) == 64


class TestPRDiff:
    """Test PRDiff SHA validation."""

    def test_empty_sha_allowed(self) -> None:
        diff = PRDiff()
        assert diff.head_sha == ""

    def test_invalid_sha_raises(self) -> None:
        with pytest.raises(ValueError, match="head_sha"):
            PRDiff(head_sha="invalid")


class TestReviewStateRecord:
    """Test state record serialization and validation."""

    def _make_record(self, **overrides: object) -> ReviewStateRecord:
        defaults: dict[str, object] = {
            "review_id": str(uuid.uuid4()),
            "head_sha": "a" * 40,
            "score": 85,
            "verdict": "APPROVE",
            "findings_hash": "abc123",
            "timestamp": "2024-01-15T10:30:00+00:00",
        }
        defaults.update(overrides)
        return ReviewStateRecord(**defaults)  # type: ignore[arg-type]

    def test_round_trip(self) -> None:
        record = self._make_record()
        html = record.to_html_comment()
        restored = ReviewStateRecord.from_html_comment(html)
        assert restored is not None
        assert restored.review_id == record.review_id
        assert restored.score == record.score
        assert restored.verdict == record.verdict

    def test_oversized_returns_none(self) -> None:
        big_text = "x" * 50_000
        assert ReviewStateRecord.from_html_comment(big_text) is None

    def test_no_marker_returns_none(self) -> None:
        assert ReviewStateRecord.from_html_comment("no marker") is None

    def test_malformed_json_returns_none(self) -> None:
        text = "<!-- prbot:state:{invalid json -->"
        assert ReviewStateRecord.from_html_comment(text) is None

    def test_invalid_uuid_returns_none(self) -> None:
        text = (
            '<!-- prbot:state:{"review_id":"not-a-uuid",'
            '"head_sha":"' + "a" * 40 + '",'
            '"score":85,"verdict":"APPROVE",'
            '"findings_hash":"abc","timestamp":"2024-01-15T10:30:00+00:00"} -->'
        )
        assert ReviewStateRecord.from_html_comment(text) is None

    def test_invalid_uuid_in_constructor(self) -> None:
        with pytest.raises(ValueError, match="UUID4"):
            self._make_record(review_id="'; DROP TABLE")

    def test_score_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="score"):
            self._make_record(score=101)

    def test_hmac_keying_differs(self) -> None:
        findings = [{"type": "bug", "line": 10}]
        hash_a = ReviewStateRecord.compute_findings_hash(
            findings, "aaa",
        )
        hash_b = ReviewStateRecord.compute_findings_hash(
            findings, "bbb",
        )
        assert hash_a != hash_b

    def test_hmac_same_key_same_result(self) -> None:
        findings = [{"type": "bug", "line": 10}]
        h1 = ReviewStateRecord.compute_findings_hash(findings, "key")
        h2 = ReviewStateRecord.compute_findings_hash(findings, "key")
        assert h1 == h2


class TestValidateResponse:
    """Test API response validation."""

    def test_valid_response(self) -> None:
        data = {"head": {"sha": "abc"}, "base": {"ref": "main"}}
        validate_response(data, ["head.sha", "base.ref"])

    def test_missing_key_raises(self) -> None:
        data = {"state": "open"}
        with pytest.raises(VCSResponseError, match=r"head\.sha"):
            validate_response(data, ["head.sha"])

    def test_missing_nested_key(self) -> None:
        data = {"head": {}}
        with pytest.raises(VCSResponseError, match=r"head\.sha"):
            validate_response(data, ["head.sha"])

    def test_multiple_missing_keys(self) -> None:
        with pytest.raises(VCSResponseError) as exc_info:
            validate_response({}, ["head.sha", "base.ref"])
        assert "head.sha" in str(exc_info.value)
        assert "base.ref" in str(exc_info.value)
