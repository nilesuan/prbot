# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

prbot is a containerised PR/MR review bot that runs as a GitHub Actions or GitLab CI job. It fetches diffs and metadata via VCS adapters, runs 2 parallel AWS Bedrock (Claude) agents -- Sonnet for general review, Opus for security -- aggregates findings with confidence scoring, and posts a structured review comment.

The container image is published to `ghcr.io/nilesuan/prbot:latest` and signed with Cosign.

## Tech Stack

- **Language:** Python 3.12
- **Package manager:** uv (with `--frozen` for reproducible installs)
- **Config:** Pydantic v2 frozen models, TOML config files via tomllib
- **HTTP client:** httpx (async) -- no CLI tools (gh/glab) at runtime
- **AWS:** boto3 for Bedrock Converse API (structured JSON output via native schema enforcement)
- **Logging:** structlog with JSON renderer to stdout
- **Retry:** tenacity with exponential backoff + jitter
- **Linter:** ruff
- **Tests:** pytest with pytest-asyncio, pytest-cov
- **Container:** Multi-stage Dockerfile, python:3.12-slim base (digest-pinned), non-root user

## Build & Development Commands

```bash
# Install dependencies
uv sync

# Run linter
ruff check src/ tests/

# Run all tests
pytest tests/ -v

# Run a single test file
pytest tests/test_preflight.py -v

# Run tests with coverage
pytest tests/ --cov=prbot --cov-report=term-missing

# Run a specific test class or method
pytest tests/test_cli.py::TestParseArgs -v

# Build container locally
docker build -t prbot:local .

# Run container tests (requires Docker)
PRBOT_RUN_DOCKER=1 pytest tests/test_container.py -v
```

## Architecture

```
src/prbot/
├── __init__.py          # Package version
├── __main__.py          # python -m prbot support
├── cli.py               # argparse entry point, pre-flight checks, pipeline orchestration
│                        # Exit codes: 0=pass, 1=blockers, 2=config, 3=infra
├── config.py            # Pydantic v2 frozen config, TOML loading, SSRF URL validation
├── exceptions.py        # Exception hierarchy mapping to exit codes
├── auth/
│   ├── token.py         # TokenResult wrapper (never exposes value), resolve_token()
│   ├── credentials.py   # AWS session credential validation (OIDC vs long-lived)
│   └── scope.py         # VCS token scope validation
├── vcs/
│   ├── __init__.py      # create_vcs_adapter() factory, API URL resolution with SSRF check
│   ├── protocol.py      # VCSAdapter Protocol class
│   ├── github.py        # httpx-based, Link header pagination, Bearer auth
│   ├── gitlab.py        # httpx-based, X-Next-Page pagination, PRIVATE-TOKEN auth
│   ├── models.py        # PRMetadata, PRDiff, FileDiff, ReviewStateRecord (HMAC-SHA256)
│   └── diff_parser.py   # DiffCoordinate with old_line/new_line/diff_position
├── review/
│   ├── models.py        # Finding, AgentResult, AgentError, TokenUsage, FINDING_JSON_SCHEMA
│   ├── prompts.py       # Load check specs from prompts/ dir, build system/user prompts
│   ├── runner.py        # run_review(): 2-agent asyncio.gather, retry, partial failure
│   ├── budget.py        # TimeoutBudget, cost estimation, model pricing
│   ├── scorer.py        # Confidence bands (hidden/borderline/reported), weighted deduction
│   ├── verdict.py       # 8-scenario deterministic state machine
│   └── formatter.py     # Platform-specific markdown, progressive comment truncation
├── security/
│   ├── datamarking.py   # Microsoft Spotlighting word-level interleaving
│   ├── validation.py    # Hallucination check: findings against actual diff
│   ├── diff_filter.py   # Binary/generated file exclusion, glob pattern matching
│   ├── redaction.py     # Secret pattern redaction in output
│   └── datamarking.py   # PII redaction with loopback exemption
└── observability/
    ├── logging.py       # structlog JSON config, token redaction processor
    ├── audit.py         # AuditRecord frozen dataclass, diff hashing
    └── residency.py     # AWS region + model geographic routing validation
```

## Key Design Patterns

- **VCS Protocol:** `VCSAdapter` is a Python Protocol class. `create_vcs_adapter()` factory selects GitHub or GitLab based on platform config. Both use httpx.AsyncClient internally.
- **Token safety:** `TokenResult` wrapper never exposes values in `repr()`/`str()`. CI masking via `::add-mask::` on stderr. structlog processor redacts token patterns at runtime.
- **Validate-then-fallback (S6):** Present-but-invalid env var token raises immediately -- no Secrets Manager fallback attempted.
- **Comment idempotency:** `ReviewStateRecord` embedded as `<!-- prbot:state:... -->` HTML comment enables find -> update instead of duplicate posting.
- **Prompt injection defense:** 4 layers -- Bedrock system/user message separation, datamarking (Microsoft Spotlighting), hallucination validation against diff, output redaction (secrets + PII).
- **Error classification (G4-15):** All HTTP errors mapped to typed `VCSError` subtypes: `rate_limited`, `auth_failed`, `not_found`, `server_error`, `timeout`, `connect_error`.
- **Pre-flight checks:** Pipeline skips closed/merged PRs, drafts, bot authors, and empty diffs before making any Bedrock API calls.
- **Lazy imports in cli.py:** `run_pipeline()` imports heavy modules (boto3, review, observability) inside the function body. Tests must patch at the source module level (e.g., `prbot.auth.resolve_token`, not `prbot.cli.resolve_token`).

## Review Checks

Check specs are in `prompts/general.md` (Q-* checks) and `prompts/security.md` (S-* checks). The general agent covers: architecture (Q-ARCH), maintainability (Q-MAINT), testing (Q-TEST), error handling (Q-ERR), API contracts (Q-API), and completeness (Q-COMP -- docs, version bump, changelog, examples). The security agent covers: credentials (S-CRED), input validation (S-INPUT), auth (S-AUTH), cryptography (S-CRYPTO), and data safety (S-DATA).

## CI/CD

- **`.github/workflows/build.yml`** -- Docker build, Trivy scan, Cosign signing, push to GHCR
- **`.github/workflows/prbot.yml`** -- Self-review on same-repo PRs
- **`.github/workflows/prbot-fork.yml`** -- Fork PR review with pull_request_target and author gating
- **`.gitlab/ci/prbot.yml`** -- GitLab CI template with OIDC support

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Review passed (APPROVE or COMMENT verdict) |
| 1 | Review found blockers (REQUEST_CHANGES) |
| 2 | Configuration error |
| 3 | Infrastructure error (API failures, timeouts) |

## Planning Artifacts

Story and epic planning artifacts are in `.claude/docs/prbot/secure-review-mvp/`. Follow `execution-plan.json` wave structure for build order.
