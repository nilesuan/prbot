"""Tests for audit trail (story-8-5)."""

from __future__ import annotations

import json
import re
from dataclasses import asdict

from prbot.observability.audit import (
    AgentAuditInfo,
    AuditRecord,
    build_audit_record,
    compute_diff_hash,
    emit_audit_record,
)
from prbot.observability.logging import (
    clear_review_context,
    configure_logging,
)

# Token patterns that must NOT appear in audit records (G-11)
_TOKEN_PATTERNS = re.compile(
    r"(?:"
    r"ghp_[A-Za-z0-9_]{36,}"
    r"|glpat-[A-Za-z0-9_-]{20,}"
    r"|(?:AKIA|ASIA)[A-Z0-9]{16}"
    r"|Bearer\s+[A-Za-z0-9_.=-]+"
    r")",
)


def _make_agent_info(
    name: str = "general",
    status: str = "success",
) -> AgentAuditInfo:
    return AgentAuditInfo(
        name=name,
        model_id="us.anthropic.claude-sonnet-4-20250514",
        status=status,
        finding_count=2,
        input_tokens=1000,
        output_tokens=500,
        latency_ms=3000,
    )


def _make_audit_record(**overrides: object) -> AuditRecord:
    defaults = {
        "review_id": "test-review-id",
        "repo": "owner/repo",
        "pr_number": 42,
        "head_sha": "abcdef" * 7 + "ab",
        "base_sha": "123456" * 7 + "78",
        "author": "alice",
        "head_ref": "feature/x",
        "base_ref": "main",
        "diff_file_count": 5,
        "diff_hash": "a" * 64,
        "filtered_file_count": 3,
        "truncated": False,
        "platform": "github",
        "aws_region": "ap-southeast-2",
        "confidence_threshold": 70,
        "blocker_threshold": 70,
        "max_diff_tokens": 100000,
        "budget_limit_usd": 5.0,
        "draft_behavior": "skip",
        "agents": [_make_agent_info()],
        "verdict": "APPROVE",
        "score": 100,
        "reported_count": 0,
        "borderline_count": 0,
        "hidden_count": 0,
        "hallucinations_removed": 0,
        "pii_redacted": 0,
        "secrets_redacted": 0,
        "comment_posted": True,
        "exit_code": 0,
        "dry_run": False,
    }
    defaults.update(overrides)
    return build_audit_record(**defaults)


class TestComputeDiffHash:
    """Tests for diff hash computation."""

    def test_returns_sha256_hex(self) -> None:
        h = compute_diff_hash("test diff content")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic(self) -> None:
        h1 = compute_diff_hash("same content")
        h2 = compute_diff_hash("same content")
        assert h1 == h2

    def test_different_input_different_hash(self) -> None:
        h1 = compute_diff_hash("content a")
        h2 = compute_diff_hash("content b")
        assert h1 != h2


class TestBuildAuditRecord:
    """Tests for audit record construction."""

    def test_all_fields_populated(self) -> None:
        record = _make_audit_record()
        d = asdict(record)
        for key, value in d.items():
            assert value is not None, f"{key} is None"

    def test_successful_agent(self) -> None:
        agent = _make_agent_info(status="success")
        record = _make_audit_record(agents=[agent])
        assert record.agents[0].status == "success"
        assert record.agents[0].input_tokens > 0

    def test_failed_agent(self) -> None:
        agent = AgentAuditInfo(
            name="security",
            model_id="us.anthropic.claude-opus-4-0-20250514",
            status="error:timeout",
            finding_count=0,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
        )
        record = _make_audit_record(agents=[agent])
        assert record.agents[0].status == "error:timeout"
        assert record.agents[0].input_tokens == 0

    def test_no_sensitive_data_g11(self) -> None:
        """G-11: No token patterns in serialized record."""
        record = _make_audit_record()
        serialized = json.dumps(asdict(record))
        assert not _TOKEN_PATTERNS.search(serialized)

    def test_no_field_exceeds_256_chars(self) -> None:
        """G-11: No field value exceeds 256 characters."""
        record = _make_audit_record()
        d = asdict(record)

        def check_values(obj: object, path: str = "") -> None:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    check_values(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    check_values(v, f"{path}[{i}]")
            elif isinstance(obj, str):
                assert len(obj) <= 256, (
                    f"Field {path} exceeds 256 chars: {len(obj)}"
                )

        check_values(d)

    def test_diff_hash_is_valid_sha256(self) -> None:
        h = compute_diff_hash("test")
        record = _make_audit_record(diff_hash=h)
        assert len(record.diff_hash) == 64


class TestEmitAuditRecord:
    """Tests for audit record emission."""

    def setup_method(self) -> None:
        configure_logging("INFO")
        clear_review_context()

    def teardown_method(self) -> None:
        clear_review_context()

    def test_emits_review_audit_event(
        self, capsys: object,
    ) -> None:
        record = _make_audit_record()
        emit_audit_record(record)
        captured = capsys.readouterr()  # type: ignore[union-attr]
        data = json.loads(captured.out.strip())
        assert data["event"] == "review.audit"
        assert data["review_id"] == "test-review-id"
        assert data["verdict"] == "APPROVE"

    def test_contains_all_fields(
        self, capsys: object,
    ) -> None:
        record = _make_audit_record()
        emit_audit_record(record)
        captured = capsys.readouterr()  # type: ignore[union-attr]
        data = json.loads(captured.out.strip())
        assert "repo" in data
        assert "agents" in data
        assert "score" in data
