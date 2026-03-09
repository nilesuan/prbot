"""Audit trail builder (story-8-2).

Captures everything needed to reconstruct a review decision.
NO sensitive data in the record (G-11).
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field

import structlog

logger = structlog.get_logger()


@dataclass(frozen=True)
class AgentAuditInfo:
    """Per-agent audit fields."""

    name: str
    model_id: str
    status: str  # "success" or "error:{type}"
    finding_count: int
    input_tokens: int
    output_tokens: int
    latency_ms: int


@dataclass(frozen=True)
class AuditRecord:
    """Complete audit record for a single review run (G-11)."""

    # Identity
    review_id: str
    repo: str
    pr_number: int
    head_sha: str
    base_sha: str
    author: str
    head_ref: str
    base_ref: str

    # Input
    diff_file_count: int
    diff_hash: str  # SHA-256 of diff text
    filtered_file_count: int
    truncated: bool

    # Config
    platform: str
    aws_region: str
    confidence_threshold: int
    blocker_threshold: int
    max_diff_tokens: int
    budget_limit_usd: float
    draft_behavior: str

    # Scoring
    verdict: str
    score: int
    reported_count: int
    borderline_count: int
    hidden_count: int

    # Safety
    hallucinations_removed: int
    pii_redacted: int
    secrets_redacted: int

    # Output
    comment_posted: bool
    exit_code: int
    dry_run: bool

    # Agents (default last — frozen dataclass ordering)
    agents: list[AgentAuditInfo] = field(default_factory=list)


def compute_diff_hash(diff_text: str) -> str:
    """Compute SHA-256 hash of diff text for audit trail."""
    return hashlib.sha256(diff_text.encode()).hexdigest()


def build_audit_record(
    *,
    review_id: str,
    repo: str,
    pr_number: int,
    head_sha: str,
    base_sha: str,
    author: str,
    head_ref: str,
    base_ref: str,
    diff_file_count: int,
    diff_hash: str,
    filtered_file_count: int,
    truncated: bool,
    platform: str,
    aws_region: str,
    confidence_threshold: int,
    blocker_threshold: int,
    max_diff_tokens: int,
    budget_limit_usd: float,
    draft_behavior: str,
    agents: list[AgentAuditInfo],
    verdict: str,
    score: int,
    reported_count: int,
    borderline_count: int,
    hidden_count: int,
    hallucinations_removed: int,
    pii_redacted: int,
    secrets_redacted: int,
    comment_posted: bool,
    exit_code: int,
    dry_run: bool,
) -> AuditRecord:
    """Build a complete audit record from pipeline state."""
    return AuditRecord(
        review_id=review_id,
        repo=repo,
        pr_number=pr_number,
        head_sha=head_sha,
        base_sha=base_sha,
        author=author,
        head_ref=head_ref,
        base_ref=base_ref,
        diff_file_count=diff_file_count,
        diff_hash=diff_hash,
        filtered_file_count=filtered_file_count,
        truncated=truncated,
        platform=platform,
        aws_region=aws_region,
        confidence_threshold=confidence_threshold,
        blocker_threshold=blocker_threshold,
        max_diff_tokens=max_diff_tokens,
        budget_limit_usd=budget_limit_usd,
        draft_behavior=draft_behavior,
        agents=agents,
        verdict=verdict,
        score=score,
        reported_count=reported_count,
        borderline_count=borderline_count,
        hidden_count=hidden_count,
        hallucinations_removed=hallucinations_removed,
        pii_redacted=pii_redacted,
        secrets_redacted=secrets_redacted,
        comment_posted=comment_posted,
        exit_code=exit_code,
        dry_run=dry_run,
    )


def emit_audit_record(record: AuditRecord) -> None:
    """Emit audit record as a single structured log event."""
    logger.info("review.audit", **asdict(record))
