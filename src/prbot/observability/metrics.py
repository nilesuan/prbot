"""Metrics emission (C7).

The audit record already assembles everything worth measuring and wrote it as
one log event. That is enough to reconstruct a single run and not enough to
answer the questions the thresholds depend on: how often reviews block, what
they cost, how many findings get suppressed, whether a change to a prompt
moved any of it.

Three sinks, all optional and all independent:

- a structured `review.metrics` event, always, so a log search can aggregate
- a JSON lines file, when PRBOT_METRICS_FILE is set, so a CI job can upload it
  as an artifact without shipping logs anywhere
- CloudWatch, when PRBOT_METRICS_NAMESPACE is set, using boto3, which is
  already a dependency

Nothing here may fail a review. A metrics sink that takes the pipeline down
with it is worse than no metrics, so every failure is logged and swallowed.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from prbot.observability.audit import AuditRecord

logger = logging.getLogger(__name__)
_events = structlog.get_logger()

# (field, CloudWatch unit). Counts and money, nothing derived: a derived
# metric computed here would disagree with the same figure computed from the
# raw ones later.
_MEASURES: list[tuple[str, str]] = [
    ("score", "None"),
    ("reported_count", "Count"),
    ("borderline_count", "Count"),
    ("hidden_count", "Count"),
    ("suppressed_count", "Count"),
    ("hallucinations_removed", "Count"),
    ("pii_redacted", "Count"),
    ("secrets_redacted", "Count"),
    ("diff_file_count", "Count"),
    ("filtered_file_count", "Count"),
    ("cost_usd", "None"),
    ("exit_code", "None"),
]


def build_metrics(record: AuditRecord) -> dict[str, Any]:
    """The numeric shape of a review, with its dimensions."""
    data = asdict(record)
    metrics: dict[str, Any] = {
        name: data[name] for name, _ in _MEASURES if name in data
    }
    metrics["agent_count"] = len(record.agents)
    metrics["agent_errors"] = sum(
        1 for a in record.agents if not a.status.startswith("success")
    )
    metrics["total_latency_ms"] = sum(a.latency_ms for a in record.agents)
    metrics["input_tokens"] = sum(a.input_tokens for a in record.agents)
    metrics["output_tokens"] = sum(a.output_tokens for a in record.agents)
    metrics["dimensions"] = {
        "repo": record.repo,
        "platform": record.platform,
        "verdict": record.verdict,
    }
    return metrics


def _write_file(metrics: dict[str, Any], path: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(metrics, sort_keys=True) + "\n")


def _put_cloudwatch(
    metrics: dict[str, Any], namespace: str, region: str,
) -> None:
    import boto3

    dimensions = [
        {"Name": key, "Value": str(value)}
        for key, value in metrics["dimensions"].items()
    ]
    data = [
        {
            "MetricName": _camel(name),
            "Value": float(metrics[name]),
            "Unit": unit,
            "Dimensions": dimensions,
        }
        for name, unit in _MEASURES
        if name in metrics
    ]
    client = boto3.client("cloudwatch", region_name=region)
    # PutMetricData takes at most 20 per call.
    for start in range(0, len(data), 20):
        client.put_metric_data(
            Namespace=namespace, MetricData=data[start : start + 20],
        )


def _camel(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_"))


def emit_metrics(
    record: AuditRecord,
    *,
    namespace: str | None = None,
    metrics_file: str | None = None,
    region: str = "ap-southeast-2",
) -> dict[str, Any]:
    """Emit the run's metrics to every configured sink.

    Returns the metrics so a caller can assert on them. Never raises.
    """
    metrics = build_metrics(record)
    _events.info("review.metrics", **metrics)

    if metrics_file:
        try:
            _write_file(metrics, metrics_file)
        except OSError as e:
            logger.warning("metrics.file_failed path=%s error=%s", metrics_file, e)

    if namespace:
        try:
            _put_cloudwatch(metrics, namespace, region)
        except Exception as e:
            # Including a missing permission, which is a deployment question
            # and not a reason to fail a review that already succeeded.
            logger.warning(
                "metrics.cloudwatch_failed namespace=%s error=%s: %s",
                namespace, type(e).__name__, e,
            )

    return metrics
