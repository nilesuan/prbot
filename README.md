# prbot

Security-hardened, AI-powered PR/MR review bot. Runs as a container in GitHub Actions or GitLab CI, analyzes diffs with two parallel Claude agents (general + security) via Amazon Bedrock, and posts structured review comments with confidence-scored findings.

## Why prbot?

### Dual-Agent Architecture

Every PR gets reviewed by two independent AI agents running concurrently:

- **General agent** (Claude Sonnet) -- architecture, maintainability, testing, error handling, API contracts, and completeness
- **Security agent** (Claude Opus) -- credentials, injection, auth, cryptography, and data safety

Two models catch what one misses. If one agent fails, the other still produces findings -- partial failure never blocks the review.

### Confidence-Based Scoring

Not all findings are equal. prbot uses confidence bands to reduce noise:

- **Reported** -- high confidence findings shown directly in the review
- **Borderline** -- medium confidence findings collapsed in a details section
- **Hidden** -- low confidence findings counted but not shown

Each finding has a severity weight and confidence score. The combination produces a 0-100 review score and a deterministic verdict (APPROVE, COMMENT, or REQUEST_CHANGES).

### Security by Default

- **4-layer prompt injection defense** -- system/user message separation, Microsoft Spotlighting datamarking, hallucination validation against actual diff content, output redaction
- **Token safety** -- tokens never appear in `repr()`/`str()`, structlog processor redacts patterns at runtime, CI masking via `::add-mask::`
- **SSRF protection** -- all URLs validated against private/loopback/metadata addresses
- **Signed container image** -- Cosign keyless signing with Sigstore for supply chain verification
- **Vulnerability scanning** -- Trivy scans every build for CRITICAL/HIGH CVEs
- **OIDC authentication** -- short-lived AWS credentials via GitHub/GitLab OIDC federation (no static keys)
- **Path traversal rejection** -- findings with `..` or absolute paths in `file_path` are dropped
- **PII redaction** -- personal data patterns removed from review comments before posting
- **Secret redaction** -- GitHub PATs, GitLab PATs, AWS keys, Bearer tokens caught and replaced

### Completeness Checks

prbot doesn't just review code quality -- it checks that PRs are complete:

- Missing documentation for behavior changes
- Missing version bumps for features and breaking changes
- Missing changelog entries for non-trivial changes
- Outdated examples when public APIs change

### Smart Pre-flight Skipping

Reviews are skipped automatically when they'd be wasted:

- Closed or merged PRs
- Draft PRs (configurable)
- Bot authors (Dependabot, Renovate, GitHub Actions, self-review loop prevention)
- Empty diffs after exclusion patterns are applied

### Idempotent Comments

prbot finds and updates its previous comment instead of posting duplicates. State is tracked via an embedded `<!-- prbot:state:... -->` HTML comment with HMAC-SHA256 integrity.

### Full Observability

- Structured JSON logging via structlog with token redaction
- Review correlation IDs (UUID4) across all log lines
- Audit trail with frozen dataclass records (no sensitive data)
- Data residency validation (region + model geographic routing checks)

## Review Checks

### General Agent (Q-*)

| ID | Check | What it catches |
|----|-------|-----------------|
| **Q-ARCH-01** | Separation of concerns | Modules/classes with multiple responsibilities |
| **Q-ARCH-02** | Dependency direction | Circular imports, outward-facing layer dependencies |
| **Q-ARCH-03** | Interface boundaries | Overly broad public APIs |
| **Q-ARCH-04** | Configuration coupling | Hardcoded values that should be configurable |
| **Q-MAINT-01** | Naming clarity | Unclear variable, function, and class names |
| **Q-MAINT-02** | Code duplication | Copy-paste patterns that should be extracted |
| **Q-MAINT-03** | Complexity | Overly long functions, high cyclomatic complexity |
| **Q-MAINT-04** | Dead code | Unused imports, variables, unreachable branches |
| **Q-TEST-01** | Test coverage | New code paths without corresponding tests |
| **Q-TEST-02** | Edge cases | Missing boundary condition and error path tests |
| **Q-TEST-03** | Test isolation | Tests that depend on external state or ordering |
| **Q-TEST-04** | Assertion quality | Tests asserting implementation details instead of behavior |
| **Q-ERR-01** | Exception specificity | Bare `except` or overly broad exception catching |
| **Q-ERR-02** | Error propagation | Swallowed errors, missing context in error messages |
| **Q-ERR-03** | Resource cleanup | Unclosed files, connections, or locks |
| **Q-ERR-04** | Failure modes | Missing graceful degradation |
| **Q-API-01** | Input validation | Missing parameter validation at boundaries |
| **Q-API-02** | Return types | Inconsistent return types, implicit None |
| **Q-API-03** | Breaking changes | Public API changes without backward compatibility or documentation |
| **Q-API-04** | Documentation | Public APIs without docstrings |
| **Q-COMP-01** | Documentation completeness | Behavior/API changes without documentation updates |
| **Q-COMP-02** | Version bump | Features or breaking changes without version update |
| **Q-COMP-03** | Changelog | Non-trivial changes without changelog entry |
| **Q-COMP-04** | Examples | API/CLI/config changes with outdated examples |

### Security Agent (S-*)

| ID | Check | What it catches |
|----|-------|-----------------|
| **S-CRED-01** | Hardcoded secrets | API keys, tokens, passwords in source code |
| **S-CRED-02** | Secret logging | Sensitive values written to logs or error messages |
| **S-CRED-03** | Credential storage | Secrets in plaintext files or databases |
| **S-CRED-04** | Secret rotation | Missing credential rotation mechanisms |
| **S-INPUT-01** | Injection | SQL, command, LDAP, XPath injection vectors |
| **S-INPUT-02** | Path traversal | User input in file paths without sanitization |
| **S-INPUT-03** | Deserialization | Unsafe deserialization of untrusted data |
| **S-INPUT-04** | Size limits | Unbounded input that could cause DoS |
| **S-AUTH-01** | Authentication bypass | Missing or weak authentication checks |
| **S-AUTH-02** | Authorization gaps | Missing permission checks on sensitive operations |
| **S-AUTH-03** | Session management | Insecure session or token handling |
| **S-AUTH-04** | Privilege escalation | Operations that could elevate user privileges |
| **S-CRYPTO-01** | Weak algorithms | MD5, SHA1, DES, or deprecated cryptography |
| **S-CRYPTO-02** | Hardcoded keys | Encryption keys embedded in source code |
| **S-CRYPTO-03** | Random generation | Non-cryptographic RNG for security purposes |
| **S-CRYPTO-04** | TLS configuration | Missing or weak TLS settings |
| **S-DATA-01** | PII exposure | Personal data logged, cached, or transmitted insecurely |
| **S-DATA-02** | Error leakage | Stack traces or internal details exposed to users |
| **S-DATA-03** | SSRF | Server-side request forgery via user-controlled URLs |
| **S-DATA-04** | Race conditions | TOCTOU or other concurrency vulnerabilities |

## Severity Levels

| Severity | Score Deduction | Meaning |
|----------|-----------------|---------|
| Critical | -25 pts | Runtime failures, data loss, security vulnerabilities |
| High | -15 pts | Significant design issues causing maintainability problems |
| Medium | -8 pts | Style or quality issues that should be addressed |
| Low | -3 pts | Minor improvements, nitpicks |
| Info | 0 pts | Observations, no action required |

Deductions are weighted by confidence: `weight * (confidence / 100)`. A critical finding at 80% confidence deducts 20 points.

## Verdict Logic

| Scenario | Verdict | Exit Code |
|----------|---------|-----------|
| No findings, all agents OK | APPROVE | 0 |
| Findings present, score above threshold | COMMENT | 0 |
| Critical finding at high confidence | REQUEST_CHANGES | 1 |
| Score below blocker threshold | REQUEST_CHANGES | 1 |
| Any agent failed | COMMENT (never approve/reject with incomplete data) | 0 |
| Both agents failed | COMMENT with error details | 3 |

## Quick Start

### GitHub

Add `.github/workflows/prbot.yml`:

```yaml
name: prbot Review

on:
  pull_request:
    types: [opened, synchronize, reopened]

permissions:
  contents: read
  pull-requests: write
  id-token: write

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: aws-actions/configure-aws-credentials@e3dd6a429d7300a6a4c196c26e071d42e0343502
        with:
          role-to-assume: ${{ vars.PRBOT_AWS_ROLE_ARN }}
          aws-region: ${{ vars.PRBOT_AWS_REGION || 'us-east-1' }}

      - uses: docker://ghcr.io/nilesuan/prbot:latest
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          PRBOT_PLATFORM: github
          PRBOT_REPO: ${{ github.repository }}
          PRBOT_PR_NUMBER: ${{ github.event.pull_request.number }}
```

### GitLab

Add to `.gitlab-ci.yml`:

```yaml
prbot-review:
  stage: test
  image: ghcr.io/nilesuan/prbot:latest
  variables:
    PRBOT_PLATFORM: gitlab
    PRBOT_REPO: $CI_PROJECT_PATH
    PRBOT_PR_NUMBER: $CI_MERGE_REQUEST_IID
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
  allow_failure: true
  script:
    - prbot --platform gitlab --repo "$CI_PROJECT_PATH" --pr "$CI_MERGE_REQUEST_IID"
```

See [docs/github-setup.md](docs/github-setup.md) and [docs/gitlab-setup.md](docs/gitlab-setup.md) for full guides including AWS OIDC setup, fork PR handling, self-hosted GitLab, and all configuration options.

## Configuration

All settings can be set via environment variables (`PRBOT_` prefix), `.prbot.toml`, or CLI flags. Merge priority: CLI > env > TOML > defaults.

| Variable | Default | Description |
|----------|---------|-------------|
| `PRBOT_PLATFORM` | auto-detected | `github` or `gitlab` |
| `PRBOT_REPO` | auto-detected | Repository in `owner/repo` format |
| `PRBOT_PR_NUMBER` | auto-detected | PR/MR number |
| `PRBOT_AWS_REGION` | `ap-southeast-2` | AWS region for Bedrock |
| `PRBOT_CONFIDENCE_THRESHOLD` | `70` | Minimum confidence to report a finding |
| `PRBOT_BLOCKER_THRESHOLD` | `70` | Minimum confidence for blocker findings |
| `PRBOT_GENERAL_MODEL_ID` | `us.anthropic.claude-sonnet-4-20250514` | General review model |
| `PRBOT_SECURITY_MODEL_ID` | `us.anthropic.claude-opus-4-0-20250514` | Security review model |
| `PRBOT_MAX_DIFF_TOKENS` | `100000` | Max diff size before rejection |
| `PRBOT_BUDGET_LIMIT_USD` | `5.00` | Max estimated cost per review |
| `PRBOT_TIMEOUT_SECONDS` | `300` | Review timeout |
| `PRBOT_DRAFT_BEHAVIOR` | `skip` | `skip` or `review` for draft PRs |
| `PRBOT_EXCLUDED_PATTERNS` | *(none)* | Glob patterns to exclude from review |
| `PRBOT_DRY_RUN` | `false` | Print review without posting |

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Review passed (APPROVE or COMMENT) |
| 1 | Review found blockers (REQUEST_CHANGES) |
| 2 | Configuration error |
| 3 | Infrastructure error |

## Architecture

```
src/prbot/
├── cli.py               # Entry point, pre-flight checks, pipeline orchestration
├── config.py            # Pydantic v2 frozen config, TOML loading, env var merge
├── exceptions.py        # Typed exception hierarchy with exit code mapping
├── auth/                # Token resolution (env → Secrets Manager), scope validation
├── vcs/                 # VCSAdapter protocol + GitHub/GitLab implementations (httpx)
├── review/              # 2-agent runner, scoring, verdicts, prompt builder, formatter
├── security/            # Datamarking, hallucination validation, diff filtering, PII/secret redaction
└── observability/       # Structured logging, audit trail, data residency validation
```

## License

See [LICENSE](LICENSE) for details.
