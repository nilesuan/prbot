# GitLab Setup Guide

Set up prbot to automatically review merge requests on GitLab -- both GitLab.com and self-hosted instances.

## Prerequisites

- **AWS account** with [Amazon Bedrock](https://aws.amazon.com/bedrock/) model access enabled (Claude Sonnet + Claude Opus)
- **GitLab instance** (GitLab.com or self-hosted, version 15.7+ for OIDC support)
- A **project or group access token** with `api` scope
- An **AWS IAM OIDC identity provider** configured for your GitLab instance (recommended), or static IAM credentials

## Architecture

```
MR opened/updated
  -> GitLab CI/CD pipeline triggers (merge_request_event)
  -> AWS credentials obtained via OIDC (or static)
  -> prbot container runs
  -> Reads MR diff via GitLab REST API (using GITLAB_TOKEN)
  -> Sends diff to Amazon Bedrock (2-agent review: general + security)
  -> Posts review note on the MR
```

## Quick Start

### 1. Create a GitLab Access Token

**Option A: Project Access Token** (single repo)

1. Go to **Settings > Access tokens**
2. Create a token with:
   - **Role:** Developer
   - **Scopes:** `api`
   - **Expiration:** set a reasonable expiry and rotate regularly

**Option B: Group Access Token** (all repos in a group)

1. Go to **Group > Settings > Access tokens**
2. Same settings as above -- the token works for all projects in the group

### 2. Configure AWS OIDC (Recommended)

Create an IAM OIDC identity provider for your GitLab instance. The provider URL depends on your setup:

| GitLab type | OIDC Provider URL |
|-------------|-------------------|
| GitLab.com | `https://gitlab.com` |
| Self-hosted | `https://your-gitlab.example.com` |

Create an IAM role with a trust policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::YOUR_ACCOUNT_ID:oidc-provider/your-gitlab.example.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "your-gitlab.example.com:aud": "https://your-gitlab.example.com"
        },
        "StringLike": {
          "your-gitlab.example.com:sub": "project_path:YOUR_GROUP/YOUR_PROJECT:*"
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

### 3. Set CI/CD Variables

Go to **Settings > CI/CD > Variables** (project or group level) and add:

| Variable | Value | Protected | Masked |
|----------|-------|-----------|--------|
| `GITLAB_TOKEN` | Access token from step 1 | Yes | Yes |
| `PRBOT_AWS_ROLE_ARN` | IAM role ARN from step 2 | No | No |
| `PRBOT_AWS_REGION` | AWS region with Bedrock access (e.g., `us-east-1`) | No | No |

If using **static AWS credentials** instead of OIDC, set these instead:

| Variable | Value | Protected | Masked |
|----------|-------|-----------|--------|
| `GITLAB_TOKEN` | Access token from step 1 | Yes | Yes |
| `AWS_ACCESS_KEY_ID` | IAM access key | Yes | Yes |
| `AWS_SECRET_ACCESS_KEY` | IAM secret key | Yes | Yes |
| `AWS_SESSION_TOKEN` | *(if using STS)* | Yes | Yes |
| `PRBOT_AWS_REGION` | AWS region | No | No |

> prbot will log a warning if `AWS_ACCESS_KEY_ID` is set without `AWS_SESSION_TOKEN`, as this indicates long-lived credentials. Prefer OIDC.

### 4. Add the CI Job

Add to your `.gitlab-ci.yml`:

#### Option A: OIDC Authentication (Recommended)

```yaml
prbot-review:
  stage: test
  image: ghcr.io/nilesuan/prbot:latest
  id_tokens:
    GITLAB_OIDC_TOKEN:
      aud: https://your-gitlab.example.com  # your GitLab URL
  variables:
    PRBOT_PLATFORM: gitlab
    PRBOT_REPO: $CI_PROJECT_PATH
    PRBOT_PR_NUMBER: $CI_MERGE_REQUEST_IID
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
  allow_failure: true
  before_script:
    - >
      export $(
        aws sts assume-role-with-web-identity
        --role-arn "$PRBOT_AWS_ROLE_ARN"
        --role-session-name "prbot-gitlab-${CI_PIPELINE_ID}"
        --web-identity-token "$GITLAB_OIDC_TOKEN"
        --duration-seconds 900
        --query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]'
        --output text
        | awk '{print "AWS_ACCESS_KEY_ID="$1" AWS_SECRET_ACCESS_KEY="$2" AWS_SESSION_TOKEN="$3}'
      )
  script:
    - prbot --platform gitlab --repo "$CI_PROJECT_PATH" --pr "$CI_MERGE_REQUEST_IID"
```

#### Option B: Static Credentials

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

The `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `GITLAB_TOKEN` CI/CD variables are automatically injected.

### 5. Using the Bundled Template (Alternative)

Instead of writing the job from scratch, include the template shipped in the prbot repository:

```yaml
include:
  - remote: 'https://raw.githubusercontent.com/nilesuan/prbot/main/.gitlab/ci/prbot.yml'
```

Then just set the CI/CD variables from step 3. The template defines a `prbot-review` job that uses OIDC.

## Self-Hosted GitLab

For self-hosted instances, prbot needs to know where your GitLab API lives. There are two ways:

### Automatic Detection (Recommended)

GitLab CI automatically sets `CI_API_V4_URL` (e.g., `https://your-gitlab.example.com/api/v4`). prbot reads this and derives the base URL. **No extra configuration needed** if running inside GitLab CI on the same instance.

### Explicit Configuration

If automatic detection doesn't work (e.g., split DNS, proxy, or running prbot outside CI), set the base URL explicitly:

```yaml
variables:
  PRBOT_API_BASE_URL: https://your-gitlab.example.com
```

Or in `.prbot.toml`:

```toml
[prbot]
api_base_url = "https://your-gitlab.example.com"
```

### HTTPS Requirement

prbot rejects `http://` URLs by default as a security measure. If your self-hosted GitLab uses plain HTTP (not recommended), set:

```yaml
variables:
  PRBOT_ALLOW_HTTP: "1"
```

### Container Registry Access

The prbot image is hosted on GitHub Container Registry (`ghcr.io`). If your GitLab runners can't reach `ghcr.io`, mirror the image to your own registry:

```bash
# Pull from GHCR and push to your registry
docker pull ghcr.io/nilesuan/prbot:latest
docker tag ghcr.io/nilesuan/prbot:latest registry.example.com/prbot:latest
docker push registry.example.com/prbot:latest
```

Then update the job:

```yaml
prbot-review:
  image: registry.example.com/prbot:latest
  # ...
```

## Configuration

### Environment Variables

All configuration can be set via `PRBOT_`-prefixed CI/CD variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `PRBOT_PLATFORM` | auto-detected | `github` or `gitlab` |
| `PRBOT_REPO` | auto-detected | Project path in `group/project` format |
| `PRBOT_PR_NUMBER` | auto-detected | MR IID to review |
| `PRBOT_API_BASE_URL` | auto-detected | GitLab instance URL (e.g., `https://gitlab.example.com`) |
| `PRBOT_AWS_REGION` | `ap-southeast-2` | AWS region for Bedrock API calls |
| `PRBOT_ALLOWED_REGIONS` | `ap-southeast-2` | Comma-separated list of allowed AWS regions |
| `PRBOT_CONFIDENCE_THRESHOLD` | `70` | Minimum confidence (0-100) to report a finding |
| `PRBOT_BLOCKER_THRESHOLD` | `70` | Minimum confidence (0-100) to mark a finding as a blocker |
| `PRBOT_GENERAL_MODEL_ID` | `us.anthropic.claude-sonnet-4-20250514` | Bedrock model ID for general review agent |
| `PRBOT_SECURITY_MODEL_ID` | `us.anthropic.claude-opus-4-0-20250514` | Bedrock model ID for security review agent |
| `PRBOT_MAX_DIFF_TOKENS` | `100000` | Maximum diff size in tokens before rejection |
| `PRBOT_BUDGET_LIMIT_USD` | `5.00` | Maximum estimated cost per review |
| `PRBOT_TIMEOUT_SECONDS` | `300` | Review timeout in seconds |
| `PRBOT_DRAFT_BEHAVIOR` | `skip` | `skip` to ignore draft/WIP MRs, `review` to review them |
| `PRBOT_EXCLUDED_PATTERNS` | *(none)* | Comma-separated glob patterns to exclude from review |
| `PRBOT_LOG_LEVEL` | `INFO` | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `PRBOT_DRY_RUN` | `false` | Set to `true` to print the review without posting it |
| `PRBOT_SECRET_NAME` | *(none)* | AWS Secrets Manager secret name for VCS token fallback |
| `PRBOT_ALLOW_HTTP` | *(unset)* | Set to `1` to allow `http://` API URLs |

### TOML Config File

For per-repository defaults, add a `.prbot.toml` file to the repo root:

```toml
[prbot]
api_base_url = "https://your-gitlab.example.com"
confidence_threshold = 70
blocker_threshold = 70
draft_behavior = "skip"
budget_limit_usd = 5.00
timeout_seconds = 300
excluded_patterns = ["*.lock", "vendor/**", "*.min.js"]
```

Config merge priority: **CLI args > environment variables > TOML file > defaults**.

### Token Resolution

prbot resolves the VCS token in this order:

1. `GITLAB_TOKEN` environment variable
2. `CI_JOB_TOKEN` environment variable (auto-provided by GitLab CI, but has limited API scope)
3. AWS Secrets Manager fallback (if `PRBOT_SECRET_NAME` is set)

`CI_JOB_TOKEN` has limited permissions and typically cannot post MR notes. Use a dedicated `GITLAB_TOKEN` (project/group access token) for full functionality.

## Exit Codes

| Code | Meaning | Pipeline effect with `allow_failure: true` |
|------|---------|----------------------------------------------|
| 0 | Review passed (APPROVE or COMMENT) | Job green |
| 1 | Review found blockers (REQUEST_CHANGES) | Job amber (warning) |
| 2 | Configuration error | Job amber |
| 3 | Infrastructure error (API timeout, auth failure) | Job amber |

To make blocker findings **fail the pipeline**, remove `allow_failure: true` from the job.

## Pre-flight Behavior

prbot automatically skips reviews in these cases (exits with code 0):

- **Closed or merged MRs** -- no review needed
- **Draft/WIP MRs** -- skipped by default (set `PRBOT_DRAFT_BEHAVIOR=review` to override)
- **Bot authors** -- MRs from `gitlab-bot`, Renovate, or the prbot user itself are skipped to prevent review loops
- **Empty diffs** -- MRs with no reviewable file changes after applying exclusion patterns

## Troubleshooting

### "GitLab API authentication failed (401)"

The `GITLAB_TOKEN` is invalid or expired. Generate a new project/group access token with `api` scope.

### "GitLab API forbidden (403)"

The token's role is too low. Ensure the access token has at least **Developer** role on the project.

### "GitLab API not found (404)"

Check that `PRBOT_REPO` matches the project path exactly (e.g., `my-group/my-project`). For nested groups, include the full path: `parent-group/sub-group/project`.

### "SSRF: URL points to private/loopback address"

prbot blocks requests to internal IP addresses. If your self-hosted GitLab resolves to a private IP (e.g., `10.x.x.x` or `192.168.x.x`), this is expected security behavior. Options:
- Use a public DNS name that resolves to a public IP
- Set `PRBOT_API_BASE_URL` to the externally-resolvable hostname

### "URL must include scheme" or "HTTP URLs are not allowed"

The `PRBOT_API_BASE_URL` must start with `https://`. If your instance uses plain HTTP, set `PRBOT_ALLOW_HTTP=1`.

### "CI_MERGE_REQUEST_IID is not set"

The pipeline is not running in a merge request context. Ensure the job has:

```yaml
rules:
  - if: $CI_PIPELINE_SOURCE == "merge_request_event"
```

### MR notes not appearing

- Verify the token has `api` scope (not just `read_api`)
- Check the token's role is Developer or higher
- `CI_JOB_TOKEN` alone usually cannot post notes -- use a dedicated `GITLAB_TOKEN`

### Review is too slow

- Lower `PRBOT_TIMEOUT_SECONDS` to cap the review duration
- Add large generated files to `PRBOT_EXCLUDED_PATTERNS`
- Lower `PRBOT_MAX_DIFF_TOKENS` to reject oversized diffs early

### Review costs too much

- Lower `PRBOT_BUDGET_LIMIT_USD` to cap estimated cost
- Use a cheaper model for `PRBOT_GENERAL_MODEL_ID` (e.g., `anthropic.claude-haiku-3-20240307`)
- Add exclusion patterns to reduce diff size

### Runners can't pull the container image

If your runners are behind a firewall and can't reach `ghcr.io`, mirror the image to your internal registry (see [Container Registry Access](#container-registry-access) above).
