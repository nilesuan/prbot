# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

prbot is a containerised PR/MR review bot that runs as a GitHub Actions or GitLab CI job. It fetches diffs and metadata via VCS adapters, runs 2 parallel AWS Bedrock (Claude) agents — Sonnet for general review, Opus for security — aggregates findings with confidence scoring, and posts a structured review comment.

**Status:** Pre-implementation. Planning artifacts are in `.claude/docs/prbot/secure-review-mvp/`. Source code does not yet exist — follow the epic/story files when building.

## Tech Stack

- **Language:** Python 3.12
- **Package manager:** uv (with `--frozen --verify-hashes` for reproducible installs)
- **Config:** Pydantic v2 frozen models, TOML config files via tomllib
- **HTTP client:** httpx (async) — no CLI tools (gh/glab) at runtime
- **AWS:** boto3 for Bedrock Converse API (structured JSON output via native schema enforcement)
- **Logging:** structlog with JSON renderer to stdout
- **Retry:** tenacity with exponential backoff + jitter
- **Linter:** ruff
- **Tests:** pytest with pytest-asyncio, pytest-cov

## Build & Development Commands

```bash
# Install dependencies
uv sync

# Run linter
ruff check src/ tests/

# Run all tests
pytest tests/ -v

# Run a single test file
pytest tests/vcs/test_github.py -v

# Run tests with coverage
pytest tests/ --cov=prbot --cov-report=term-missing

# Run a specific test class or method
pytest tests/vcs/test_github.py::TestGitHubGetDiff -v
pytest tests/vcs/test_github.py::TestGitHubGetDiff::test_pagination -v

# Type check (if mypy is configured)
mypy src/prbot/
```

## Architecture

```
src/prbot/
├── __init__.py          # Package init, _resolve_api_base_url() with SSRF validation
├── cli.py               # argparse entry point, exit codes: 0=pass, 1=blockers, 2=config, 3=infra
├── config.py            # Pydantic v2 frozen config model with field validators
├── exceptions.py        # Exception hierarchy mapping to exit codes (2=config, 3=infra)
├── auth/                # Token resolution (env var → Secrets Manager fallback), scope validation
├── vcs/                 # VCSAdapter Protocol + GitHubAdapter, GitLabAdapter, diff parser
│   ├── github.py        # httpx-based, Link header pagination, SHA-pinned diffs
│   ├── gitlab.py        # httpx-based, X-Next-Page pagination, URL-encoded project paths
│   └── diff_parser.py   # DiffCoordinate with old_line/new_line/diff_position
├── review/              # Core pipeline
│   ├── models.py        # Finding, AgentResult, AgentError, AgentOutcome, FINDING_JSON_SCHEMA
│   ├── prompts.py       # Load check specs from prompts/ dir, build system/user prompts
│   ├── runner.py        # run_review(): 2-agent asyncio.gather, partial failure, retry
│   ├── budget.py        # TimeoutBudget, cost estimation, model pricing
│   ├── scorer.py        # Confidence bands (hidden/borderline/reported), weighted deduction
│   ├── verdict.py       # 8-scenario deterministic state machine
│   └── formatter.py     # Platform-specific markdown, comment truncation
├── security/            # Defense layers
│   ├── datamarking.py   # Microsoft Spotlighting word-level interleaving
│   ├── validation.py    # Hallucination check: findings against actual diff content
│   ├── diff_filter.py   # Binary/generated file exclusion, PurePosixPath matching
│   └── pii.py           # PII redaction with loopback exemption
└── observability/       # Structured logging, audit trail, data residency
```

## Key Design Patterns

- **VCS Protocol:** `VCSAdapter` is a Python Protocol class. `create_vcs_adapter()` factory selects GitHub or GitLab based on platform config. Both use httpx.AsyncClient internally.
- **Token safety:** `TokenResult` wrapper never exposes values in `repr()`/`str()`. CI masking via `::add-mask::` on stderr. structlog processor redacts token patterns at runtime.
- **Validate-then-fallback (S6):** Present-but-invalid env var token raises immediately — no Secrets Manager fallback attempted.
- **Comment idempotency:** `ReviewStateRecord` embedded as `<!-- prbot:state:... -->` HTML comment enables find → update instead of duplicate posting.
- **Prompt injection defense:** 4 layers — Bedrock system/user message separation, datamarking (Microsoft Spotlighting), hallucination validation against diff, output redaction (secrets + PII).
- **Error classification (G4-15):** All HTTP errors mapped to typed `VCSError` subtypes: `rate_limited`, `auth_failed`, `not_found`, `server_error`, `timeout`, `connect_error`.

## Implementation Guide

Stories are in `.claude/docs/prbot/secure-review-mvp/epics/`. Each story is self-contained with:
- Goal, scope, acceptance criteria (Given/When/Then)
- Implementation steps (behavioral contracts, not pseudocode)
- Definition of Done and verification commands

**Execution order:** Follow `execution-plan.json` wave structure (Wave 1 → 7). Stories within a wave can be parallelized unless they have inter-dependencies listed in their Issue Links section.

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Review passed (APPROVE or COMMENT verdict) |
| 1 | Review found blockers (REQUEST_CHANGES) |
| 2 | Configuration error |
| 3 | Infrastructure error (API failures, timeouts) |
