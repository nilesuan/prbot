# GitHub Setup Guide

Set up prbot to automatically review pull requests on GitHub repositories.

## Prerequisites

- **AWS account** with [Amazon Bedrock](https://aws.amazon.com/bedrock/) model access enabled for whichever models you configure (Claude Sonnet by default for both agents)
- **GitHub repository** (public or private)
- An **AWS IAM OIDC identity provider** configured for GitHub Actions (recommended), or static IAM credentials

## Architecture

```
PR opened/updated
  -> GitHub Actions workflow triggers
  -> AWS credentials obtained via OIDC
  -> prbot container runs
  -> Reads PR diff via GitHub API (using GITHUB_TOKEN)
  -> Sends diff to Amazon Bedrock (2-agent review: general + security)
  -> Posts review comment on the PR
```

## Quick Start (Same-Repo PRs)

### 1. Configure AWS OIDC for GitHub Actions

Create an IAM OIDC identity provider for GitHub Actions in your AWS account. The provider URL is `https://token.actions.githubusercontent.com`.

Create an IAM role with a trust policy that allows your repository:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::YOUR_ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:YOUR_ORG/YOUR_REPO:*"
        }
      }
    }
  ]
}
```

Attach a policy granting Bedrock invoke access:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "bedrock:InvokeModel",
      "Resource": "arn:aws:bedrock:*::foundation-model/anthropic.*"
    }
  ]
}
```

If using AWS Secrets Manager for token storage (optional), also add:

```json
{
  "Effect": "Allow",
  "Action": "secretsmanager:GetSecretValue",
  "Resource": "arn:aws:secretsmanager:YOUR_REGION:YOUR_ACCOUNT_ID:secret:prbot/*"
}
```

### 2. Set Repository Variables

Go to **Settings > Secrets and variables > Actions > Variables** and add:

| Variable | Value | Example |
|----------|-------|---------|
| `PRBOT_AWS_ROLE_ARN` | IAM role ARN from step 1 | `arn:aws:iam::123456789012:role/prbot-github` |
| `PRBOT_AWS_REGION` | AWS region with Bedrock access | `us-east-1` |

No secrets need to be added -- `GITHUB_TOKEN` is automatically provided by GitHub Actions.

### 3. Add the Workflow

Create `.github/workflows/prbot.yml`:

```yaml
name: prbot Review

on:
  pull_request:
    types: [opened, synchronize, reopened]

permissions:
  contents: read
  pull-requests: write
  id-token: write  # Required for OIDC

concurrency:
  group: prbot-${{ github.event.pull_request.number }}
  cancel-in-progress: true

jobs:
  review:
    name: PR Review
    runs-on: ubuntu-latest
    # Skip fork PRs if you don't want to review them
    if: github.event.pull_request.head.repo.full_name == github.repository
    steps:
      - name: Configure AWS credentials (OIDC)
        uses: aws-actions/configure-aws-credentials@e3dd6a429d7300a6a4c196c26e071d42e0343502  # v4.0.2
        with:
          role-to-assume: ${{ vars.PRBOT_AWS_ROLE_ARN }}
          aws-region: ${{ vars.PRBOT_AWS_REGION || 'ap-southeast-2' }}

      - name: Run prbot
      - name: Resolve image digest
        id: image
        env:
          PRBOT_IMAGE: ghcr.io/nilesuan/prbot
          PRBOT_IMAGE_TAG: 0.5.2
        run: |
          digest=$(docker buildx imagetools inspect \
            "${PRBOT_IMAGE}:${PRBOT_IMAGE_TAG}" \
            --format '{{.Manifest.Digest}}')
          echo "ref=${PRBOT_IMAGE}@${digest}" >> "$GITHUB_OUTPUT"

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
            -e PRBOT_BOT_LOGIN='github-actions[bot]' \
            "${{ steps.image.outputs.ref }}"
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          PRBOT_PLATFORM: github
          PRBOT_REPO: ${{ github.repository }}
          PRBOT_PR_NUMBER: ${{ github.event.pull_request.number }}
```

That's it. Open a pull request and prbot will post a review comment.

## Fork PR Reviews

Fork PRs require special handling because `GITHUB_TOKEN` in a `pull_request` event has read-only access to the base repo. Use `pull_request_target` with safety guards.

Create `.github/workflows/prbot-fork.yml`:

```yaml
name: prbot Review (Fork)

on:
  pull_request_target:
    types: [opened, synchronize, reopened]

permissions:
  contents: read
  pull-requests: write
  id-token: write

concurrency:
  group: prbot-fork-${{ github.event.pull_request.number }}
  cancel-in-progress: true

jobs:
  review:
    name: Fork PR Review
    runs-on: ubuntu-latest
    environment: fork-review  # Require manual approval for first-time contributors
    # Every gate is a job-level condition. `exit 0` inside a run: step ends
    # that step successfully and lets the job continue, so an unknown author
    # would still reach the OIDC role and a paid review.
    if: >-
      github.event.pull_request.head.repo.full_name != github.repository
      && github.event.pull_request.state != 'closed'
      && github.event.pull_request.author_association != 'FIRST_TIME_CONTRIBUTOR'
      && github.event.pull_request.author_association != 'NONE'
    steps:
      - name: Configure AWS credentials (OIDC)
        uses: aws-actions/configure-aws-credentials@e3dd6a429d7300a6a4c196c26e071d42e0343502  # v4.0.2
        with:
          role-to-assume: ${{ vars.PRBOT_FORK_AWS_ROLE_ARN }}
          aws-region: ${{ vars.PRBOT_AWS_REGION || 'ap-southeast-2' }}

      - name: Run prbot
      - name: Resolve image digest
        id: image
        env:
          PRBOT_IMAGE: ghcr.io/nilesuan/prbot
          PRBOT_IMAGE_TAG: 0.5.2
        run: |
          digest=$(docker buildx imagetools inspect \
            "${PRBOT_IMAGE}:${PRBOT_IMAGE_TAG}" \
            --format '{{.Manifest.Digest}}')
          echo "ref=${PRBOT_IMAGE}@${digest}" >> "$GITHUB_OUTPUT"

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
            -e PRBOT_BOT_LOGIN='github-actions[bot]' \
            "${{ steps.image.outputs.ref }}"
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          PRBOT_PLATFORM: github
          PRBOT_REPO: ${{ github.repository }}
          PRBOT_PR_NUMBER: ${{ github.event.pull_request.number }}
```

### Fork Security Notes

- **`pull_request_target`** runs in the base repo context, so `GITHUB_TOKEN` has write access to post comments
- **No `actions/checkout`** -- fork code is never checked out, preventing code execution attacks
- **Author association gating** skips reviews for unknown contributors to prevent abuse
- **`environment: fork-review`** lets you require manual approval via GitHub Environment protection rules
- Use a **separate IAM role** (`PRBOT_FORK_AWS_ROLE_ARN`) with tighter permissions for fork reviews

## Configuration

### Environment Variables

All configuration can be set via `PRBOT_`-prefixed environment variables in the workflow:

| Variable | Default | Description |
|----------|---------|-------------|
| `PRBOT_PLATFORM` | auto-detected | `github` or `gitlab` |
| `PRBOT_REPO` | auto-detected | Repository in `owner/repo` format |
| `PRBOT_PR_NUMBER` | auto-detected | PR number to review |
| `PRBOT_AWS_REGION` | `ap-southeast-2` | AWS region for Bedrock API calls |
| `PRBOT_ALLOWED_REGIONS` | `ap-southeast-2` | Comma-separated list of allowed AWS regions |
| `PRBOT_CONFIDENCE_THRESHOLD` | `70` | Minimum confidence (0-100) to report a finding |
| `PRBOT_BLOCKER_THRESHOLD` | `70` | Minimum confidence (0-100) to mark a finding as a blocker |
| `PRBOT_GENERAL_MODEL_ID` | `au.anthropic.claude-sonnet-5` | Bedrock model ID for general review agent |
| `PRBOT_SECURITY_MODEL_ID` | `au.anthropic.claude-sonnet-5` | Bedrock model ID for security review agent |
| `PRBOT_MIN_PASSING_SCORE` | `70` | Minimum review score (0-100) required to pass |
| `PRBOT_MAX_OUTPUT_TOKENS` | `8192` | Max tokens in a single agent response |
| `PRBOT_TEMPERATURE` | unset | Sampling temperature, sent only when set. The default model rejects it; set `0` for a model that accepts it |
| `PRBOT_TOOL_TURNS` | `0` | Turns an agent may spend reading other files of the repository (`read_file`) before it must report. Off by default: measured so far to add cost without adding findings |
| `PRBOT_VERIFY` | `false` | A second call per chunk that checks each finding against the code and replaces its confidence with the verdict's. Given up before the review when the budget cannot cover it |
| `PRBOT_BOT_LOGIN` | unset | The login prbot posts as, used when the token cannot read it. Set `github-actions[bot]` with `GITHUB_TOKEN`, or prbot cannot tell its own threads from anyone else's. Never a person's login |
| `PRBOT_DATAMARK_DIFF` | `true` | Whether patch content is datamarked (metadata always is) |
| `PRBOT_ALLOWED_REGIONS` | `ap-southeast-2` | Comma-separated regions the review may run in |
| `PRBOT_MAX_DIFF_TOKENS` | `100000` | Maximum diff size in tokens before rejection |
| `PRBOT_BUDGET_LIMIT_USD` | `5.00` | Maximum estimated cost per review |
| `PRBOT_TIMEOUT_SECONDS` | `300` | Review timeout in seconds |
| `PRBOT_DRAFT_BEHAVIOR` | `skip` | `skip` to ignore draft PRs, `review` to review them |
| `PRBOT_EXCLUDED_PATTERNS` | *(none)* | Comma-separated glob patterns to exclude from review |
| `PRBOT_LOG_LEVEL` | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `PRBOT_DRY_RUN` | `false` | Set to `true` to print the review without posting it |
| `PRBOT_SECRET_NAME` | *(none)* | AWS Secrets Manager secret name for VCS token fallback |

### TOML Config File

For per-repository defaults, add a `.prbot.toml` file to the repo root:

```toml
[prbot]
confidence_threshold = 70
blocker_threshold = 70
draft_behavior = "skip"
budget_limit_usd = 5.00
timeout_seconds = 300
excluded_patterns = ["*.lock", "vendor/**", "*.min.js"]
```

Or add a `[tool.prbot]` section to `pyproject.toml`:

```toml
[tool.prbot]
confidence_threshold = 70
excluded_patterns = ["*.lock"]
```

Config merge priority: **CLI args > environment variables > TOML file > defaults**.

## Exit Codes

| Code | Meaning | Workflow effect |
|------|---------|-----------------|
| 0 | Review passed (APPROVE or COMMENT) | Job succeeds |
| 1 | Review found blockers (REQUEST_CHANGES) | Job fails |
| 2 | Configuration error | Job fails |
| 3 | Infrastructure error (API timeout, auth failure) | Job fails |

To make blocker findings non-blocking (advisory mode), wrap the run step:

```yaml
      - name: Run prbot
        continue-on-error: true
      - name: Resolve image digest
        id: image
        env:
          PRBOT_IMAGE: ghcr.io/nilesuan/prbot
          PRBOT_IMAGE_TAG: 0.5.2
        run: |
          digest=$(docker buildx imagetools inspect \
            "${PRBOT_IMAGE}:${PRBOT_IMAGE_TAG}" \
            --format '{{.Manifest.Digest}}')
          echo "ref=${PRBOT_IMAGE}@${digest}" >> "$GITHUB_OUTPUT"

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
            -e PRBOT_BOT_LOGIN='github-actions[bot]' \
            "${{ steps.image.outputs.ref }}"
        env:
          # ...
```

## Pre-flight Behavior

prbot automatically skips reviews in these cases (exits with code 0):

- **Closed or merged PRs** -- no review needed
- **Draft PRs** -- skipped by default (set `PRBOT_DRAFT_BEHAVIOR=review` to override)
- **Bot authors** -- PRs from Dependabot, Renovate, GitHub Actions bot, or the prbot user itself are skipped to prevent review loops
- **Empty diffs** -- PRs with no reviewable file changes after applying exclusion patterns

## Cosign Image Verification (Optional)

The prbot container image is signed with [Sigstore Cosign](https://docs.sigstore.dev/cosign/overview/) using keyless signing. To verify the image before running:

```yaml
      - name: Install Cosign
        uses: sigstore/cosign-installer@d7d6bc7722e3daa8354c50bcb52f4837da5e9b6a  # v3.8.1

      - name: Verify prbot image
        env:
          PRBOT_IMAGE: ghcr.io/nilesuan/prbot:0.5.2
        run: |
          cosign verify "$PRBOT_IMAGE" \
            --certificate-identity-regexp=".*" \
            --certificate-oidc-issuer="https://token.actions.githubusercontent.com"
```

## Troubleshooting

### "OIDC token not found"

Ensure `id-token: write` is in the workflow `permissions` block.

### "Access denied to Bedrock"

Check that your IAM role has `bedrock:InvokeModel` permission and the trust policy allows your repo.

### "No VCS token found"

`GITHUB_TOKEN` must be passed explicitly to the container. Nothing is injected automatically. The container is launched with `docker run` rather than `uses: docker://...` because `uses:` cannot interpolate an expression, so it cannot reference the digest that was verified.

### "Configuration validation failed: repo must be 'owner/name'"

The `PRBOT_REPO` value must be in `owner/repo` format (e.g., `octocat/hello-world`). Check that `${{ github.repository }}` is correctly passed.

### Review is too slow

- Lower `PRBOT_TIMEOUT_SECONDS` to cap the review duration
- Add large generated files to `PRBOT_EXCLUDED_PATTERNS`
- Lower `PRBOT_MAX_DIFF_TOKENS` to reject oversized diffs early

### Review costs too much

- Lower `PRBOT_BUDGET_LIMIT_USD` to cap estimated cost
- Point `PRBOT_GENERAL_MODEL_ID` at a cheaper model, and check it
  against `PRICING` in `review/budget.py` so the cost estimate is
  accurate rather than falling back to the Opus upper bound
- Add exclusion patterns to reduce diff size
