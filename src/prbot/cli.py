"""CLI entry point for prbot.

Stories: 1-3, 2-3, 5-6, 6-6, 7-3, 8-4.

Exit codes:
    0 — Review passed (APPROVE or COMMENT verdict)
    1 — Review found blockers (REQUEST_CHANGES)
    2 — Configuration error
    3 — Infrastructure error (API failures, timeouts)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid

from prbot import __version__
from prbot.config import PrBotConfig, build_config
from prbot.exceptions import (
    AuthError,
    ConfigError,
    InsufficientScopesError,
    PrBotError,
)

EXIT_PASS = 0
EXIT_BLOCKERS = 1
EXIT_CONFIG_ERROR = 2
EXIT_INFRA_ERROR = 3

logger = logging.getLogger(__name__)

# Known bot author patterns (S71)
_BOT_AUTHOR_PATTERNS = frozenset({
    "dependabot[bot]",
    "renovate[bot]",
    "github-actions[bot]",
    "gitlab-bot",
})


def _is_bot_author(
    author: str, *, authenticated_user: str = "",
) -> bool:
    """Check if PR author is a bot (S71, G4-03)."""
    lower = author.lower()

    if lower in _BOT_AUTHOR_PATTERNS:
        return True
    if lower.endswith("[bot]"):
        return True
    if lower.startswith("bot-"):
        return True
    return bool(
        authenticated_user and lower == authenticated_user.lower(),
    )


def parse_args(
    argv: list[str] | None = None,
) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="prbot",
        description="Security-hardened PR/MR review bot",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"prbot {__version__}",
    )
    parser.add_argument(
        "--platform",
        choices=["github", "gitlab"],
        help="VCS platform (auto-detected from CI environment "
        "if omitted)",
    )
    parser.add_argument(
        "--pr",
        type=int,
        dest="pr_number",
        help="PR/MR number to review",
    )
    parser.add_argument(
        "--repo",
        help="Repository in owner/repo format",
    )
    parser.add_argument(
        "--config",
        help="Path to .prbot.toml config file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Run analysis without posting comment",
    )
    return parser.parse_args(argv)


async def run_pipeline(config: PrBotConfig) -> int:
    """Run the full review pipeline.

    Returns exit code: 0 for pass, 1 for blockers, 3 for infra.
    """
    import datetime

    from prbot.auth import (
        resolve_token,
        validate_aws_session_credentials,
    )
    from prbot.observability.audit import (
        AgentAuditInfo,
        build_audit_record,
        compute_diff_hash,
        emit_audit_record,
    )
    from prbot.observability.logging import (
        bind_review_context,
        clear_review_context,
    )
    from prbot.observability.residency import (
        log_data_flow,
        validate_data_residency,
    )
    from prbot.review.budget import (
        TimeoutBudget,
        estimate_cost,
        validate_diff_size,
    )
    from prbot.review.formatter import format_review_comment
    from prbot.review.models import AgentResult
    from prbot.review.prompts import build_user_prompt
    from prbot.review.runner import run_review
    from prbot.review.scorer import score_findings
    from prbot.review.verdict import (
        ReviewVerdict,
        determine_verdict,
    )
    from prbot.security.diff_filter import filter_diff
    from prbot.security.redaction import redact_pii, redact_secrets
    from prbot.security.validation import (
        validate_findings_against_diff,
    )
    from prbot.vcs import create_vcs_adapter
    from prbot.vcs.models import ReviewStateRecord

    # G-02: Generate review_id as FIRST action
    review_id = str(uuid.uuid4())
    bind_review_context(
        config.repo, config.pr_number, "",
        review_id=review_id,
    )

    # Validate data residency before any API calls
    model_ids = [config.general_model_id, config.security_model_id]
    validate_data_residency(
        config.aws_region, config.allowed_regions, model_ids,
    )

    # Resolve VCS token
    token = await resolve_token(config)
    token.mask_in_ci()
    logger.info(
        "auth.resolved source=%s token=%s",
        token.source, token.redacted,
    )
    validate_aws_session_credentials()

    # Create VCS adapter
    adapter = create_vcs_adapter(config, token)

    # Audit tracking variables
    pii_redacted_total = 0
    hallucinations_removed = 0
    secret_count = 0
    exit_code = EXIT_PASS

    try:
        # Fetch PR metadata
        metadata = await adapter.get_pr_metadata()

        # Update context with real commit SHA
        bind_review_context(
            config.repo, metadata.number,
            metadata.head_sha, review_id=review_id,
        )

        # Log data flow path
        log_data_flow(
            config.platform, config.aws_region, config.platform,
        )

        # Pre-flight: Skip closed/merged PRs (S81)
        if metadata.state in ("closed", "merged"):
            logger.info(
                "preflight.skip reason=pr_%s pr=#%d",
                metadata.state, metadata.number,
            )
            return EXIT_PASS

        # Pre-flight: Handle draft PRs (S34)
        if (
            metadata.is_draft
            and config.draft_behavior == "skip"
        ):
            logger.info(
                "preflight.skip reason=draft pr=#%d",
                metadata.number,
            )
            return EXIT_PASS

        # Pre-flight: Bot self-review loop (S71, G4-03)
        authenticated_user = await adapter.get_authenticated_user()
        if _is_bot_author(
            metadata.author,
            authenticated_user=authenticated_user,
        ):
            logger.info(
                "preflight.skip reason=bot_author author=%s "
                "pr=#%d",
                metadata.author, metadata.number,
            )
            return EXIT_PASS

        # Fetch diff and filter
        raw_diff = await adapter.get_diff()
        filtered_diff = filter_diff(
            raw_diff,
            exclusion_patterns=config.excluded_patterns,
        )
        logger.info(
            "diff.filtered files=%d→%d",
            len(raw_diff.files),
            len(filtered_diff.files),
        )

        # Pre-flight: Empty diff short-circuit (S32)
        if not filtered_diff.files:
            logger.info(
                "preflight.skip reason=empty_diff pr=#%d",
                metadata.number,
            )
            return EXIT_PASS

        # Validate diff size and estimate cost
        diff_text = build_user_prompt(filtered_diff, metadata)
        validate_diff_size(diff_text, config.max_diff_tokens)
        agents = [
            {
                "name": "general",
                "model_id": config.general_model_id,
            },
            {
                "name": "security",
                "model_id": config.security_model_id,
            },
        ]
        estimate_cost(
            diff_text,
            [a["model_id"] for a in agents],
            config.budget_limit_usd,
        )

        # Run 2-agent review
        budget = TimeoutBudget(config.timeout_seconds)
        outcomes = await run_review(
            filtered_diff, metadata, agents,
            budget, config.aws_region,
        )

        # Hallucination validation
        for i, outcome in enumerate(outcomes):
            if (
                isinstance(outcome, AgentResult)
                and outcome.findings
            ):
                pre_count = len(outcome.findings)
                validated = validate_findings_against_diff(
                    outcome.findings, filtered_diff,
                )
                hallucinations_removed += (
                    pre_count - len(validated)
                )
                outcomes[i] = AgentResult(
                    agent=outcome.agent,
                    findings=validated,
                    token_usage=outcome.token_usage,
                    latency_ms=outcome.latency_ms,
                    model_id=outcome.model_id,
                )

        # PII redaction
        for i, outcome in enumerate(outcomes):
            if isinstance(outcome, AgentResult):
                from dataclasses import replace

                redacted_findings = []
                for finding in outcome.findings:
                    desc, count = redact_pii(finding.description)
                    pii_redacted_total += count
                    redacted_findings.append(
                        replace(finding, description=desc),
                    )
                outcomes[i] = AgentResult(
                    agent=outcome.agent,
                    findings=redacted_findings,
                    token_usage=outcome.token_usage,
                    latency_ms=outcome.latency_ms,
                    model_id=outcome.model_id,
                )

        # Score findings and determine verdict
        reported, borderline, hidden_count, score = (
            score_findings(
                outcomes,
                threshold=config.confidence_threshold,
                blocker_threshold=config.blocker_threshold,
            )
        )
        verdict = determine_verdict(
            outcomes, reported, score,
            config.blocker_threshold,
        )
        logger.info(
            "verdict=%s score=%d findings=%d hidden=%d",
            verdict.value,
            score.clamped_score,
            score.finding_count,
            hidden_count,
        )

        # Build ReviewStateRecord with HMAC-SHA256 (G4-09)
        findings_dicts = [
            {
                "id": sf.finding.id,
                "check_id": sf.finding.check_id,
                "severity": sf.finding.severity,
                "confidence": sf.finding.confidence,
            }
            for sf in reported
        ]
        findings_hash = ReviewStateRecord.compute_findings_hash(
            findings_dicts, review_id,
        )
        state_record = ReviewStateRecord(
            review_id=review_id,
            head_sha=metadata.head_sha,
            score=score.clamped_score,
            verdict=verdict.value,
            findings_hash=findings_hash,
            timestamp=datetime.datetime.now(
                datetime.UTC,
            ).isoformat(),
        )
        state_html = state_record.to_html_comment()

        # Format comment, redact secrets
        comment = format_review_comment(
            verdict, score, reported, borderline,
            hidden_count, outcomes, state_html,
            config.platform,
        )
        comment, secret_count = redact_secrets(comment)
        if secret_count > 0:
            logger.warning(
                "Redacted %d secret(s) from review comment",
                secret_count,
            )

        # Post or update comment
        comment_posted = False
        if not config.dry_run:
            cid = await adapter.post_or_update_comment(comment)
            logger.info("comment.posted id=%d", cid)
            comment_posted = True
        else:
            logger.info("dry-run: comment not posted")
            print(comment)

        # Exit code mapping (G-28)
        has_results = any(
            isinstance(o, AgentResult) for o in outcomes
        )
        if not has_results:
            exit_code = EXIT_INFRA_ERROR
        elif verdict == ReviewVerdict.REQUEST_CHANGES:
            exit_code = EXIT_BLOCKERS
        else:
            exit_code = EXIT_PASS

        # Emit audit record
        agent_infos = []
        for a_cfg, outcome in zip(agents, outcomes, strict=True):
            if isinstance(outcome, AgentResult):
                agent_infos.append(AgentAuditInfo(
                    name=a_cfg["name"],
                    model_id=a_cfg["model_id"],
                    status="success",
                    finding_count=len(outcome.findings),
                    input_tokens=outcome.token_usage.get(
                        "inputTokens", 0,
                    ),
                    output_tokens=outcome.token_usage.get(
                        "outputTokens", 0,
                    ),
                    latency_ms=outcome.latency_ms,
                ))
            else:
                err_type = type(outcome).__name__
                agent_infos.append(AgentAuditInfo(
                    name=a_cfg["name"],
                    model_id=a_cfg["model_id"],
                    status=f"error:{err_type}",
                    finding_count=0,
                    input_tokens=0,
                    output_tokens=0,
                    latency_ms=0,
                ))

        audit = build_audit_record(
            review_id=review_id,
            repo=config.repo,
            pr_number=metadata.number,
            head_sha=metadata.head_sha,
            base_sha=metadata.base_sha,
            author=metadata.author,
            head_ref=metadata.head_ref,
            base_ref=metadata.base_ref,
            diff_file_count=len(raw_diff.files),
            diff_hash=compute_diff_hash(diff_text),
            filtered_file_count=len(filtered_diff.files),
            truncated=filtered_diff.truncated,
            platform=config.platform,
            aws_region=config.aws_region,
            confidence_threshold=config.confidence_threshold,
            blocker_threshold=config.blocker_threshold,
            max_diff_tokens=config.max_diff_tokens,
            budget_limit_usd=config.budget_limit_usd,
            draft_behavior=config.draft_behavior,
            agents=agent_infos,
            verdict=verdict.value,
            score=score.clamped_score,
            reported_count=len(reported),
            borderline_count=len(borderline),
            hidden_count=hidden_count,
            hallucinations_removed=hallucinations_removed,
            pii_redacted=pii_redacted_total,
            secrets_redacted=secret_count,
            comment_posted=comment_posted,
            exit_code=exit_code,
            dry_run=config.dry_run,
        )
        emit_audit_record(audit)

        return exit_code

    finally:
        await adapter.close()
        clear_review_context()


def main(argv: list[str] | None = None) -> None:
    """Entry point for `python -m prbot`."""
    from prbot.observability.logging import configure_logging

    args = parse_args(argv)

    try:
        config = build_config(cli_args=vars(args))
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(EXIT_CONFIG_ERROR)

    # Configure structured logging before any log statements
    configure_logging(config.log_level)

    try:
        exit_code = asyncio.run(run_pipeline(config))
    except InsufficientScopesError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(EXIT_CONFIG_ERROR)
    except AuthError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(EXIT_INFRA_ERROR)
    except PrBotError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(e.exit_code)

    sys.exit(exit_code)
