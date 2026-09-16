# Root-cause analysis: why prbot scored 100/100 on a destroy

## 1. What happened

`infrastructure/infrastructure-core!194` adopted two hand-created IAM Access
Analyzers into OpenTofu state via `import` blocks, and created two more. prbot
ran on all three head commits. A separate multi-agent review pipeline ran once,
on the first commit.

| Commit | prbot verdict | prbot score | general findings | security findings |
|--------|---------------|-------------|------------------|-------------------|
| `eaa3480b` original | **APPROVE** | 100 | 0 | 0 |
| `1db1d3a7` fix round 1 | **APPROVE** | 100 | 0 | 0 |
| `9b67058c` final | COMMENT | 100 | 4 | 0 |

The other reviewer scored `eaa3480b` at **64.93/100, REQUEST CHANGES, 13
findings**. The author accepted those findings and fixed them. The merged
source cites the reviewer's finding identifiers directly in four places
(`SEC-IAC-01`, `SEC-IAC-02`, `SEC-IAC-04`, `GEN-API-04-1`).

The defect was real. See `repro/` for the proof: the approved shape plans
`Plan: 1 to add, 0 to change, 1 to destroy` against post-import state, and the
merged shape plans `No changes.`

## 2. "100/100" meant two different things

These are separate mechanisms and both need fixing.

### On `eaa3480b` and `1db1d3a7`: a genuine miss, not a filter

The audit records read `reported_count: 0, borderline_count: 0,
hidden_count: 0, suppressed_count: 0`. Nothing was found at any confidence.
The security agent emitted **34 output tokens** on the first run and **8** on
the second. Eight tokens is an empty findings array.

### On `9b67058c`: a scoring artefact

Four findings were returned, then scored to zero. From
`src/prbot/review/scorer.py`:

```python
deduction = weight * (finding.confidence / 100.0) if band == "reported" else 0.0
...
total_deductions = sum(sf.deduction for sf in reported)
raw_score = 100.0 - total_deductions
```

`band == "reported"` requires `confidence >= threshold`, and
`confidence_threshold` defaults to `70` (confirmed as `70` in all three audit
records). The four findings were one at 55% and three below 55%. `reported` was
empty, so `total_deductions` was `0.0`, so `raw_score` was exactly `100.0`.

**"Score: 100/100" and "found 4 problems" are the same sentence in this
system.** The comment body said "No findings to report" while the audit record
said `finding_count: 4`.

## 3. The four causes, ranked

Only one of these is about how much context the reviewer can see, and it is
the smallest.

### Cause 1: neither check specification covers infrastructure (largest)

`src/prbot/prompts/security.md` covers S-CRED (secrets), S-INPUT (injection),
S-AUTH (authn/authz), S-CRYPTO (cryptography), S-DATA (PII, SSRF, races).

`src/prbot/prompts/general.md` covers Q-ARCH, Q-MAINT, Q-TEST, Q-ERR, Q-API,
Q-COMP.

Between the two there is **no** category for resource replacement, state
adoption, provider lifecycle semantics, IAM scope, or blast radius. Given a
Terraform diff and a checklist asking about SQL injection, `{"findings": []}`
is the correct answer to the question asked. The security agent is not failing.
It is the wrong agent for this repository.

This is corroborated by the token counts. The security agent's input was
~63-85k tokens each run (it read the diff) and its output was 8 to 34 tokens
(it had nothing to say about it).

### Cause 2: no way to settle a suspicion

The other reviewer *did* spot this from the same diff and filed it at **38%
confidence**, writing verbatim that it "could not be confirmed without `tofu
providers schema -json`". It was right and did not know it.

prbot has the identical ceiling. A reviewer that can suspect but never confirm
will file everything low, and under Cause 3 everything it files is worth zero.
This is a tool gap, not a repository-access gap.

### Cause 3: scoring nulls out exactly this class of finding

`classify_confidence_band(38, 70)` returns `"hidden"`, so a 38%-confidence
finding deducts `0.0` regardless of severity. A `critical` finding
(`SEVERITY_WEIGHTS["critical"] = 25.0`) at 69% confidence deducts nothing and
is never shown.

Had prbot found this defect with the same 38% confidence the other reviewer
assigned, it would still have printed 100/100.

### Cause 4: missing surrounding context (smallest)

Real, but it did not cause this miss. Both halves of the defect were inside the
diff prbot received (`diff.filtered files=4->4`):

- `access-analyzer-import.tf`: an `import` block adopting a live
  `unused_access_apse2`
- `main.tf`: that same resource declaring `analyzer_name`, `type` and `tags`,
  and nothing else

The question "you are adopting a live resource but declaring only some of its
attributes, so what happens to the ones you left out?" is answerable from those
two hunks alone. No clone, no wider window, no extra file fetch.

## 4. What the codebase already has

- **`get_file_content(path, ref)`** exists on `vcs/protocol.py` and
  `vcs/gitlab.py`. Retrieval over the API with no working copy.
- It is called in exactly one place, `cli.py:459-464`, gated on
  `config.context_lines > 0`, and only for files already in the diff.
- **`context_lines` defaults to `0`** (`config.py:256`) and is in `_INT_FIELDS`
  (`config.py:551`), so `PRBOT_CONTEXT_LINES` works from the environment today.
- **`PRBOT_PROMPTS_DIR`** overrides the prompt directory
  (`review/prompts.py:34`), so custom check specs do not require an image
  rebuild.
- **`agents`** is a configurable roster (`config.py:281`). `AgentSpec.name`
  selects `{name}.md`, and the `check_prefix` validator names `'IAC-'` as its
  worked example. A third agent was anticipated by design.
- **`adversarial.md`** already ships and is framed correctly for this class of
  defect ("find the inputs, the environment, or the sequence of events under
  which this code does the wrong thing"). It is not in the default roster,
  which is `general` + `security`.

## 5. The one real configuration blocker

`agents` is a `list[AgentSpec]`. It is not in `_LIST_FIELDS`
(`config.py:559`, which holds only `excluded_patterns` and `allowed_regions`),
so `PRBOT_AGENTS=...` falls through to `merged[field_name] = value` as a raw
string and Pydantic rejects it. The roster can only be set from TOML.

In CI that TOML is unreachable:

- `config.py:501` refuses the implicit config search when `_in_ci()` is true
- the consuming template sets `GIT_STRATEGY: none`, so no repository file
  exists in the job's working directory to point `--config` at

**Consequence:** in this CI wiring the roster is hard-pinned to `general` +
`security`, and there is no supported way for a repository to add an IaC agent
to its own reviews.

The `GIT_STRATEGY: none` choice is correct and should not be reverted. Its
stated reason is that a checkout "would place a `.prbot.toml` that the reviewed
code controls into the job's working directory", which is a real
prompt-injection and config-tampering boundary. The fix must preserve it.

## 6. What needs actual code

`review/runner.py` issues a single `client.converse` call with
`toolChoice` forced to `FINDINGS_TOOL_NAME` (`runner.py:254`). The tool is
structured output, not retrieval. The model gets one turn and cannot request
anything.

Giving the reviewer a bounded `read_file(path, ref)` tool backed by the
existing `get_file_content` is the largest single capability lever, and it is
the only item in `recommendation.md` that is a genuine code change rather than
configuration.

## 7. What no prompt change fixes

`prbot-review` is `allow_failure: true` in the consuming template, so it never
gates a merge. A perfect review posting a critical finding would not have
stopped this merge. The MR was merged 26 seconds after its pipeline finished,
which is not enough time to read the plan the MR description asked reviewers to
read.

Raising review quality raises the odds someone is warned. It does not create a
gate. That is a governance decision in the consuming repository (ADR 0003
there), not a prbot change.

## 8. Calibration note on the comparison reviewer

Recorded so this case study is not read as "the other tool is correct".

- **One of its 13 findings is a false positive.** `SPEC-DRIFT-02` (41%)
  asserted there was no Jira comment recording the scope reinterpretation.
  Comment `139778` was created at `2026-09-16T10:19:35.437+1000`, about 33
  minutes *before* the finding was written at `10:52:09`. The finding did
  disclose its own blind spot ("the Jira snapshot used for this review has no
  comments field"), which is the right behaviour, but the claim is wrong.
- **Its confidence calibration is inverted on the finding that mattered.** The
  only finding describing live infrastructure loss was filed at the lowest
  confidence in its security set (38%).

The target for prbot is not parity with a multi-stage pipeline that runs the
test suite. prbot is roughly $0.27 and 60 seconds of two single-shot agents.
The achievable target is "never returns 100/100 on a change that destroys live
infrastructure", which is a much lower bar.
