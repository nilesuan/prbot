---
description: "Automated PR review — 77-check quality gate across security, architecture, testing, performance, API design, IaC, and requirements"
allowed-tools: Bash(gh issue view:*), Bash(gh search:*), Bash(gh issue list:*), Bash(gh pr comment:*), Bash(gh pr diff:*), Bash(gh pr view:*), Bash(gh pr list:*), Bash(gh pr checks:*), Bash(git diff:*), Bash(git log:*), Bash(git show:*), Bash(git blame:*)
---

# /review-pr $ARGUMENTS

Automated pull request review with a 77-check quality gate. Posts structured findings to the PR.

**Input:** A GitHub pull request URL or number.
**Example:** `/review-pr https://github.com/owner/repo/pull/42` or `/review-pr 42`

---

## Step 1 — Parse Input and Validate PR

Extract from `$ARGUMENTS`:
- If a URL: parse `owner`, `repo`, and `pr_number`
- If a number: use the current repo context

Run eligibility checks. **Skip review if the PR is:**
- Closed or merged
- A draft
- Automated (bot-generated, Dependabot, Renovate)
- Already reviewed by this tool (check for existing "## PR Review" comment)

If ineligible, output `skipped — {reason}` and stop.

## Step 2 — Gather Context

Run these in parallel using `gh` commands:

1. **PR metadata** — `gh pr view {number} --json title,body,labels,baseRefName,headRefName,additions,deletions,changedFiles,commits`
2. **PR diff** — `gh pr diff {number}`
3. **Linked issues** — Extract issue references from PR body/title (e.g., `#123`, `closes #456`)
4. **CLAUDE.md files** — Read the root `CLAUDE.md` and any `CLAUDE.md` in directories touched by the PR

From the diff, compute:
- **Total lines changed** (additions + deletions)
- **Files changed count**
- **File types changed** (to determine which check categories apply)
- **Whether IaC files are in the diff** (`.tf`, `.toml` for OpenTofu)
- **Whether test files are in the diff**
- **Whether API route files are in the diff**

## Step 3 — Run CI-Equivalent Checks

These are deterministic checks the AI performs by analyzing the diff. Each produces a PASS/FAIL result.

### 3.1 Process Gates

| # | Check | How to Verify | Severity |
|---|-------|---------------|----------|
| 1 | **PR size** — Total lines changed | FAIL if >400 lines (warn >200) | Warning |
| 2 | **PR linked to issue** — References an issue number | Check PR body/title for `#NNN`, `closes`, `fixes`, `resolves` | Warning |
| 3 | **Conventional commit title** — PR title matches `type(scope): description` | Regex: `^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)(\(.+\))?: .+` | Minor |

### 3.2 Security Gates (scan the diff)

| # | Check | How to Verify | Severity |
|---|-------|---------------|----------|
| 4 | **No secrets in diff** — API keys, passwords, tokens, connection strings | Scan for patterns: API key assignments, AWS key patterns `AKIA[0-9A-Z]{16}`, JWT patterns, base64-encoded secrets >40 chars | Blocker |
| 5 | **No PII in logs** — Logging statements containing email, phone, IP, name fields | Scan for logging calls referencing sensitive field names (email, phone, ip_addr, password, ssn, credit_card) | Critical |
| 6 | **No hardcoded credentials in IaC** — Default variable values with secrets | In `.tf` files: default values in variable blocks near password/secret/key/token names | Blocker |

### 3.3 Test Gates (scan the diff)

| # | Check | How to Verify | Severity |
|---|-------|---------------|----------|
| 7 | **New code has tests** — New exported functions/classes have corresponding test files | For each new function/class in the diff, check if a test file exists or was added | Blocker |
| 8 | **No test files deleted without code deletion** — Tests removed but implementation kept | Cross-reference deleted test files against remaining source files | Critical |

## Step 4 — Launch Parallel AI Review Agents

Launch 5 parallel agents. Each agent receives the full PR diff, PR summary, linked issue context, and relevant CLAUDE.md content.

### Agent 1: Architecture & Design Review (Sonnet)

Review the diff for architecture and design quality violations:

**Checks (10):**
- [ ] **[A1]** Dependency direction — Domain layer must not import from infrastructure. Scan imports in each file, map to architectural layers based on directory structure. (Critical, High confidence)
- [ ] **[A2]** Circular dependencies — No module import cycles introduced by this PR. (Major, High confidence)
- [ ] **[A3]** SRP — Flag classes/files >500 lines, >15 methods, or importing from >10 distinct modules. (Major, Medium confidence)
- [ ] **[A4]** ISP — Interfaces with >10 methods or implementations where >30% are no-ops/stubs. (Major, High confidence for TS)
- [ ] **[A5]** DIP — Business logic directly instantiating infrastructure (e.g., `new S3Client()`, `new DynamoDBClient()` in service files). (Critical, High confidence)
- [ ] **[A6]** God class — >500 lines AND >15 methods AND imports from 4+ domain areas. (Major, Medium confidence)
- [ ] **[A7]** Deep nesting — >4 levels of control flow indentation. Suggest early returns/guard clauses. (Major, High confidence)
- [ ] **[A8]** Long parameter lists — Functions with >5 parameters (excluding options/config objects). (Major, High confidence)
- [ ] **[A9]** Fan-out — File importing from >10 distinct modules. (Major, Medium confidence)
- [ ] **[A10]** Shotgun surgery — Single logical change touching >5 files across >3 directories. (Major, Medium confidence)

**Context-aware suppression:**
- Do NOT flag high fan-out in composition roots, DI containers, route registration, or barrel/index files.
- Do NOT flag single-implementation interfaces if a test mock exists or the file is in a ports/adapters directory.
- Do NOT flag similar code across different provider adapters (e.g., `twilio-adapter.ts` vs `vonage-adapter.ts`) as duplication.

### Agent 2: Code Quality & Patterns Review (Sonnet)

Review the diff for code quality, DRY, and KISS violations:

**Checks (9):**
- [ ] **[Q1]** DRY (exact) — >10 lines of identical or near-identical code within the PR. (Major, Medium confidence)
- [ ] **[Q2]** DRY (near-match) — >80% structurally similar code blocks >10 lines. (Minor, Medium confidence)
- [ ] **[Q3]** KISS (single-impl) — Interface/abstract class with exactly one concrete implementation and no test mock. Frame as question, not violation. (Info, Medium confidence)
- [ ] **[Q4]** KISS (unnecessary async) — `async` function that never `await`s. (Minor, High confidence)
- [ ] **[Q5]** KISS (pass-through) — Function whose only job is calling another function with the same args. (Minor, High confidence)
- [ ] **[Q6]** Boolean blindness — Functions with 2+ boolean parameters. Suggest options object or named constants. (Minor, High confidence)
- [ ] **[Q7]** Data clumps — Same group of 3+ parameters passed together across multiple functions. (Minor, Medium confidence)
- [ ] **[Q8]** File size — File exceeding 800 lines (warn) or 1000 lines (fail, per CLAUDE.md). (Major at 1000, Info at 800, High confidence)
- [ ] **[Q9]** Commented-out code — Blocks of commented-out code (>3 lines). (Minor, High confidence)

### Agent 3: Security & Input Validation Review (Opus)

Deep security review of the diff:

**Checks (10):**
- [ ] **[S1]** Missing authentication — Route/endpoint handler without auth middleware. (Blocker, High confidence)
- [ ] **[S2]** Missing authorization — Endpoint without role/permission check where data is user-scoped. (Critical, Medium confidence)
- [ ] **[S3]** Input validation — Endpoint accepting user input without schema validation (Zod, Joi, Pydantic, class-validator). (Critical, High confidence)
- [ ] **[S4]** SQL injection — String concatenation or template literals in database queries with user input. (Blocker, High confidence)
- [ ] **[S5]** XSS — Unescaped user input rendered in HTML responses. (Blocker, High confidence)
- [ ] **[S6]** Weak cryptography — Use of MD5, SHA-1, DES, 3DES, RC4 for security purposes. (Critical, High confidence)
- [ ] **[S7]** Error details exposed — Internal error messages, stack traces, or system info returned to clients. (Major, Medium confidence)
- [ ] **[S8]** Missing rate limiting — Public-facing endpoint without rate limit middleware. (Major, Medium confidence)
- [ ] **[S9]** Insecure deserialization — Unsafe parsing of untrusted input without validation, dynamic code execution, or unsafe serialization libraries on untrusted data. (Blocker, High confidence)
- [ ] **[S10]** SSRF — User-controlled URLs passed to HTTP clients without allowlist validation. (Critical, High confidence)

### Agent 4: Testing & Performance Review (Sonnet)

Review test adequacy and performance patterns:

**Testing checks (5):**
- [ ] **[T1]** New code without tests — New exported functions/classes with no corresponding test. (Blocker, Medium confidence)
- [ ] **[T2]** Happy-path-only tests — Tests that only cover the success case with no error/edge case tests. (Major, Medium confidence)
- [ ] **[T3]** Missing error path tests — Try/catch blocks without tests for the catch path. (Major, Medium confidence)
- [ ] **[T4]** Missing integration tests for new endpoints — New API routes without integration test. (Major, Medium confidence)
- [ ] **[T5]** Snapshot test abuse — Large snapshot tests for logic that should have assertion-based tests. (Minor, Medium confidence)

**Performance checks (6):**
- [ ] **[P1]** N+1 queries — Database/API calls inside loops. (Critical, Medium confidence)
- [ ] **[P2]** Missing pagination — List endpoint returning unbounded results without limit/offset or cursor. (Major, Medium confidence)
- [ ] **[P3]** Blocking I/O — Synchronous file/network operations in async context. (Major, Medium confidence)
- [ ] **[P4]** Algorithmic complexity — Nested loops (O(n^2)+) iterating over potentially large datasets. (Major, Medium confidence)
- [ ] **[P5]** Memory leaks — Unclosed resources (streams, connections, file handles), event listeners not removed, growing collections without bounds. (Critical, Medium confidence)
- [ ] **[P6]** Large payload — Unbounded serialization or response without size/count limits. (Major, Medium confidence)

### Agent 5: API Design & IaC Review (Sonnet)

Review API conventions and infrastructure code:

**API checks (6) — only if API route files are in the diff:**
- [ ] **[D1]** Resource naming — Endpoints should use plural nouns, no verbs in URL path. (Major, High confidence)
- [ ] **[D2]** HTTP method correctness — POST for create, GET for read, PUT/PATCH for update, DELETE for delete. (Major, High confidence)
- [ ] **[D3]** Status code correctness — 201 for creation, 204 for deletion, 404 for not found, etc. (Major, High confidence)
- [ ] **[D4]** Error response format — Consistent error shape matching project convention. (Major, Medium confidence)
- [ ] **[D5]** Breaking changes — Removing or renaming response fields without API versioning. (Blocker, High confidence)
- [ ] **[D6]** Versioning — New endpoints under versioned prefix (e.g., `/v1/`). (Major, High confidence)

**IaC checks (12) — only if `.tf` files are in the diff:**
- [ ] **[I1]** Hardcoded values — Literals instead of variables in resource configurations. (Major, High confidence)
- [ ] **[I2]** Missing tags — AWS resources without Name, Environment, Project, ManagedBy tags. (Major, High confidence)
- [ ] **[I3]** Wide security groups — Ingress `0.0.0.0/0` on non-443/80 ports. (Critical, High confidence)
- [ ] **[I4]** Missing encryption — S3 buckets, RDS instances, EBS volumes without encryption. (Critical, High confidence)
- [ ] **[I5]** No state locking — Backend config without DynamoDB lock table. (Blocker, High confidence)
- [ ] **[I6]** Secrets in variable defaults — Default values containing passwords/keys/tokens. (Blocker, High confidence)
- [ ] **[I7]** Missing `prevent_destroy` — Stateful resources (RDS, S3 with data) without lifecycle protection. (Critical, High confidence)
- [ ] **[I8]** Oversized modules — Module >500 lines or >15 resources. (Major, High confidence)
- [ ] **[I9]** Provider version unpinned — `required_providers` without version constraints. (Major, High confidence)
- [ ] **[I10]** CIDR conflicts — Subnets not following the project's documented CIDR scheme. (Critical, High confidence)
- [ ] **[I11]** Layer violations — Platform module directly referencing management layer resources without data sources. (Major, High confidence)
- [ ] **[I12]** Missing variable descriptions — Variables without `description` field. (Minor, High confidence)

**Requirements checks (4):**
- [ ] **[R1]** PR linked to issue — Changes should reference an issue/ticket. (Major, High confidence)
- [ ] **[R2]** Scope creep — PR modifies files clearly unrelated to the linked issue or PR description. (Major, Medium confidence)
- [ ] **[R3]** Acceptance criteria — If linked issue has AC, verify the diff addresses them. (Info, Low confidence)
- [ ] **[R4]** Documentation — Behavior changes should include doc updates (README, API docs, inline comments on public APIs). (Minor, Medium confidence)

## Step 5 — Score and Validate Findings

### 5.1 Confidence Scoring

Each agent returns findings with a confidence score (0-100):

| Range | Label | Action |
|-------|-------|--------|
| 90-100 | Definite | Report. Auto-block if Blocker severity. |
| 70-89 | Likely | Report. Human confirms. |
| 50-69 | Possible | Report as suggestion only. |
| <50 | Low | **Discard. Do not report.** |

### 5.2 Validation Pass

For each finding scored >= 50, launch a parallel Haiku validation agent that:

1. Re-reads the specific code referenced by the finding
2. Checks if the finding is a false positive using these criteria:
   - **Pre-existing issue** (code existed before this PR) — discard
   - **Intentional design** (suppression comment like `// @review-ignore:DRY`) — discard
   - **Linter/compiler would catch it** (type errors, formatting) — discard
   - **Generated/vendored code** — discard
   - **Issue is on lines NOT modified in this PR** — discard
3. Returns a revised confidence score

**Filter threshold: Only include findings with validated confidence >= 70.**

Exception: Blockers (secrets, SQL injection, missing auth) are included at >= 50 confidence.

### 5.3 Scoring

Start at 100. Apply deductions:

| Finding Severity | Deduction Per Instance | Max Deduction |
|-----------------|----------------------|---------------|
| Blocker | -25 | -50 |
| Critical | -15 | -45 |
| Major | -5 | -30 |
| Minor | -2 | -10 |
| Info | 0 | 0 |

**Verdicts:**

| Score | Verdict | Action |
|-------|---------|--------|
| 85-100 | **APPROVE** | Approve the PR |
| 70-84 | **APPROVE WITH COMMENTS** | Approve but post findings |
| 50-69 | **REQUEST CHANGES** | Do not approve, post findings |
| 0-49 | **REJECT** | Do not approve, post findings |

## Step 6 — Post Review Comment

Use `gh pr comment {number} --body` to post the review. Use this exact format:

```markdown
## PR Review

**Score:** {score}/100 | **Verdict:** {verdict}
**Checks passed:** {pass_count}/77 | **Findings:** {blocker_count} blockers, {critical_count} critical, {major_count} major, {minor_count} minor

---

{IF findings exist}

### Blockers (must fix before merge)

{For each blocker finding:}
1. **[{id}]** {description} — [{file}:{line}]({link_to_file_with_full_sha}#L{start}-L{end}) (confidence: {score}%)
   > {1-line explanation of why this is a problem and how to fix it}

### Critical (should fix before merge)

{For each critical finding:}
1. **[{id}]** {description} — [{file}:{line}]({link_to_file_with_full_sha}#L{start}-L{end}) (confidence: {score}%)
   > {1-line explanation}

### Major (fix or create follow-up)

{For each major finding:}
1. **[{id}]** {description} — [{file}:{line}]({link_to_file_with_full_sha}#L{start}-L{end}) (confidence: {score}%)

### Minor

{For each minor finding:}
- **[{id}]** {description} — [{file}:{line}]({link_to_file_with_full_sha}#L{start}-L{end})

{END IF}

{IF no findings}
No issues found. Checked for security, architecture, testing, performance, API design, and IaC compliance.
{END IF}

### Checklist Summary

| Category | Status | Checks |
|----------|--------|--------|
| Process | {pass/fail} | PR size, issue link, commit format |
| Security | {pass/fail} | Auth, secrets, validation, injection, PII |
| Architecture | {pass/fail} | Layers, SOLID, coupling, nesting |
| Code Quality | {pass/fail} | DRY, KISS, file size, patterns |
| Testing | {pass/fail} | Coverage, edge cases, error paths |
| Performance | {pass/fail} | N+1, pagination, blocking I/O |
| API Design | {pass/fail or N/A} | Naming, methods, status codes, versioning |
| IaC | {pass/fail or N/A} | Tags, encryption, security groups, state |
| Requirements | {pass/fail} | Issue link, scope, AC, docs |
```

**Link format:** Use full git SHA in links. Get SHA via `gh pr view {number} --json headRefOid -q .headRefOid`. Format: `https://github.com/{owner}/{repo}/blob/{sha}/{file}#L{start}-L{end}`

## Step 7 — Final Output

After posting the comment, output a single summary line:

```
{verdict} ({score}/100) — {findings_count} findings ({blocker_count}B {critical_count}C {major_count}M {minor_count}m) — PR #{number}
```

Examples:
- `APPROVE (92/100) — 2 findings (0B 0C 1M 1m) — PR #42`
- `REQUEST CHANGES (58/100) — 7 findings (1B 2C 3M 1m) — PR #42`
- `APPROVE (100/100) — 0 findings — PR #42`

---

## Configuration

This command respects project-level configuration in `CLAUDE.md`. Override defaults by adding a `## PR Review Config` section:

```markdown
## PR Review Config
- max-file-lines: 1000
- max-function-params: 5
- max-nesting-depth: 4
- max-fan-out: 10
- coverage-threshold-new-code: 80
- pr-size-warning: 200
- pr-size-limit: 400
- confidence-threshold: 70
- blocker-confidence-threshold: 50
- architecture-layers:
  - domain: src/domain/**
  - application: src/application/**
  - infrastructure: src/infrastructure/**
- forbidden-imports:
  - domain: [src/infrastructure/**, @aws-sdk/**]
- required-tags: [Name, Environment, Project, ManagedBy]
- suppression-comment: @review-ignore
```

## Severity Reference

| Level | Meaning | PR Impact |
|-------|---------|-----------|
| **Blocker** | System failure, data loss, security breach | -25 each, blocks approval |
| **Critical** | Significant production issue | -15 each, should fix before merge |
| **Major** | Maintenance/debt problem | -5 each, fix or ticket |
| **Minor** | Style/convention issue | -2 each, fix if convenient |
| **Info** | Observation | No deduction, no action needed |
