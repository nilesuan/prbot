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
import dataclasses
import logging
import sys
import uuid
from typing import Any

from prbot import __version__
from prbot.config import PrBotConfig, build_config
from prbot.exceptions import ConfigError, PrBotError

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
        "--force-review",
        action="store_true",
        dest="force_review",
        default=None,
        help="Review again even if this commit was already reviewed",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        # default=None, not the store_true default of False: build_config
        # overlays every CLI value that is not None, so a False here would
        # overwrite PRBOT_DRY_RUN and .prbot.toml on every run (A1).
        default=None,
        help="Run analysis without posting comment",
    )
    return parser.parse_args(argv)


def _summarise_agents(
    agents: list[dict[str, str]],
    outcomes: list[Any],
    budget_limit_usd: float,
) -> tuple[list[Any], float]:
    """Build the per-agent audit rows and total what the run cost.

    With chunking there is one outcome per agent per chunk, and they are
    appended in roster order, so the agent configuration repeats across the
    outcome list. Extracted from run_pipeline (GEN-ARCH-01).

    The cost compared here is what Bedrock actually charged for, not the
    pre-flight character-count estimate (A8). The money is already spent by
    the time this runs, so exceeding the budget is a warning rather than a
    failure; the value of the number is that it can calibrate the estimate.
    """
    from prbot.observability.audit import AgentAuditInfo
    from prbot.review.models import AgentResult

    agent_infos: list[Any] = []
    cycle = [agents[i % len(agents)] for i in range(len(outcomes))]

    for a_cfg, outcome in zip(cycle, outcomes, strict=True):
        if isinstance(outcome, AgentResult):
            agent_infos.append(AgentAuditInfo(
                name=a_cfg["name"],
                model_id=a_cfg["model_id"],
                status="success",
                finding_count=len(outcome.findings),
                input_tokens=outcome.token_usage.input_tokens,
                output_tokens=outcome.token_usage.output_tokens,
                latency_ms=outcome.latency_ms,
                cost_usd=outcome.token_usage.estimated_cost_usd,
            ))
        else:
            agent_infos.append(AgentAuditInfo(
                name=a_cfg["name"],
                model_id=a_cfg["model_id"],
                status=f"error:{type(outcome).__name__}",
                finding_count=0,
                input_tokens=0,
                output_tokens=0,
                latency_ms=0,
            ))

    total_cost = sum(info.cost_usd for info in agent_infos)
    if total_cost > budget_limit_usd:
        logger.warning(
            "cost.over_budget actual=%.4f limit=%.2f",
            total_cost, budget_limit_usd,
        )
    else:
        logger.info(
            "cost.actual usd=%.4f limit=%.2f", total_cost, budget_limit_usd,
        )

    return agent_infos, total_cost


def _finding_audit_entries(findings: Any) -> list[Any]:
    """Every scored finding as a structured audit entry, prose left out."""
    from prbot.observability.audit import FindingAuditInfo
    from prbot.review.identity import finding_fingerprint

    return [
        FindingAuditInfo(
            fingerprint=finding_fingerprint(scored.finding),
            check_id=scored.finding.check_id,
            severity=scored.finding.severity,
            confidence=scored.finding.confidence,
            band=scored.band,
            file_path=scored.finding.file_path,
            line_start=scored.finding.line_start,
            line_end=scored.finding.line_end,
        )
        for scored in findings
    ]


def _apply_safety(
    outcomes: list[Any],
    chunks: list[Any],
    filtered_diff: Any,
    agent_count: int,
    context_lines: int = 0,
) -> tuple[list[Any], int, int]:
    """Validate findings against their own chunk, then redact PII.

    Returns the rewritten outcomes and the two counts the audit record
    carries. Extracted from run_pipeline (GEN-ARCH-01).

    The chunk index matters: an agent only ever saw one chunk's files, so
    validating against the whole diff accepts a finding naming a file that
    agent never read. run_review emits exactly agent_count outcomes per
    chunk in roster order, which is what makes the arithmetic sound.
    """
    from prbot.review.models import AgentResult
    from prbot.security.redaction import redact_finding_pii
    from prbot.security.validation import validate_findings_against_diff

    hallucinations_removed = 0
    pii_redacted_total = 0

    for i, outcome in enumerate(outcomes):
        if not isinstance(outcome, AgentResult):
            continue

        source_chunk = (
            chunks[i // agent_count] if chunks else filtered_diff
        )
        findings = outcome.findings

        if findings:
            pre_count = len(findings)
            findings = validate_findings_against_diff(
                findings, source_chunk, context_lines,
            )
            hallucinations_removed += pre_count - len(findings)

        cleaned = []
        for finding in findings:
            scrubbed, count = redact_finding_pii(finding)
            pii_redacted_total += count
            cleaned.append(scrubbed)

        outcomes[i] = AgentResult(
            agent=outcome.agent,
            findings=cleaned,
            token_usage=outcome.token_usage,
            latency_ms=outcome.latency_ms,
            model_id=outcome.model_id,
        )

    return outcomes, hallucinations_removed, pii_redacted_total


async def _run_preflight(
    config: PrBotConfig,
    adapter: Any,
    metadata: Any,
    authenticated_user: str,
) -> tuple[int | None, tuple[int, str] | None]:
    """Decide whether this pull request should be reviewed at all.

    Returns (exit_code, existing_comment). A non-None exit code means stop;
    the caller returns it. The existing comment is carried out because the
    already-reviewed check has to fetch it and the posting step needs it,
    and looking it up twice paginates the whole comment list twice.

    Extracted from run_pipeline, which had grown to bundle a dozen stages
    in one function (GEN-ARCH-01).
    """
    from prbot.vcs.models import ReviewStateRecord

    # Closed or merged (S81)
    if metadata.state in ("closed", "merged"):
        logger.info(
            "preflight.skip reason=pr_%s pr=#%d",
            metadata.state, metadata.number,
        )
        return EXIT_PASS, None

    # Draft (S34)
    if metadata.is_draft and config.draft_behavior == "skip":
        logger.info("preflight.skip reason=draft pr=#%d", metadata.number)
        return EXIT_PASS, None

    # Bot self-review loop (S71, G4-03)
    if _is_bot_author(
        metadata.author, authenticated_user=authenticated_user,
    ):
        logger.info(
            "preflight.skip reason=bot_author author=%s pr=#%d",
            metadata.author, metadata.number,
        )
        return EXIT_PASS, None

    # Already reviewed at this commit (C3). The state record is advisory,
    # not authenticated: it is read only from a comment posted under this
    # bot's login, so forging it means posting as that login. Under
    # GITHUB_TOKEN every workflow in the repository posts as it.
    existing = await adapter.find_bot_comment()
    previous = (
        ReviewStateRecord.from_html_comment(existing[1]) if existing else None
    )
    if (
        previous is not None
        and previous.head_sha == metadata.head_sha
        and not config.force_review
    ):
        logger.info(
            "review.skip reason=already_reviewed sha=%s "
            "previous_review_id=%s previous_verdict=%s",
            metadata.head_sha, previous.review_id, previous.verdict,
        )
        return (
            EXIT_BLOCKERS
            if previous.verdict == "REQUEST_CHANGES"
            else EXIT_PASS
        ), existing

    return None, existing


async def run_pipeline(config: PrBotConfig) -> int:
    """Run the full review pipeline.

    Returns exit code: 0 for pass, 1 for blockers, 3 for infra.
    """
    import datetime

    from prbot.auth import (
        resolve_token,
        validate_aws_session_credentials,
        validate_token_scopes,
    )
    from prbot.observability.audit import (
        build_audit_record,
        compute_diff_hash,
        emit_audit_record,
    )
    from prbot.observability.logging import (
        bind_review_context,
        clear_review_context,
    )
    from prbot.observability.metrics import emit_metrics
    from prbot.observability.residency import (
        log_data_flow,
        validate_data_residency,
    )
    from prbot.review.budget import TimeoutBudget, estimate_cost
    from prbot.review.chunking import chunk_for_prompt
    from prbot.review.formatter import (
        build_inline_comments,
        format_review_comment,
        format_review_event_body,
        unanchored_findings,
    )
    from prbot.review.models import AgentResult
    from prbot.review.outcomes import reconcile
    from prbot.review.prompts import build_user_prompt
    from prbot.review.runner import run_review
    from prbot.review.scorer import (
        apply_suppressions,
        deduplicate_findings,
        score_findings,
    )
    from prbot.review.verdict import (
        ReviewVerdict,
        determine_verdict,
    )
    from prbot.security.diff_filter import filter_diff
    from prbot.security.redaction import (
        redact_secrets,
    )
    from prbot.vcs import create_vcs_adapter
    from prbot.vcs.models import ReviewStateRecord

    # G-02: Generate review_id as FIRST action
    review_id = str(uuid.uuid4())
    bind_review_context(
        config.repo, config.pr_number, "",
        review_id=review_id,
    )

    # GEN-ERR-03: everything after the bind runs inside the try, so the
    # context is cleared on every exit path. Several fallible steps used to
    # sit between the bind and the try, and a ConfigError or AuthError from
    # any of them left repo, pr_number and review_id bound for the life of
    # the process, which in a long-lived host attributes later log lines to
    # a review that already failed.
    adapter = None
    try:

        # C5: the roster is configuration, not three hardcoded modules
        roster = config.agent_roster()
        agents = [
            {
                "name": spec.name,
                "model_id": spec.model_id or config.general_model_id,
                "check_prefix": spec.check_prefix,
            }
            for spec in roster
        ]

        # Validate data residency before any API calls
        model_ids = [a["model_id"] for a in agents]
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

        # A9: scope validation was written, tested and never called, so a token
        # missing a scope failed later as an opaque 403 rather than a clear
        # configuration error. Token types that cannot report their own scopes
        # skip the check rather than failing it.
        from prbot.vcs import _resolve_api_base_url

        await validate_token_scopes(
            token, config.platform, _resolve_api_base_url(config),
        )

        validate_aws_session_credentials()

        # Create VCS adapter
        adapter = create_vcs_adapter(config, token)

        # Audit tracking variables
        pii_redacted_total = 0
        hallucinations_removed = 0
        secret_count = 0
        exit_code = EXIT_PASS

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

        # Pre-flight short circuits, in the order that costs least first
        # (GEN-ARCH-01). Returns an exit code when the review should not
        # happen, and the existing bot comment when it should.
        authenticated_user = await adapter.get_authenticated_user()
        skip, existing = await _run_preflight(
            config, adapter, metadata, authenticated_user,
        )
        if skip is not None:
            return skip

        # Fetch diff and filter
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

        # C6: a diff over the limit used to raise DiffTooLargeError and end
        # the run, so the largest pull requests got no review at all. It is
        # reviewed in pieces instead, bounded by the same max_diff_tokens
        # that used to refuse it.
        # B8: fetch the surrounding code when asked for it. A file that
        # cannot be fetched simply has no excerpt; expanded context improves
        # the prompt and is never a precondition for reviewing.
        file_contents: dict[str, str] = {}
        if config.context_lines > 0:
            for file_diff in filtered_diff.files:
                if file_diff.status == "removed":
                    continue
                content = await adapter.get_file_content(
                    file_diff.path, metadata.head_sha,
                )
                if content is not None:
                    file_contents[file_diff.path] = content
            logger.info(
                "context.fetched files=%d of=%d lines=%d",
                len(file_contents),
                len(filtered_diff.files),
                config.context_lines,
            )

        # Sized by what each chunk's prompt will be: every file with its
        # excerpt and datamarking, plus the header, description and list of
        # other files each chunk repeats. Sizing the raw patch let a diff
        # estimated at 23k tokens reach the model as 635k.
        all_paths = [f.path for f in filtered_diff.files]
        chunks = chunk_for_prompt(
            filtered_diff, config.max_diff_tokens,
            datamark_diff=config.datamark_diff,
            file_contents=file_contents,
            context_lines=config.context_lines,
            metadata=metadata,
            all_paths=all_paths,
        )
        chunk_texts = [
            build_user_prompt(
                chunk, metadata,
                datamark_diff=config.datamark_diff,
                file_contents=file_contents,
                context_lines=config.context_lines,
                all_paths=all_paths,
            )
            for chunk in chunks
        ]
        diff_text = "\n\n".join(chunk_texts)

        # The budget bounds the whole run, not each chunk. Checking chunks
        # individually would let ten chunks each under the limit cost ten
        # times it.
        estimate_cost(
            diff_text,
            [a["model_id"] for a in agents],
            config.budget_limit_usd,
            estimated_output_tokens=(
                config.max_output_tokens * max(len(chunks), 1)
            ),
        )
        if len(chunks) > 1:
            logger.info(
                "review.chunked chunks=%d files=%d",
                len(chunks), len(filtered_diff.files),
            )

        budget = TimeoutBudget(config.timeout_seconds)
        outcomes = []
        for chunk in chunks:
            outcomes.extend(
                await run_review(
                    chunk, metadata, agents,
                    budget, config.aws_region,
                    max_output_tokens=config.max_output_tokens,
                    datamark_diff=config.datamark_diff,
                    file_contents=file_contents,
                    context_lines=config.context_lines,
                    all_paths=all_paths,
                ),
            )

        # Hallucination validation
        # Post-model safety layers, in order: each finding is checked
        # against the chunk that produced it, then PII is removed from every
        # prose field (GEN-ARCH-01).
        outcomes, hallucinations_removed, pii_redacted_total = _apply_safety(
            outcomes, chunks, filtered_diff, len(agents),
            config.context_lines,
        )

        # C4: apply suppressions after deduplication so one rule silences a
        # defect both agents reported, and before scoring so a suppressed
        # finding does not deduct.
        # What the agents returned, before anything downstream removes a
        # finding. hallucinations_removed has already been taken out of
        # outcomes by _apply_safety, so it is added back to get the total the
        # comment has to account for (D5).
        produced_count = sum(
            len(o.findings) for o in outcomes if isinstance(o, AgentResult)
        ) + hallucinations_removed

        merged = deduplicate_findings(outcomes)
        merged_count = produced_count - hallucinations_removed - len(merged)
        kept, suppressed = apply_suppressions(merged, config.suppress)
        suppressed_count = len(suppressed)
        if suppressed_count:
            logger.info("findings.suppressed count=%d", suppressed_count)

        # Score findings and determine verdict
        reported, borderline, hidden_count, score = (
            score_findings(
                [AgentResult(agent="merged", findings=kept)],
                threshold=config.confidence_threshold,
                blocker_threshold=config.blocker_threshold,
            )
        )
        # The real de-duplication happens above, on every agent's findings,
        # not inside score_findings, which is handed one already-merged
        # result. The count therefore has to be put back on the score here.
        score = dataclasses.replace(score, merged_count=merged_count)
        verdict = determine_verdict(
            outcomes, reported, score,
            blocker_confidence=config.blocker_threshold,
            min_passing_score=config.min_passing_score,
        )
        logger.info(
            "verdict=%s score=%d findings=%d hidden=%d",
            verdict.value,
            score.clamped_score,
            score.finding_count,
            hidden_count,
        )

        # C8: each finding is one comment thread, so the pull request is
        # the store. Reconciling against the threads a previous run left
        # does three things at once: it stops a finding being posted twice,
        # it lets a finding that has gone away be closed out where the
        # author is looking, and it turns "was this finding acted on" into
        # something measurable.
        inline: list = []
        unanchored: list = []
        outcome_counts: dict[str, int] = {}
        outcomes_report = None

        if config.review_mode == "review":
            threads = await adapter.list_review_threads()
            # SEC-AUTH-02: the finding marker is not identity, so only
            # threads this token actually wrote are reconciled.
            # D7: anchor everything worth showing, not only what cleared
            # the reporting threshold. Inline comments used to be built from
            # `reported` alone, and with the threshold at its default almost
            # nothing reaches that band: across 19 audited production reviews
            # one finding did, so prbot had never posted an inline comment at
            # all while both repositories had review mode on and their merges
            # gated on unresolved discussions. A borderline finding is shown
            # in the summary already; giving it a thread puts it on the line
            # it is about and lets it be resolved or fixed like any other.
            anchorable = [*reported, *borderline]
            outcomes_report = reconcile(
                anchorable, threads, bot_user=authenticated_user,
            )
            outcome_counts = outcomes_report.counts()
            inline = build_inline_comments(
                outcomes_report.new, filtered_diff,
            )
            # A finding that already has a thread is detailed in it, and one
            # that just got an inline comment is detailed there. What is left
            # is the findings the diff cannot anchor: the summary is the only
            # place their description can go, so the summary is given them.
            # Only reported findings earn full detail in the summary. A
            # borderline one that cannot be anchored is already listed in the
            # collapsed section, and repeating it in full would say the same
            # thing twice at two different prominences.
            unanchored = [
                sf for sf in unanchored_findings(
                    outcomes_report.new, filtered_diff,
                )
                if sf.band == "reported"
            ]
            logger.info(
                "review.inline new=%d persisting=%d fixed=%d resolved=%d "
                "reopened=%d anchored=%d unanchored=%d",
                len(outcomes_report.new),
                len(outcomes_report.persisting),
                len(outcomes_report.fixed),
                len(outcomes_report.human_resolved),
                len(outcomes_report.reopened),
                len(inline),
                len(unanchored),
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
        # The comment is rewritten in place, so without carrying the
        # previous record forward this run silently deletes what prbot said
        # about the last commit. That is how two APPROVE verdicts at 100/100
        # on MR 194 became invisible.
        previous_state = (
            ReviewStateRecord.from_html_comment(existing[1])
            if existing else None
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
        state_record = state_record.superseding(previous_state)
        state_html = state_record.to_html_comment()

        # Format comment, redact secrets
        comment = format_review_comment(
            verdict, score, reported, borderline,
            hidden_count, outcomes, state_html,
            config.platform,
            suppressed_count=suppressed_count,
            fixed_count=outcome_counts.get("findings_fixed", 0),
            unanchored=unanchored,
            inline_enabled=config.review_mode == "review",
            produced_count=produced_count,
            dropped_count=hallucinations_removed,
            history=state_record.history,
            hidden=list(score.hidden),
        )
        comment, secret_count = redact_secrets(comment)

        # SEC-CRED-02: the inline bodies are built from description,
        # failure_scenario and suggestion, which is exactly where a
        # credential the model echoed back out of the diff would sit. Every
        # body that is about to be posted goes through the same scrubber as
        # the summary, and its hits are counted the same way.
        if inline:
            from dataclasses import replace as _replace

            scrubbed = []
            for item in inline:
                body, hits = redact_secrets(item.body)
                secret_count += hits
                scrubbed.append(_replace(item, body=body))
            inline = scrubbed

        if secret_count > 0:
            logger.warning(
                "Redacted %d secret(s) from review comment",
                secret_count,
            )

        # Post or update comment
        comment_posted = False
        if not config.dry_run:
            if config.review_mode == "review":
                # The summary is not the review body. A review cannot be
                # found or rewritten by a later run, so putting the summary
                # in one would repost it on every push and leave the state
                # record somewhere find_bot_comment does not look, which is
                # what the C3 unchanged-commit skip reads.
                await adapter.submit_review(
                    format_review_event_body(
                        verdict, score, reported, len(inline),
                    ),
                    verdict.value,
                    inline,
                    head_sha=metadata.head_sha,
                    base_sha=metadata.base_sha,
                )
                logger.info(
                    "review.submitted event=%s inline=%d",
                    verdict.value, len(inline),
                )

                # A finding that is no longer reported against newer code has
                # been dealt with. Say so in its own thread and close it,
                # rather than leaving the author to work out which of last
                # week's comments still apply.
                if outcomes_report is not None:
                    unresolved = 0
                    for thread in outcomes_report.fixed:
                        try:
                            await adapter.reply_to_thread(
                                thread,
                                "No longer reported as of "
                                f"`{metadata.head_sha[:8]}`. Resolving.",
                            )
                            # GEN-ERR-02: resolve_thread reports rather than
                            # raises precisely so the caller can tell. A
                            # silently unresolved thread stays open for ever
                            # and the next run re-replies to it.
                            if not await adapter.resolve_thread(thread):
                                unresolved += 1
                                logger.warning(
                                    "Thread %s was replied to but could not "
                                    "be resolved; it will stay open",
                                    thread.id,
                                )
                        except PrBotError as e:
                            # Closing out a finding is a courtesy, not part
                            # of delivering the review.
                            unresolved += 1
                            logger.warning(
                                "Could not close thread %s: %s", thread.id, e,
                            )
                    if unresolved:
                        logger.warning(
                            "review.threads_unresolved count=%d of=%d",
                            unresolved, len(outcomes_report.fixed),
                        )

                    # A thread prbot closed because one run missed the
                    # finding. The finding is back, so the closure was a
                    # guess, and leaving it closed would discard a real
                    # defect on the strength of one unlucky run.
                    for _, thread in outcomes_report.reopened:
                        try:
                            if await adapter.unresolve_thread(thread):
                                await adapter.reply_to_thread(
                                    thread,
                                    "Reported again as of "
                                    f"`{metadata.head_sha[:8]}`. Reopening.",
                                )
                            else:
                                logger.warning(
                                    "Thread %s could not be reopened",
                                    thread.id,
                                )
                        except PrBotError as e:
                            logger.warning(
                                "Could not reopen thread %s: %s",
                                thread.id, e,
                            )

            # One summary comment, found and rewritten in place, in both
            # modes. It carries the state record, so this is also what makes
            # the next run able to recognise its own work.
            if existing:
                cid = existing[0]
                await adapter.update_comment(cid, comment)
            else:
                cid = await adapter.post_comment(comment)
            logger.info("comment.posted id=%d", cid)
            comment_posted = True
        else:
            logger.info(
                "dry-run: nothing posted (%d inline comment(s) withheld)",
                len(inline),
            )
            print(comment)

        # Exit code mapping (G-28)
        has_results = any(
            isinstance(o, AgentResult) for o in outcomes
        )
        # SEC-DESIGN-02: a pass no agent completed was reviewed by nobody, so
        # the job fails as it does when every agent fails, unless a blocker
        # found elsewhere already fails it. run_review emits one outcome per
        # agent per chunk, in chunk order.
        unreviewed = sum(
            1 for k in range(len(chunks))
            if not any(
                isinstance(o, AgentResult)
                for o in outcomes[k * len(agents):(k + 1) * len(agents)]
            )
        )
        if not has_results:
            exit_code = EXIT_INFRA_ERROR
        elif verdict == ReviewVerdict.REQUEST_CHANGES:
            exit_code = EXIT_BLOCKERS
        elif unreviewed:
            logger.warning(
                "%d of %d review passes were completed by no agent",
                unreviewed, len(chunks),
            )
            exit_code = EXIT_INFRA_ERROR
        else:
            exit_code = EXIT_PASS

        # Per-agent audit rows and what the run actually cost
        # (GEN-ARCH-01).
        agent_infos, total_cost = _summarise_agents(
            agents, outcomes, config.budget_limit_usd,
        )

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
            suppressed_count=suppressed_count,
            pii_redacted=pii_redacted_total,
            secrets_redacted=secret_count,
            comment_posted=comment_posted,
            exit_code=exit_code,
            dry_run=config.dry_run,
            cost_usd=total_cost,
            outcome_counts=outcome_counts,
            findings=_finding_audit_entries(
                (*reported, *borderline, *score.hidden),
            ),
        )
        emit_audit_record(audit)
        emit_metrics(
            audit,
            namespace=config.metrics_namespace,
            metrics_file=config.metrics_file,
            region=config.aws_region,
        )

        return exit_code

    finally:
        if adapter is not None:
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
    except PrBotError as e:
        # exceptions.py declares exit_code per class and is the single source
        # of truth. Hand-written branches per exception type were a second
        # copy of that mapping, able to drift from it (GEN-MAINT-02).
        print(f"error: {e}", file=sys.stderr)
        sys.exit(e.exit_code)
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        sys.exit(EXIT_INFRA_ERROR)
    except Exception:
        # D4: an uncaught exception exits 1, which every workflow reads as
        # REQUEST_CHANGES. A crash is an infrastructure failure, not a
        # review outcome, so it must be distinguishable from one.
        logger.exception("pipeline.crashed")
        sys.exit(EXIT_INFRA_ERROR)

    sys.exit(exit_code)
