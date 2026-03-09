# Discovery Pass 3 of 3

## Pass Focus
**Recommendation** — "Can we use it?" SWOT analysis, go/no-go decision, the complete PR review checklist, and implementation guidance.

## SWOT Analysis

### Hybrid Approach: CI Tooling + Claude Code AI Review (MCDA Winner: 81.25)

| | Helpful | Harmful |
|---|---|---|
| **Internal** | **Strengths** | **Weaknesses** |
| | Best detection accuracy (CI catches deterministic, AI catches judgment) | Most complex to set up (CI pipeline + AI review config) |
| | Lowest overall false positive rate (each layer tuned to its strengths) | Two systems to maintain |
| | Covers all 12 check categories | CI tools have licensing/pricing for advanced features |
| | CI gates are fast and blocking; AI review is thorough and advisory | AI review adds 2-5 minutes to PR cycle |
| | Confidence scoring reduces noise from AI findings | Requires tuning thresholds for project context |
| **External** | **Opportunities** | **Threats** |
| | Claude Code's review architecture is actively improving | AI model changes could shift detection accuracy |
| | CI tool ecosystem is mature and well-documented | Tool fragmentation (many tools, each covering a slice) |
| | Can start with AI-only and add CI incrementally | Over-tooling risk — too many gates slows velocity |

### AI-Only Approach: Claude Code Review (MCDA: 75.00)

| | Helpful | Harmful |
|---|---|---|
| **Internal** | **Strengths** | **Weaknesses** |
| | Fastest to implement (configure CLAUDE.md + checklist) | Higher false positive rate on deterministic checks |
| | Broadest coverage from a single tool | No blocking CI gates (advisory only by default) |
| | Adapts to project-specific patterns via CLAUDE.md | Slower than dedicated linters (2-5 min per review) |
| | Can be upgraded to hybrid incrementally | Non-deterministic — may miss issues on re-runs |
| **External** | **Opportunities** | **Threats** |
| | Growing plugin ecosystem for Claude Code | API costs scale with PR volume |
| | GitHub Actions integration already available | Dependency on Anthropic API availability |

### CI Tooling Only (MCDA: 70.75)

| | Helpful | Harmful |
|---|---|---|
| **Internal** | **Strengths** | **Weaknesses** |
| | Deterministic and reproducible | Cannot assess design quality, SOLID, architecture |
| | Fastest execution (seconds, not minutes) | Rigid — each new check requires tool configuration |
| | Lowest false positive rate | Poor at understanding intent and context |
| | Industry-standard tooling with large communities | Many tools needed to cover all categories |
| **External** | **Opportunities** | **Threats** |
| | Well-understood DevOps practice | Tool sprawl increases maintenance burden |
| | Free tiers cover solo founder needs | Configuration drift across repos |

## Go/No-Go Recommendation

### Primary Recommendation: Phased Hybrid (Start AI-Only, Add CI Incrementally)

**GO** — with phased implementation.

**Rationale for a solo founder:**

1. **Start with Claude Code AI review** — Configure a comprehensive checklist in `.claude/agents/code-reviewer.md`. This gives you coverage across ALL 12 categories from day one with zero CI infrastructure. Time: 1-2 hours.

2. **Add blocking CI gates incrementally** — Start with the highest-ROI deterministic checks (lint, type check, secret scan, tests) as GitHub Actions. Add one tool per week. Time: 2-4 hours per tool.

3. **The AI review catches what CI misses** — Architecture violations, design quality, SOLID principles, performance patterns, and requirements compliance cannot be caught by static analysis alone.

4. **The CI gates catch what AI might miss** — Deterministic checks (lint, types, secrets) should never be advisory. They should block merges automatically.

### The Complete Checklist

Organized by enforcement mechanism and check timing.

---

## THE CHECKLIST

### Section 1: CI Pipeline Gates (Blocking — Must Pass)

These run automatically on every PR. No human judgment needed.

#### 1.1 Code Quality Gates

| # | Check | Tool | Trigger | Severity |
|---|-------|------|---------|----------|
| 1 | Lint passes (zero errors) | ESLint/Ruff | Every PR | Blocker |
| 2 | Code formatting correct | Prettier/Black | Every PR | Blocker |
| 3 | Type check passes (zero errors) | tsc/mypy | Every PR | Blocker |
| 4 | All existing tests pass | Jest/pytest | Every PR | Blocker |
| 5 | New code has tests | Coverage diff tool | Every PR | Blocker |
| 6 | Coverage on new code >= 80% | Jest --coverage/pytest-cov | Every PR | Blocker |
| 7 | Build succeeds | Language-specific | Every PR | Blocker |
| 8 | PR title follows conventional commits | commitlint/action-semantic-pull-request | Every PR | Blocker |

#### 1.2 Security Gates

| # | Check | Tool | Trigger | Severity |
|---|-------|------|---------|----------|
| 9 | No secrets in code | Gitleaks | Every PR | Blocker |
| 10 | No known vulnerable dependencies | Snyk/Dependabot | Every PR (dep changes) | Blocker (critical/high) |
| 11 | SAST — no critical/high findings | Semgrep | Every PR | Blocker (critical), Warning (medium) |
| 12 | IaC scan passes | Checkov/tfsec | PRs with .tf files | Blocker (critical/high) |

#### 1.3 Process Gates

| # | Check | Tool | Trigger | Severity |
|---|-------|------|---------|----------|
| 13 | PR size < 400 lines | GitHub Action | Every PR | Warning (>400), Info (>200) |
| 14 | PR linked to issue | GitHub Action | Every PR | Warning |
| 15 | No merge conflicts | GitHub native | Every PR | Blocker |

### Section 2: AI Review Checks (Advisory — Claude Code)

These are evaluated by Claude Code's AI review agent. Findings are posted as PR comments with confidence scores.

#### 2.1 Architecture & Design (Confidence threshold: 70%)

| # | Check | Confidence | Severity |
|---|-------|------------|----------|
| 16 | **Dependency direction**: Domain layer must not import from infrastructure | High | Critical |
| 17 | **Circular dependencies**: No module import cycles | High | Major |
| 18 | **SRP**: Class/file should have a single reason to change (<500 lines, <15 methods, <10 imports from distinct domains) | Medium | Major |
| 19 | **ISP**: Interfaces should be focused (<10 methods; no >30% no-op implementations) | High (TS) | Major |
| 20 | **DIP**: Business logic must not directly instantiate infrastructure (no `new S3Client()` in services) | High | Critical |
| 21 | **God class**: >500 lines + >15 methods + imports from 4+ domain areas | Medium | Major |
| 22 | **Deep nesting**: >4 levels of control flow indentation | High | Major |
| 23 | **Long parameter lists**: >5 parameters without options object | High | Major |
| 24 | **Fan-out**: File importing from >10 distinct modules | Medium | Major |
| 25 | **Shotgun surgery**: Single feature change touching >5 files across >3 directories | Medium | Major |

#### 2.2 Code Quality (Confidence threshold: 70%)

| # | Check | Confidence | Severity |
|---|-------|------------|----------|
| 26 | **DRY**: Exact duplication >10 lines within PR | Medium | Major |
| 27 | **DRY**: Near-match (>80% similar) >10 lines | Medium | Minor |
| 28 | **KISS**: Single-implementation interface with no test mock | Medium | Info |
| 29 | **KISS**: Unnecessary async (async function that never awaits) | High | Minor |
| 30 | **KISS**: Pass-through functions (function only calls another function) | High | Minor |
| 31 | **Boolean blindness**: Functions with 2+ boolean parameters | High | Minor |
| 32 | **Data clumps**: Same 3+ parameters passed together across functions | Medium | Minor |
| 33 | **File approaching limit**: >800/1000 lines | High | Info |
| 34 | **Commented-out code**: Blocks of commented-out code | High | Minor |

#### 2.3 Security (Confidence threshold: 80%)

| # | Check | Confidence | Severity |
|---|-------|------------|----------|
| 35 | **Missing auth**: Route handler without authentication middleware | High | Blocker |
| 36 | **Missing authorization**: Endpoint without role/permission check | Medium | Critical |
| 37 | **Input validation**: Endpoint without schema validation (Zod/Joi/Pydantic) | High | Critical |
| 38 | **SQL injection**: Unsanitized user input in database queries | High | Blocker |
| 39 | **XSS**: Unescaped user input in HTML output | High | Blocker |
| 40 | **PII in logs**: Logging user data (email, phone, IP, names) without redaction | Medium | Critical |
| 41 | **Weak cryptography**: Use of MD5, SHA-1, DES, 3DES, RC4 | High | Critical |
| 42 | **Hardcoded credentials**: Passwords, API keys as string literals | High | Blocker |
| 43 | **Error details exposed**: Internal error messages returned to clients | Medium | Major |
| 44 | **Missing rate limiting**: Public endpoint without rate limit middleware | Medium | Major |

#### 2.4 Testing (Confidence threshold: 70%)

| # | Check | Confidence | Severity |
|---|-------|------------|----------|
| 45 | **New code without tests**: New functions/classes with no test file | Medium | Blocker |
| 46 | **Test quality**: Tests only check happy path, no edge cases | Medium | Major |
| 47 | **Missing error path tests**: Try/catch blocks without tests for the catch path | Medium | Major |
| 48 | **Integration test for new endpoints**: New API endpoints without integration tests | Medium | Major |
| 49 | **Snapshot test abuse**: Overuse of snapshot tests for logic that should have assertions | Medium | Minor |

#### 2.5 Performance (Confidence threshold: 60%)

| # | Check | Confidence | Severity |
|---|-------|------------|----------|
| 50 | **N+1 queries**: Database calls inside loops | Medium | Critical |
| 51 | **Missing pagination**: List endpoint returning unbounded results | Medium | Major |
| 52 | **Blocking I/O**: Synchronous calls in async context | Medium | Major |
| 53 | **Algorithmic complexity**: O(n^2) nested loops on potentially large datasets | Medium | Major |
| 54 | **Memory leaks**: Unclosed resources, growing collections without bounds | Medium | Critical |
| 55 | **Large payload**: Unbounded serialization without size limits | Medium | Major |

#### 2.6 API Design (Confidence threshold: 70%)

| # | Check | Confidence | Severity |
|---|-------|------------|----------|
| 56 | **Resource naming**: Endpoints using plural nouns, no verbs in URL | High | Major |
| 57 | **HTTP method correctness**: POST for create, GET for read, etc. | High | Major |
| 58 | **Status code correctness**: 201 for creation, 204 for deletion, etc. | High | Major |
| 59 | **Error response format**: Consistent error shape `{ error: { code, message } }` | Medium | Major |
| 60 | **Breaking changes**: Removing/renaming response fields without versioning | High | Blocker |
| 61 | **Versioning**: New endpoints under `/v1/` prefix | High | Major |

#### 2.7 Infrastructure as Code (Confidence threshold: 80%)

| # | Check | Confidence | Severity |
|---|-------|------------|----------|
| 62 | **Hardcoded values**: Literals instead of variables | High | Major |
| 63 | **Missing tags**: Resources without Name/Environment/Project/ManagedBy | High | Major |
| 64 | **Wide security groups**: 0.0.0.0/0 on non-public ports | High | Critical |
| 65 | **Missing encryption**: S3/RDS/EBS without encryption | High | Critical |
| 66 | **No state locking**: Backend without DynamoDB lock table | High | Blocker |
| 67 | **Secrets in variables**: Default values containing credentials | High | Blocker |
| 68 | **Missing `prevent_destroy`**: Stateful resources without lifecycle protection | High | Critical |
| 69 | **Oversized modules**: Module >500 lines or >15 resources | High | Major |
| 70 | **Provider version unpinned**: Missing version constraints | High | Major |
| 71 | **CIDR conflicts**: Subnets not following documented scheme | High | Critical |
| 72 | **Layer violations**: Platform module referencing management resources directly | High | Major |
| 73 | **Missing variable descriptions**: Variables without `description` | High | Minor |

#### 2.8 Requirements Compliance (Confidence threshold: 60%)

| # | Check | Confidence | Severity |
|---|-------|------------|----------|
| 74 | **PR linked to issue**: Changes should reference an issue/ticket | High | Major |
| 75 | **Acceptance criteria met**: If issue has AC, changes should address them | Low | Info |
| 76 | **Scope creep**: PR changes files unrelated to the linked issue | Medium | Major |
| 77 | **Documentation updated**: Behavior changes should update relevant docs | Medium | Minor |

---

### Implementation Phases

```
Phase 1 (Day 1): Claude Code AI Review Only
  → Create .claude/agents/code-reviewer.md with full checklist
  → Configure via GitHub Actions (claude-code-action)
  → All checks are advisory (comment on PR)
  → Time: 1-2 hours

Phase 2 (Week 1): Add Core CI Gates
  → Lint + Format (ESLint/Prettier or Ruff/Black)
  → Type check (tsc / mypy)
  → Unit tests + coverage gate (80% new code)
  → Secret scan (Gitleaks)
  → Conventional commit check
  → Time: 4-6 hours

Phase 3 (Week 2): Add Security CI Gates
  → SAST (Semgrep — free, fast, extensible)
  → SCA (Snyk free tier or Dependabot)
  → IaC scan (Checkov — free, OpenTofu compatible)
  → Time: 3-4 hours

Phase 4 (Week 3-4): Add Process Gates
  → PR size check
  → PR-to-issue linking
  → Integration tests on merge to main
  → Time: 2-3 hours

Phase 5 (Ongoing): Tune and Optimize
  → Track false positive rates per check
  → Promote/demote severity levels based on data
  → Add project-specific rules to CLAUDE.md
  → Time: 1-2 hours/week
```

### Review Output Format

The AI reviewer should produce structured output:

```markdown
## PR Review: #{pr_number} — {title}

### Summary
{1-2 sentence description of what the PR does}

### Verdict: {APPROVE | REQUEST_CHANGES | COMMENT}

### Findings

#### Blockers (must fix)
- [ ] **[B1]** {description} — {file}:{line} (confidence: {score}%)

#### Critical (should fix before merge)
- [ ] **[C1]** {description} — {file}:{line} (confidence: {score}%)

#### Major (fix or create follow-up)
- [ ] **[M1]** {description} — {file}:{line} (confidence: {score}%)

#### Minor (fix if convenient)
- [ ] **[m1]** {description} — {file}:{line} (confidence: {score}%)

#### Info (observations)
- **[i1]** {description}

### Checklist
- [x] No secrets detected
- [x] Tests present for new code
- [x] Coverage >= 80% on new lines
- [x] No breaking API changes
- [ ] Architecture layer violations found (see C1)
...
```

## Score

| Criterion | Score (0-100) | Justification |
|-----------|---------------|---------------|
| Source quality | 90 | Industry checklists from Google, Microsoft, Uber; OWASP security standards |
| Coverage breadth | 95 | 77 checks across 8 categories with severity and confidence classification |
| Evidence strength | 87 | Automation confidence backed by systems architect analysis; FP rates estimated |
| Actionability | 94 | Complete checklist with implementation phases, output format, and tool recommendations |
| **Overall** | **92** | Production-ready PR review checklist with phased implementation plan |

## Pass Gate
- [x] All findings have source citations (via Passes 1-2)
- [x] Score >= threshold (92 >= 80)
- [x] Final pass complete? YES
