# prbot

Security-hardened, AI-powered PR/MR review bot. Runs as a container in GitHub Actions or GitLab CI, analyzes diffs with two parallel Claude agents (general + security) via Amazon Bedrock, and posts structured review comments with confidence-scored findings.

## Why prbot?

### Dual-Agent Architecture

Every PR gets reviewed by two independent AI agents running concurrently:

- **General agent** -- architecture, maintainability, testing, error handling, API contracts, and completeness
- **Security agent** -- credentials, injection, auth, cryptography, and data safety

Both default to Claude Sonnet. Either can be pointed at a different model with `PRBOT_GENERAL_MODEL_ID` and `PRBOT_SECURITY_MODEL_ID`.

Two models catch what one misses. If one agent fails, the other still produces findings -- partial failure never blocks the review.

### Adversarial Agent (optional)

A third agent that asks a different question. The two default agents work from
category checklists, so they find what resembles a listed pattern and miss what
fits no category. The adversarial agent is asked to construct a concrete
failure instead, and every one of its findings must carry a `failure_scenario`:
the trigger, then the wrong outcome. A finding without one is dropped, so a
model that hedges produces nothing rather than noise.

Its categories are about behaviour rather than shape: silent no-ops (a guard
that does not guard, a glob that matches nothing, a config value overwritten
before it is read), boundaries and degenerate input, failure and partial
failure, ordering and concurrency, and contract drift.

It is off by default, because a third agent is roughly 50% more spend per
review. Enable it by declaring the roster:

```toml
[[prbot.agents]]
name = "general"
check_prefix = "Q-"

[[prbot.agents]]
name = "security"
check_prefix = "S-"

[[prbot.agents]]
name = "adversarial"
check_prefix = "X-"
```

The roster is also how you add your own agent: drop `myagent.md` into a
directory, point `PRBOT_PROMPTS_DIR` at it, and give the agent a name and a
check prefix. Each entry may set its own `model_id`, and `enabled = false`
turns one off without deleting it.

### Confidence-Based Scoring

Not all findings are equal. prbot uses confidence bands to reduce noise:

- **Reported** -- high confidence findings shown directly in the review
- **Borderline** -- medium confidence findings collapsed in a details section
- **Hidden** -- low confidence findings counted but not shown

Each finding has a severity weight and confidence score. The combination produces a 0-100 review score and a deterministic verdict (APPROVE, COMMENT, or REQUEST_CHANGES).

### Security by Default

- **5-layer prompt injection defense** -- system/user message separation, Microsoft Spotlighting datamarking (structure-preserving, so hunk headers and line prefixes survive), hallucination validation against actual diff content, output redaction, and sanitisation of everything the model writes into the posted comment
- **Token safety** -- tokens never appear in `repr()`/`str()`, every log record goes through one redaction processor regardless of whether it came from structlog or stdlib logging, CI masking via `::add-mask::`
- **SSRF protection** -- all URLs validated against private/loopback/metadata addresses
- **Signed container image** -- Cosign keyless signing with Sigstore for supply chain verification
- **Vulnerability scanning** -- Trivy scans every build for CRITICAL/HIGH CVEs
- **OIDC authentication** -- short-lived AWS credentials via GitHub/GitLab OIDC federation (no static keys)
- **Path traversal rejection** -- findings with `..` or absolute paths in `file_path` are dropped
- **PII redaction** -- personal data patterns removed from review comments before posting
- **Secret redaction** -- GitHub PATs, GitLab PATs, AWS long-lived and temporary keys, Bearer tokens caught and replaced, with the label kept so the reader can see what was found
- **Structured output** -- findings come back through a forced tool carrying a JSON schema, at temperature 0, with an explicit output token cap

### Completeness Checks

prbot doesn't just review code quality -- it checks that PRs are complete:

- Missing documentation for behavior changes
- Missing version bumps for features and breaking changes
- Missing changelog entries for non-trivial changes
- Outdated examples when public APIs change

### Inline Comments and Real Reviews

By default prbot posts one summary comment. Set `PRBOT_REVIEW_MODE=review` and
it submits a platform review instead: each finding that lands on a line the
diff covers becomes a comment on that line, and the verdict reaches the pull
request rather than only the exit code. On GitHub that is one review with
`event: APPROVE`, `COMMENT` or `REQUEST_CHANGES`, pinned to the commit
reviewed. On GitLab it is a summary note, one positioned discussion per
finding, and approve or unapprove.

A finding whose lines fall outside the diff stays in the summary rather than
being anchored to a line the platform would reject. An inline position the
platform refuses, usually a line that has moved, is dropped with a warning:
losing one anchor is acceptable, losing the review is not.

### Surrounding Code

A hunk carries three lines of context, which is rarely the enclosing function,
so a judgement about architecture or test coverage is being made without the
thing being judged. `PRBOT_CONTEXT_LINES=30` includes that many numbered lines
of the head revision around each hunk.

It defaults to 0. The extra tokens are real and whether they buy enough
precision to be worth it is a question for measurement on your own pull
requests, not one to settle by argument. The excerpt is datamarked like the
patch, since it is file content from the same pull request.

### Large Diffs

A diff larger than `PRBOT_MAX_DIFF_TOKENS` used to be refused outright, so the
pull requests most worth reviewing got no review. It is now reviewed in
several passes, packed by file in their original order, and the findings are
merged and deduplicated as though they came from one pass.

The cost of the whole run is estimated before any call is made and checked
against `PRBOT_BUDGET_LIMIT_USD`, so splitting cannot quietly multiply the
bill. Lowering `PRBOT_MAX_DIFF_TOKENS` is now a way to trade money for
attention: smaller passes mean the model reads less at once.

### One Comment Per Finding, and Closing Them Out

In `review` mode each finding is posted as its own comment thread, stamped
with an invisible fingerprint of the file, the check and what the finding
says. Deliberately not the line numbers: a defect on line 12 that becomes
line 400 because something was inserted above it is the same defect.

On the next review prbot reads back the threads it wrote and sorts them:

| | What prbot does | What it means |
|---|---|---|
| Still reported | Leaves the thread alone | Not acted on yet |
| No longer reported | Replies "No longer reported as of `abc1234`" and resolves the thread | The author fixed it |
| Resolved by a human | Leaves it alone, never re-raises it | Someone decided it is settled |

That makes the pull request itself the store. Nothing else has to hold state,
and the record of what happened sits where the people who did it are looking.

It also gives the numbers the thresholds need. Every run records how many
findings were new, persisting, fixed and human-resolved, which is what turns
`PRBOT_BLOCKER_THRESHOLD` into a calibration rather than a guess. Send those
somewhere with `PRBOT_METRICS_FILE` or `PRBOT_METRICS_NAMESPACE`.

Threads without prbot's fingerprint are ignored entirely. Human review threads
are none of the bot's business.

On GitHub, resolution state and resolving both need GraphQL. If the token
cannot reach it, prbot falls back to REST: findings are still deduplicated and
replied to, but a thread a human resolved may be re-reported. It says so in
the log when that happens.

### Suppressing a Finding

A nit that comes back on every push is the most common reason a team turns a
review bot off. Suppress one in `.prbot.toml`:

```toml
[[prbot.suppress]]
check_id = "Q-MAINT"          # a whole family, or "Q-MAINT-03" for one check
path = "src/legacy/**"        # optional, gitignore syntax
max_severity = "medium"       # optional: never silence a critical
reason = "Rewrite tracked in PROJ-123"
```

`reason` is required, because an undocumented suppression is indistinguishable
from a bug six months later. Every comment states how many findings the
configuration removed, and the audit record carries the count, so the list
cannot quietly grow into a gag.

`max_severity` is worth setting. Without it, a rule written to silence long
functions also silences a critical finding that happens to share the check
family.

### Smart Pre-flight Skipping

Reviews are skipped automatically when they'd be wasted:

- Closed or merged PRs
- Draft PRs (configurable)
- Bot authors (Dependabot, Renovate, GitHub Actions, self-review loop prevention)
- Empty diffs after exclusion patterns are applied

### Idempotent Comments and Incremental Review

prbot finds and updates its previous comment instead of posting
duplicates, using a state record embedded in an HTML comment.

That record also carries the commit the review was for. A re-run against
a commit that has already been reviewed skips the review entirely and
carries the previous verdict through to the exit code, which matters on
a pipeline that reruns, or a PR that is closed and reopened. Set
`PRBOT_FORCE_REVIEW=true` to review anyway.

The record is advisory, not authenticated: its digest is salted with an
identifier published beside it. It is only ever read from a comment
authored by prbot's own token, and anything that matters is verified
against the API instead.

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
| Findings present, no blocker, score passes | COMMENT | 0 |
| Critical or high finding at or above `PRBOT_BLOCKER_THRESHOLD` | REQUEST_CHANGES | 1 |
| Score below `PRBOT_MIN_PASSING_SCORE` | REQUEST_CHANGES | 1 |
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

env:
  PRBOT_IMAGE: ghcr.io/nilesuan/prbot
  PRBOT_IMAGE_TAG: 0.3.0

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: sigstore/cosign-installer@d7d6bc7722e3daa8354c50bcb52f4837da5e9b6a  # v3.8.1

      # Resolve the tag once, verify that digest, run that digest.
      - name: Resolve image digest
        id: image
        run: |
          digest=$(docker buildx imagetools inspect \
            "${PRBOT_IMAGE}:${PRBOT_IMAGE_TAG}" \
            --format '{{.Manifest.Digest}}')
          echo "ref=${PRBOT_IMAGE}@${digest}" >> "$GITHUB_OUTPUT"

      - name: Verify prbot image
        run: |
          cosign verify "${{ steps.image.outputs.ref }}" \
            --certificate-identity-regexp='^https://github\.com/nilesuan/prbot/\.github/workflows/' \
            --certificate-oidc-issuer='https://token.actions.githubusercontent.com'

      - uses: aws-actions/configure-aws-credentials@e3dd6a429d7300a6a4c196c26e071d42e0343502  # v4.0.2
        with:
          role-to-assume: ${{ vars.PRBOT_AWS_ROLE_ARN }}
          aws-region: ${{ vars.PRBOT_AWS_REGION || 'ap-southeast-2' }}

      - name: Run prbot
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          docker run --rm \
            -e GITHUB_TOKEN -e GITHUB_ACTIONS=true -e GITHUB_API_URL \
            -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN \
            -e AWS_REGION -e AWS_DEFAULT_REGION \
            -e PRBOT_PLATFORM=github \
            -e PRBOT_REPO="${{ github.repository }}" \
            -e PRBOT_PR_NUMBER="${{ github.event.pull_request.number }}" \
            "${{ steps.image.outputs.ref }}"
```

`uses: docker://` cannot interpolate an expression, which is why the container
is launched with an explicit `docker run`: it is the only way to guarantee the
image that runs is the image that was verified.

### GitLab

Add to `.gitlab-ci.yml`:

```yaml
prbot-review:
  stage: test
  image: ghcr.io/nilesuan/prbot:0.3.0
  id_tokens:
    GITLAB_OIDC_TOKEN:
      aud: https://gitlab.com  # must match the role's trust policy
  variables:
    PRBOT_PLATFORM: gitlab
    PRBOT_REPO: $CI_PROJECT_PATH
    PRBOT_PR_NUMBER: $CI_MERGE_REQUEST_IID
    PRBOT_AWS_REGION: $PRBOT_AWS_REGION
    AWS_REGION: $PRBOT_AWS_REGION
    AWS_ROLE_ARN: $PRBOT_AWS_ROLE_ARN
    AWS_WEB_IDENTITY_TOKEN_FILE: /tmp/prbot-oidc-token
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
  allow_failure: true
  before_script:
    # boto3 performs the web identity exchange itself. The image contains a
    # Python virtual environment only, so there is no `aws` CLI in it.
    - umask 077
    - printf '%s' "$GITLAB_OIDC_TOKEN" > "$AWS_WEB_IDENTITY_TOKEN_FILE"
  script:
    - prbot --platform gitlab --repo "$CI_PROJECT_PATH" --pr "$CI_MERGE_REQUEST_IID"
```

See [docs/github-setup.md](docs/github-setup.md) and [docs/gitlab-setup.md](docs/gitlab-setup.md) for full guides including AWS OIDC setup, fork PR handling, self-hosted GitLab, and all configuration options.

## Configuration

prbot does not read `.prbot.toml` from the working directory unless you
ask it to. A CI runner clones the branch under review into that
directory, so a configuration file found there is one the reviewed code
controls, and it can set the API base URL the token is sent to. Name a
file with `--config`, or set `PRBOT_ALLOW_IMPLICIT_CONFIG=1` for local
use. The opt-in never applies inside CI.

All settings can be set via environment variables (`PRBOT_` prefix), `.prbot.toml`, or CLI flags. Merge priority: CLI > env > TOML > defaults.

| Variable | Default | Description |
|----------|---------|-------------|
| `PRBOT_PLATFORM` | auto-detected | `github` or `gitlab` |
| `PRBOT_REPO` | auto-detected | Repository in `owner/repo` format |
| `PRBOT_PR_NUMBER` | auto-detected | PR/MR number |
| `PRBOT_AWS_REGION` | `ap-southeast-2` | AWS region for Bedrock |
| `PRBOT_ALLOWED_REGIONS` | `ap-southeast-2` | Comma-separated regions the review may run in |
| `PRBOT_CONFIDENCE_THRESHOLD` | `70` | Minimum confidence to report a finding |
| `PRBOT_BLOCKER_THRESHOLD` | `70` | Minimum **confidence** at which a critical or high finding blocks |
| `PRBOT_MIN_PASSING_SCORE` | `70` | Minimum **score** (0-100) a review must reach to pass |
| `PRBOT_GENERAL_MODEL_ID` | `au.anthropic.claude-sonnet-5` | General review model |
| `PRBOT_SECURITY_MODEL_ID` | `au.anthropic.claude-sonnet-5` | Security review model |
| `PRBOT_MAX_DIFF_TOKENS` | `100000` | Tokens per review call; a larger diff is reviewed in several passes |
| `PRBOT_MAX_OUTPUT_TOKENS` | `8192` | Max tokens in a single agent response |
| `PRBOT_CONTEXT_LINES` | `0` | Lines of surrounding code to include around each hunk |
| `PRBOT_BUDGET_LIMIT_USD` | `5.00` | Max estimated cost per review |
| `PRBOT_TIMEOUT_SECONDS` | `300` | Review timeout |
| `PRBOT_DRAFT_BEHAVIOR` | `skip` | `skip` or `review` for draft PRs |
| `PRBOT_REVIEW_MODE` | `comment` | `comment` for one summary comment, `review` for a platform review with inline comments |
| `PRBOT_FORCE_REVIEW` | `false` | Review again even if this commit was already reviewed |
| `PRBOT_EXCLUDED_PATTERNS` | *(none)* | Comma-separated gitignore-style patterns to exclude |
| `PRBOT_DATAMARK_DIFF` | `true` | Whether patch content is datamarked (metadata always is) |
| `PRBOT_LOG_LEVEL` | `INFO` | Log level for prbot's own output |
| `PRBOT_ALLOW_IMPLICIT_CONFIG` | *(unset)* | Allow searching the working directory for `.prbot.toml`; never applies in CI |
| `PRBOT_METRICS_FILE` | *(none)* | Append run metrics as JSON lines, for a CI artifact |
| `PRBOT_METRICS_NAMESPACE` | *(none)* | Publish run metrics to CloudWatch under this namespace |
| `PRBOT_DRY_RUN` | `false` | Print review without posting |
| `PRBOT_SECRET_NAME` | *(none)* | Secrets Manager secret holding the VCS token |
| `PRBOT_API_BASE_URL` | platform default | API base URL for GitHub Enterprise or self-hosted GitLab |

`PRBOT_BLOCKER_THRESHOLD` and `PRBOT_MIN_PASSING_SCORE` are different
quantities that share a range. The first asks how sure the model is about one
finding; the second asks how the review scored overall.

Exclusion patterns use gitignore syntax. A pattern with no separator matches
at any depth (`*.lock`), a leading `**/` matches the root as well as any
subdirectory (`**/node_modules/**`), and a pattern with a separator is
anchored to the repository root (`vendor/**`).

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

Not yet chosen. Until a licence is added, no permission to use,
copy, modify or distribute this code is granted.
