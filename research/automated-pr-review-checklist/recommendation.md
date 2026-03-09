# Recommendation: Automated PR Review Checklist for Claude Code

## Executive Summary

**Use a phased hybrid approach: start with Claude Code AI review (day 1), add CI gates incrementally (weeks 1-4).** Your initial 12-item checklist expands to 77 checks across 8 categories, each classified by severity, automation confidence, and false positive rate.

The checklist is split into two enforcement layers:
1. **CI Pipeline Gates (15 checks)** — Deterministic, blocking, fast. Lint, type check, tests, secrets, SAST, SCA.
2. **AI Review Checks (62 checks)** — Judgment-based, advisory, thorough. Architecture, SOLID, security, performance, API design, IaC, requirements.

## The Answer: 77-Check Hybrid Checklist

### CI Pipeline Gates (Blocking — 15 checks)

| # | Check | Tool | Severity |
|---|-------|------|----------|
| 1 | Lint passes | ESLint/Ruff | Blocker |
| 2 | Code formatting correct | Prettier/Black | Blocker |
| 3 | Type check passes | tsc/mypy | Blocker |
| 4 | All existing tests pass | Jest/pytest | Blocker |
| 5 | New code has tests | Coverage diff | Blocker |
| 6 | Coverage on new code >= 80% | Jest --coverage/pytest-cov | Blocker |
| 7 | Build succeeds | Language-specific | Blocker |
| 8 | PR title follows conventional commits | commitlint | Blocker |
| 9 | No secrets in code | Gitleaks | Blocker |
| 10 | No vulnerable dependencies | Snyk/Dependabot | Blocker |
| 11 | SAST — no critical/high findings | Semgrep | Blocker |
| 12 | IaC scan passes | Checkov | Blocker |
| 13 | PR size < 400 lines | GitHub Action | Warning |
| 14 | PR linked to issue | GitHub Action | Warning |
| 15 | No merge conflicts | GitHub native | Blocker |

### AI Review Checks (Advisory — 62 checks across 8 categories)

**Architecture & Design (10 checks):** Dependency direction, circular deps, SRP, ISP, DIP, God class, deep nesting, long params, fan-out, shotgun surgery.

**Code Quality (9 checks):** DRY (exact + near-match), KISS (single-impl interfaces, unnecessary async, pass-through), boolean blindness, data clumps, file size warnings, commented-out code.

**Security (10 checks):** Missing auth/authz, input validation, SQL injection, XSS, PII in logs, weak crypto, hardcoded credentials, error details exposed, rate limiting.

**Testing (5 checks):** Missing tests, happy-path-only tests, missing error path tests, missing integration tests, snapshot abuse.

**Performance (6 checks):** N+1 queries, missing pagination, blocking I/O, algorithmic complexity, memory leaks, large payloads.

**API Design (6 checks):** Resource naming, HTTP methods, status codes, error format, breaking changes, versioning.

**IaC (12 checks):** Hardcoded values, missing tags, wide security groups, missing encryption, state locking, secrets in vars, prevent_destroy, oversized modules, provider pinning, CIDR conflicts, layer violations, variable descriptions.

**Requirements (4 checks):** Issue linkage, acceptance criteria, scope creep, documentation updates.

## Severity System

| Level | Meaning | Action | Examples |
|-------|---------|--------|---------|
| **Blocker** | System failure, data loss, or security breach | Must fix before merge | Secrets, missing auth, SQL injection |
| **Critical** | Significant production issue | Fix before or immediately after merge | Wide security groups, N+1 queries, missing validation |
| **Major** | Maintenance/debt problem | Fix in PR or create ticket | SRP violations, DRY, hardcoded config |
| **Minor** | Style/convention issue | Fix if convenient | Boolean params, missing descriptions |
| **Info** | Observation | No action required | "File at 850/1000 lines" |

## Implementation Plan

```
Phase 1 (Day 1) ── Claude Code AI Review
  Configure .claude/agents/code-reviewer.md
  All 62 AI checks active, advisory mode
  Time: 1-2 hours

Phase 2 (Week 1) ── Core CI Gates
  Lint + Format + Type Check + Tests + Coverage + Secrets + Commits
  Time: 4-6 hours

Phase 3 (Week 2) ── Security CI Gates
  SAST (Semgrep) + SCA (Snyk/Dependabot) + IaC (Checkov)
  Time: 3-4 hours

Phase 4 (Week 3-4) ── Process Gates + Tuning
  PR size + issue linking + integration tests on main
  Tune false positive thresholds based on first 2 weeks
  Time: 2-3 hours + 1-2 hrs/week ongoing
```

**Total setup: ~12-16 hours over 4 weeks.** After that, ~1-2 hours/week for tuning.

## Key Insights

1. **Your initial 12 items were on the right track.** Industry standards validate every item on your list. The main gaps were: PR size limits, dependency scanning, error handling, performance checks, and IaC-specific checks.

2. **CI for deterministic checks, AI for judgment.** Lint, types, tests, and secrets should be blocking CI gates (zero tolerance). Architecture, SOLID, and design quality need AI judgment with confidence scoring.

3. **Confidence scoring prevents noise.** Following Uber's approach, every AI finding should include a confidence score. Only findings above 60-80% (depending on category) should be reported. This prevents the "boy who cried wolf" problem.

4. **Start advisory, graduate to blocking.** New checks start as informational. Promote to their target severity after 2-4 weeks of data showing low false positive rates (<20%).

5. **The checklist is a living document.** Add project-specific rules as patterns emerge. Remove checks that consistently produce false positives.

## Artifacts

```
.claude/docs/code-quality/research/automated-pr-review-checklist/
  discover-pass-1.md   — Landscape (industry checklists, tools, standards)
  discover-pass-2.md   — Evidence (MCDA scoring, automation confidence, severity system)
  discover-pass-3.md   — Recommendation (complete 77-check checklist, implementation plan)
  references.md        — 44 sources cited
  recommendation.md    — This file
```
