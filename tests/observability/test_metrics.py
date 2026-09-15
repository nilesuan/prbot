"""Tests for metrics emission (C7)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from prbot.observability.audit import AgentAuditInfo, build_audit_record
from prbot.observability.metrics import build_metrics, emit_metrics


def _record(**overrides: Any):
    defaults: dict[str, Any] = {
        "review_id": "rid",
        "repo": "owner/repo",
        "pr_number": 42,
        "head_sha": "a" * 40,
        "base_sha": "b" * 40,
        "author": "someone",
        "head_ref": "feature",
        "base_ref": "main",
        "diff_file_count": 7,
        "diff_hash": "c" * 64,
        "filtered_file_count": 5,
        "truncated": False,
        "platform": "github",
        "aws_region": "ap-southeast-2",
        "confidence_threshold": 70,
        "blocker_threshold": 70,
        "max_diff_tokens": 100_000,
        "budget_limit_usd": 5.0,
        "draft_behavior": "skip",
        "agents": [
            AgentAuditInfo(
                name="general", model_id="m", status="success",
                finding_count=2, input_tokens=1000, output_tokens=200,
                latency_ms=1500, cost_usd=0.01,
            ),
            AgentAuditInfo(
                name="security", model_id="m", status="error:timeout",
                finding_count=0, input_tokens=0, output_tokens=0,
                latency_ms=0, cost_usd=0.0,
            ),
        ],
        "verdict": "COMMENT",
        "score": 88,
        "reported_count": 2,
        "borderline_count": 1,
        "hidden_count": 3,
        "suppressed_count": 1,
        "hallucinations_removed": 1,
        "pii_redacted": 0,
        "secrets_redacted": 0,
        "comment_posted": True,
        "exit_code": 0,
        "dry_run": False,
        "cost_usd": 0.01,
    }
    defaults.update(overrides)
    return build_audit_record(**defaults)


class TestBuildMetrics:
    def test_counts_are_carried(self) -> None:
        m = build_metrics(_record())
        assert m["score"] == 88
        assert m["reported_count"] == 2
        assert m["suppressed_count"] == 1
        assert m["cost_usd"] == 0.01

    def test_agent_figures_are_aggregated(self) -> None:
        m = build_metrics(_record())
        assert m["agent_count"] == 2
        assert m["agent_errors"] == 1
        assert m["total_latency_ms"] == 1500
        assert m["input_tokens"] == 1000
        assert m["output_tokens"] == 200

    def test_dimensions_identify_the_run(self) -> None:
        m = build_metrics(_record())
        assert m["dimensions"] == {
            "repo": "owner/repo", "platform": "github", "verdict": "COMMENT",
        }

    def test_no_identifying_content_leaks_in(self) -> None:
        """Metrics are aggregates, not a copy of the audit record."""
        m = build_metrics(_record())
        for field in ("author", "head_sha", "diff_hash", "review_id"):
            assert field not in m


class TestFileSink:
    def test_a_line_is_appended(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "metrics.jsonl"
        emit_metrics(_record(), metrics_file=str(target))
        emit_metrics(_record(score=50), metrics_file=str(target))
        lines = target.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[1])["score"] == 50

    def test_an_unwritable_path_does_not_fail_the_review(
        self, tmp_path: Path,
    ) -> None:
        blocker = tmp_path / "file"
        blocker.write_text("x")
        # A path under a regular file cannot be created
        emit_metrics(_record(), metrics_file=str(blocker / "metrics.jsonl"))


class TestCloudWatchSink:
    def test_metrics_are_published_when_a_namespace_is_set(self) -> None:
        client = MagicMock()
        boto3 = MagicMock()
        boto3.client.return_value = client
        with patch.dict("sys.modules", {"boto3": boto3}):
            emit_metrics(_record(), namespace="prbot")

        assert client.put_metric_data.called
        call = client.put_metric_data.call_args.kwargs
        assert call["Namespace"] == "prbot"
        names = {d["MetricName"] for d in call["MetricData"]}
        assert "Score" in names
        assert "SuppressedCount" in names

    def test_batches_respect_the_api_limit(self) -> None:
        client = MagicMock()
        boto3 = MagicMock()
        boto3.client.return_value = client
        with patch.dict("sys.modules", {"boto3": boto3}):
            emit_metrics(_record(), namespace="prbot")

        for call in client.put_metric_data.call_args_list:
            assert len(call.kwargs["MetricData"]) <= 20

    def test_dimensions_are_attached(self) -> None:
        client = MagicMock()
        boto3 = MagicMock()
        boto3.client.return_value = client
        with patch.dict("sys.modules", {"boto3": boto3}):
            emit_metrics(_record(), namespace="prbot")

        first = client.put_metric_data.call_args.kwargs["MetricData"][0]
        assert {"Name": "verdict", "Value": "COMMENT"} in first["Dimensions"]

    def test_a_failure_does_not_fail_the_review(self) -> None:
        client = MagicMock()
        client.put_metric_data.side_effect = RuntimeError("AccessDenied")
        boto3 = MagicMock()
        boto3.client.return_value = client
        with patch.dict("sys.modules", {"boto3": boto3}):
            emit_metrics(_record(), namespace="prbot")

    def test_nothing_is_published_without_a_namespace(self) -> None:
        client = MagicMock()
        boto3 = MagicMock()
        boto3.client.return_value = client
        with patch.dict("sys.modules", {"boto3": boto3}):
            emit_metrics(_record())
        assert not client.put_metric_data.called


class TestAlwaysEmitsAnEvent:
    def test_the_structured_event_is_emitted(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from prbot.observability.logging import configure_logging

        configure_logging("INFO")
        emit_metrics(_record())
        found = False
        for line in capsys.readouterr().out.splitlines():
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if payload.get("event") == "review.metrics":
                found = True
                assert payload["score"] == 88
        assert found
