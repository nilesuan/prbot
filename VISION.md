# prbot — Automated PR/MR Review Bot

## Purpose

A containerised PR/MR review bot that runs as a GitHub Actions or GitLab CI job. It fetches the diff and metadata, fans out 5 parallel AWS Bedrock (Claude) calls — one per review category — aggregates findings with confidence scoring, and posts a structured review comment.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  CI Job (GitHub Actions / GitLab CI)                │
│                                                     │
│  ┌───────────────────────────────────────────────┐  │
│  │  Container: prbot (Python 3.12 + uv)          │  │
│  │                                               │  │
│  │  1. Auth: env var → Secrets Manager fallback  │  │
│  │  2. VCS adapter (GitHub / GitLab) fetches:    │  │
│  │     - PR metadata, diff, linked issues        │  │
│  │  3. Prompt builder: injects diff + checks     │  │
│  │     into category-specific system prompts     │  │
│  │  4. Fan out 5 parallel Bedrock Converse calls │  │
│  │     ┌──────┐ ┌──────┐ ┌──────┐ ┌────┐ ┌────┐│  │
│  │     │Arch  │ │Qual  │ │ Sec* │ │Test│ │API ││  │
│  │     │Sonnet│ │Sonnet│ │Opus  │ │Son.│ │Son.││  │
│  │     └──┬───┘ └──┬───┘ └──┬───┘ └─┬──┘ └─┬──┘│  │
│  │        └────────┴────────┴───────┴──────┘   │  │
│  │  5. Aggregator: merge, score, verdict        │  │
│  │  6. VCS adapter: post comment to PR/MR       │  │
│  └───────────────────────────────────────────────┘  │
│                                                     │
│  IAM: Task Role / Instance Profile → bedrock:*      │
│  Token: $GH_TOKEN / $GITLAB_TOKEN or Secrets Mgr   │
└─────────────────────────────────────────────────────┘
```

## Components

| Component | Responsibility |
|-----------|---------------|
| `cli.py` | Entry point. Parses args (`--platform`, `--pr`, `--repo`), orchestrates the pipeline |
| `vcs/base.py` | Abstract VCS interface: `get_pr()`, `get_diff()`, `post_comment()` |
| `vcs/github.py` | GitHub implementation using `gh` CLI or REST API |
| `vcs/gitlab.py` | GitLab implementation using `glab` CLI or REST API |
| `review/prompts.py` | Loads the 77-check spec, builds category-specific system prompts |
| `review/agents.py` | 5 review agent definitions (arch, quality, security, testing, api+iac) |
| `review/runner.py` | Async fan-out of Bedrock Converse calls via `asyncio.gather()` |
| `review/scorer.py` | Confidence filtering, deduction scoring, verdict logic |
| `review/formatter.py` | Renders findings into the structured markdown comment |
| `auth.py` | Token resolution: env var → Secrets Manager fallback |
| `config.py` | Configuration (model IDs, thresholds, region) from env vars + optional config file |

## Key Design Decisions

1. **VCS abstraction from day 1** — `VCSAdapter` protocol with `GitHubAdapter` and `GitLabAdapter`. Both use their respective CLI tools (`gh`, `glab`) since they'll be available in the container.
2. **Prompts as data** — The 77-check spec markdown files are bundled into the container and loaded at runtime. Update checks by updating markdown, rebuild image.
3. **Structured JSON output** — Each Bedrock call requests JSON output with a defined schema (findings array with id, description, file, line, confidence, severity). Python code handles scoring — not the model.
4. **Opus for security only** — The security agent uses `us.anthropic.claude-opus-4-0-20250514` for deeper analysis. All others use `us.anthropic.claude-sonnet-4-20250514`.
5. **No tool use** — Pure prompt-in, JSON-out. All context (diff, metadata, issues) is pre-fetched and injected into prompts. This keeps Bedrock calls simple and fast.

## Container

```dockerfile
FROM python:3.12-slim
# Install uv, gh CLI, glab CLI
# COPY src + prompts
# ENTRYPOINT ["python", "-m", "prbot"]
```

## Configuration (env vars)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PRBOT_PLATFORM` | yes | — | `github` or `gitlab` |
| `PRBOT_PR_NUMBER` | yes | — | PR/MR number to review |
| `PRBOT_REPO` | yes | — | `owner/repo` |
| `GH_TOKEN` / `GITLAB_TOKEN` | yes* | — | VCS API token (*or via Secrets Manager) |
| `AWS_REGION` | no | `ap-southeast-2` | Bedrock region |
| `PRBOT_SECRET_NAME` | no | — | Secrets Manager secret name for VCS token |
| `PRBOT_CONFIDENCE_THRESHOLD` | no | `70` | Min confidence to report findings |
| `PRBOT_BLOCKER_THRESHOLD` | no | `50` | Min confidence for blockers |

## File Structure

```
prbot/
├── src/prbot/
│   ├── __init__.py
│   ├── cli.py
│   ├── config.py
│   ├── auth.py
│   ├── vcs/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── github.py
│   │   └── gitlab.py
│   └── review/
│       ├── __init__.py
│       ├── prompts.py
│       ├── agents.py
│       ├── runner.py
│       ├── scorer.py
│       └── formatter.py
├── prompts/
│   ├── architecture.md
│   ├── code-quality.md
│   ├── security.md
│   ├── testing-performance.md
│   └── api-iac-requirements.md
├── research/
│   └── automated-pr-review-checklist/
│       ├── discover-pass-1.md
│       ├── discover-pass-2.md
│       ├── discover-pass-3.md
│       ├── recommendation.md
│       └── references.md
├── skill/
│   └── review-pr.md
├── tests/
│   └── ...
├── Dockerfile
├── pyproject.toml
├── .gitignore
├── CLAUDE.md
└── VISION.md
```
