# Evidence record

Every claim in `findings.md` with its source. Split into what was verified
directly in this session and what was not.

---

## A. Verified: the merge request

Source: GitLab API, project 220.

| Field | Value |
|-------|-------|
| URL | https://gitlab.padua.net.au/infrastructure/infrastructure-core/-/merge_requests/194 |
| Title | `IOPS-1479: IAM Access Analyzer coverage in the two missing usable regions` |
| Author | `nile.suan` |
| Created | `2026-09-16T10:18:49.014+10:00` |
| Merged | `2026-09-16T12:41:14.410+10:00` |
| Merge commit | `39d2fd049ad77dfc7efb955de76e76af8a43d279` |
| Squash commit | `f9c24f419032db90ec6d7dce8271fb4fa0c3c544` |
| Files changed | 5 (`.gitlab-ci.yml`, `access-analyzer-import.tf`, `main.tf`, `iops_1479_access_analyzer.tftest.hcl`, `variables.tf`) |

Commit timeline, from `git log -1 --format='%H %ci %s'`:

| SHA | Committed | Role |
|-----|-----------|------|
| `eaa3480b1213efcc17634db6d01b50ab290b76a2` | 2026-09-16 10:09:51 +1000 | original |
| `1db1d3a7f2ea11f2546a34e2e1594afb923bd9b4` | 2026-09-16 11:35:09 +1000 | fix round 1 |
| `4d340117f7a322425b8b19a4edb9e285b8b67a9b` | 2026-09-16 12:24:41 +1000 | original, rebased |
| `9b67058cb46382837409cdf7e8da6b6f9a93c406` | 2026-09-16 12:36:16 +1000 | final |

---

## B. Verified: the three prbot runs

Source: job traces `projects/220/jobs/{574566,574683,575134}/trace`, the
`review.audit` JSON line in each.

| Job | Pipeline | head_sha | verdict | score | reported | borderline | hidden | cost USD |
|-----|----------|----------|---------|-------|----------|------------|--------|----------|
| 574566 | 123238 | `eaa3480b` | APPROVE | 100 | 0 | 0 | 0 | 0.26931400000000005 |
| 574683 | 123250 | `1db1d3a7` | APPROVE | 100 | 0 | 0 | 0 | 0.353878 |
| 575134 | 123263 | `9b67058c` | COMMENT | 100 | 0 | 1 | 3 | 0.35682800000000003 |

Per-agent, same source:

| Job | Agent | finding_count | input_tokens | output_tokens | latency_ms |
|-----|-------|---------------|--------------|---------------|------------|
| 574566 | general | 0 | 63721 | 1505 | 19447 |
| 574566 | security | 0 | 63241 | **34** | 3320 |
| 574683 | general | 0 | 85567 | 1249 | 17063 |
| 574683 | security | 0 | 85087 | **8** | 4371 |
| 575134 | general | 4 | 85507 | 1542 | 18278 |
| 575134 | security | 0 | 85027 | **34** | 4773 |

`confidence_threshold: 70` and `blocker_threshold: 70` in all three audit
records. `model_id` was `au.anthropic.claude-sonnet-5` for every agent on every
run. All three runs recorded `status: "success"` and `agent_errors: 0`, so no
agent crashed or timed out.

Verbatim log line, job 574566:

```
{"event": "verdict=APPROVE score=100 findings=0 hidden=0",
 "commit_sha": "eaa3480b1213efcc17634db6d01b50ab290b76a2", ...}
```

The diff prbot received on that run: `{"event": "diff.filtered files=4→4"}`.

### Comment lifecycle

prbot posts one note and edits it in place. Note `112179` was created
`2026-09-16T10:19:42.834+10:00` and last updated `2026-09-16T12:37:01.265+10:00`.
Only the final state is visible through the API; the two APPROVE bodies were
overwritten. The audit records in the job traces are the only surviving record
of them.

---

## C. Verified: the comparison review

Source: MR notes `112184` through `112197`, author `nile.suan`, posted
`2026-09-16T10:51:06` to `10:52:18`, against `eaa3480b`.

Header: `**Score:** 64.93/100`, `**Verdict:** REQUEST CHANGES`, 13 findings.

| ID | Severity marker | Confidence |
|----|-----------------|-----------|
| SPEC-AC-01 | 🟡 | 71% |
| SEC-IAC-05 | ⚪ info (verification result, not a defect) | 71% |
| SEC-IAC-02 | 🟡 | 64% |
| SPEC-AC-04 | 🟡 | 60% |
| SPEC-TRACE-02 | 🟡 | 60% |
| SEC-IAC-03 | 🔵 | 54% |
| SPEC-TRACE-01 | 🟡 | 52% |
| SEC-IAC-04 | 🔵 | 45% |
| GEN-API-04 (tautological test) | 🔵 | 45% |
| SPEC-DRIFT-02 | 🔵 | 41% |
| GEN-API-04 (tags vs 0-diff) | 🔵 | 41% |
| **SEC-IAC-01** | 🟡 | **38%** |
| GEN-API-04 (gitignored evidence path) | 🔵 | 22% |

Five of the thirteen (`SPEC-*`) are specification and traceability findings, a
category prbot has no agent for.

### The findings were accepted and fixed

`git grep -n -E 'Review finding (SEC-IAC|GEN-API|SPEC)' 39d2fd0` against the
merged tree:

```
layers/management/tests/iops_1479_access_analyzer.tftest.hcl:554:# Review finding GEN-API-04-1: run block 2 above
layers/management/tests/iops_1479_access_analyzer.tftest.hcl:593:# Review finding SEC-IAC-01: aws_accessanalyzer_analyzer.unused_access_apse2
layers/management/tests/iops_1479_access_analyzer.tftest.hcl:640:# Review finding SEC-IAC-02: the two apse2 adoption targets had no lifecycle
layers/management/tests/iops_1479_access_analyzer.tftest.hcl:670:# Review finding SEC-IAC-04: aws_accessanalyzer_analyzer.external_access_apse2
```

### One finding is a false positive

`SPEC-DRIFT-02` claimed no Jira comment recorded the scope reinterpretation.
Jira comment `139778` on IOPS-1479 was created `2026-09-16T10:19:35.437+1000`;
the finding was posted `10:52:09`. The comment predates the finding by about 33
minutes. The finding disclosed its own blind spot in its text.

---

## D. Verified: the defect was real (controlled experiment)

Full reproduction in `repro/`. Method:

1. Read the resource shape from `eaa3480b` directly:

```
$ git show eaa3480b:layers/management/main.tf | sed -n '2650,2660p'
resource "aws_accessanalyzer_analyzer" "unused_access_apse2" {
  count    = var.account_name == "paduafg" ? 1 : 0
  provider = aws.management

  analyzer_name = "UnusedAccess"
  type          = "ORGANIZATION_UNUSED_ACCESS"

  tags = {
    Component = "AccessAnalyzer"
  }
}
```

No `configuration` block. No `lifecycle` block.

2. Built an isolated module using the provider already cached in the consuming
   repository, `terraform-provider-aws` **5.100.0** (pinned in
   `layers/management/.terraform.lock.hcl`), served through a
   `filesystem_mirror` so no download or AWS credentials were needed.

3. Wrote a state file representing the live analyzer as `import` would populate
   it: `unused_access_age = 90`, `analysis_rule.exclusion.account_ids =
   ["631965728858"]`.

4. Ran `tofu plan -refresh=false` against each shape.

**Original shape (the one prbot approved):**

```
# aws_accessanalyzer_analyzer.unused_access_apse2 must be replaced
-/+ resource "aws_accessanalyzer_analyzer" "unused_access_apse2" {
      ~ arn           = "arn:aws:access-analyzer:ap-southeast-2:215457784173:analyzer/UnusedAccess" -> (known after apply)
      ~ id            = "UnusedAccess" -> (known after apply)
      - configuration { # forces replacement
          - unused_access { # forces replacement
              - unused_access_age = 90 -> null # forces replacement
              - analysis_rule { # forces replacement
                  - exclusion { # forces replacement
                      - account_ids   = [ # forces replacement
                          - "631965728858",
                        ] -> null
...
Plan: 1 to add, 0 to change, 1 to destroy.
```

**Merged shape (after the comparison review's findings were fixed):**

```
No changes. Your infrastructure matches the configuration.
```

The provider schema was also read directly
(`tofu providers schema -json`), confirming `configuration` is a
`nesting_mode: list` block with `max_items: 1` whose nested shape matches the
merged HCL exactly.

---

## E. Verified: prbot source facts

Read from tag `v0.4.0` (the version the CI image pins), except where noted.

| Claim | Location |
|-------|----------|
| `SEVERITY_WEIGHTS` = critical 25.0, high 15.0, medium 8.0, low 3.0, info 0.0 | `review/scorer.py` |
| `deduction = weight * (confidence / 100.0) if band == "reported" else 0.0` | `review/scorer.py`, `ScoredFinding.from_finding` |
| `total_deductions = sum(sf.deduction for sf in reported)`; `raw_score = 100.0 - total_deductions` | `review/scorer.py`, `score_findings` |
| reported iff `confidence >= threshold`; borderline iff `>= threshold - 15`; else hidden | `review/scorer.py`, `classify_confidence_band` |
| `confidence_threshold: int = Field(default=70, ...)` | `config.py:239` |
| `blocker_threshold: int = Field(default=70, ...)` | `config.py:240` |
| `context_lines: int = Field(default=0, ge=0, le=200)` | `config.py:256` |
| `agents: list[AgentSpec] \| None = Field(default=None, min_length=1)` | `config.py:281` |
| `_INT_FIELDS` includes `context_lines` | `config.py:548-553` |
| `_LIST_FIELDS = frozenset({"excluded_patterns", "allowed_regions"})`, no `agents` | `config.py:559` |
| Unrecognised `PRBOT_*` env vars fall through as raw strings | `config.py:~625` |
| Implicit config search refused in CI | `config.py:501` |
| `--config` CLI flag exists | `cli.py:88` |
| `PRBOT_PROMPTS_DIR` > package data | `review/prompts.py:34`, `load_check_spec` |
| `AgentSpec.name` selects `{name}.md`; `check_prefix` validator example is `'IAC-'` | `config.py:154-186` |
| Single forced tool call, `toolChoice: {"tool": {"name": FINDINGS_TOOL_NAME}}` | `review/runner.py:233-302` |
| `get_file_content` called once, gated on `context_lines > 0`, diff files only, at `metadata.head_sha` | `cli.py:459-464` |
| `get_file_content(path, ref)` on protocol and GitLab client | `vcs/protocol.py:37`, `vcs/gitlab.py:150` |
| `security.md` categories: S-CRED, S-INPUT, S-AUTH, S-CRYPTO, S-DATA | `prompts/security.md` |
| `general.md` categories: Q-ARCH, Q-MAINT, Q-TEST, Q-ERR, Q-API, Q-COMP | `prompts/general.md` |
| `adversarial.md` exists, not in default roster | `prompts/adversarial.md`, `config.py:279-281` |

Consuming CI template, `infrastructure-core:gitlab/templates/prbot.yml` at
merge commit `39d2fd0`:

- `image: ghcr.io/nilesuan/prbot:0.4.0`, `entrypoint: [""]`
- `GIT_STRATEGY: none`, with the stated reason that a checkout "would place a
  `.prbot.toml` that the reviewed code controls into the job's working
  directory"
- `allow_failure: true`, with the stated reason that merge governance is
  ADR 0003
- `rules: - if: $CI_PIPELINE_SOURCE == "merge_request_event"`

---

## F. Verified: state of the change at time of writing

- `management-plan: [paduafg]` on pipeline 123263 finished `success` at
  `2026-09-16T12:40:48.635+10:00`. The MR was merged at `12:41:14.410`, 26
  seconds later.
- `management-apply: [paduafg]` on the post-merge pipeline 123265 is still
  `manual` and has never run. The two new analyzers do not exist and the import
  has not happened.
- IOPS-1479 status is `In Review`, `resolution: null`.

---

## G. NOT verified

Stated explicitly so nothing here is read as established.

1. **The bodies of the two overwritten APPROVE comments.** GitLab exposes no
   note version history. The `verdict`, `score` and per-agent counts come from
   the job traces, which are reliable; the rendered Markdown of those two
   comments is unrecoverable.

2. **Why the security agent produced 8 to 34 output tokens.** The
   prompt-domain-mismatch explanation in `findings.md` is an inference from the
   check categories in `security.md`, not an observed model trace. It is
   consistent with the token counts and with the agent returning 0 findings on
   all three commits including the final one, but it has not been tested. The
   test is cheap: run the same diff against `adversarial.md` or a drafted
   `iac.md` and compare output length and finding count.

3. **Whether `PRBOT_AGENTS` actually raises a Pydantic error.** This is read
   from the config loader's code path (`agents` is absent from every coercion
   set, so it is assigned a raw string to a `list[AgentSpec]` field). It was not
   executed.

4. **Whether widening `context_lines` would change any finding on this MR.**
   Untested. `findings.md` argues context was not the binding constraint here,
   which if correct means the effect is small on this case specifically.

5. **Whether a GitLab file-type CI variable plus `--config` works end to end.**
   The mechanism is inferred from `cli.py:88` and GitLab's documented file
   variable type. Not executed.

6. **Anything about how prbot behaves on non-Terraform diffs.** This case study
   is one MR in one repository. It says nothing about precision or recall on
   application code, which is what both shipped prompts were written for.
