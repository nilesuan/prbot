# Recommendation

Ordered cheapest first. Each item states what it fixes, what it costs, and how
you would know it worked. **Stop after item 2 and re-measure** before doing 3
or 4.

## Measure before you change anything

`evals/` is a promptfoo harness whose fixtures are built from prbot's own
`PRDiff` and `PRMetadata` types, so what the model sees in an eval is what it
sees in production (`evals/fixtures.py`).

Add MR 194 as a regression pair before touching any code or prompt:

- `eaa3480b` diff: **must** produce at least one finding naming the missing
  `configuration` block on an import target
- `9b67058c` diff: **must not** produce that finding

Without this pair, every change below is tuning until a number looks right,
which produces a reviewer that passes the cases you already have and misses the
next one. The diffs are recoverable from
`refs/merge-requests/194/head` in `infrastructure/infrastructure-core`, or from
the `diff_hash` values recorded in the audit records (`evidence.md` section B).

---

## 1. Turn on the context that already exists

**Fixes:** cause 4 (partially).
**Cost:** no code. One CI variable.

`context_lines` defaults to `0`, so `get_file_content` is never called.
`context_lines` is in `_INT_FIELDS`, so it is settable from the environment:

```yaml
variables:
  PRBOT_CONTEXT_LINES: "80"
```

**Expected effect on this case: small.** `findings.md` argues context was not
the binding constraint on MR 194. Do it because it is free and because the
capability's own docstring says the question is "for measurement, not
argument", not because it is expected to fix this.

**Measure:** token cost per review and finding count across the eval suite,
before and after. Watch for precision loss as well as recall gain.

---

## 2. Add an IaC agent (the one that should catch this)

**Fixes:** cause 1, the largest.
**Cost:** one new prompt file, one config-delivery mechanism.

### 2a. Write `iac.md`

Check prefix `IAC-`. The `AgentSpec.check_prefix` validator already names
`'IAC-'` as its worked example, so no code change is needed to accept it.

Categories the MR 194 defect would have been caught by, and which neither
shipped prompt contains:

| Suggested ID | Check |
|--------------|-------|
| `IAC-ADOPT-01` | An `import` block whose target resource declares fewer attributes than the live resource carries. State is populated from live, config is not, and the next plan reconciles by destroying. |
| `IAC-ADOPT-02` | Import target with no `prevent_destroy` on a singleton or hard-to-recreate resource. |
| `IAC-REPLACE-01` | A change to an attribute that forces replacement on a resource that holds accumulated state (findings, logs, keys, snapshots). |
| `IAC-SCOPE-01` | Resource gating (`count`, `for_each`, provider alias) that does not match the stated blast radius. |
| `IAC-REGION-01` | Provider alias or region binding that is implicit where siblings are explicit. |
| `IAC-TEST-01` | A test asserting a hardcoded literal against itself, so it cannot fail. |

Four of these six map to findings the comparison reviewer actually filed
(`SEC-IAC-01`, `SEC-IAC-02`, `SEC-IAC-04`, `GEN-API-04-1`). That is the
calibration target, not an aspiration.

**Do not write this prompt against MR 194's specifics.** Every check above must
be a statement about a property of Terraform or OpenTofu that holds for any
provider and any resource type. A check that only fires on
`aws_accessanalyzer_analyzer` is worthless.

Consider whether `adversarial.md`, which already ships and is framed correctly
for this class of defect, is a better starting point than a new file, or belongs
in the roster alongside it.

### 2b. Make the roster reachable from CI

This is the blocking dependency and needs a decision. `agents` can only be set
from TOML, and in CI the TOML is unreachable (`findings.md` section 5).

**Recommended option: GitLab file-type CI variable.**

```yaml
variables:
  PRBOT_CONFIG: ""   # file-type variable defined in project settings
script:
  - prbot --config "$PRBOT_CONFIG" --platform gitlab --repo "$CI_PROJECT_PATH" --pr "$CI_MERGE_REQUEST_IID"
```

This preserves the `GIT_STRATEGY: none` boundary exactly. The config comes from
project settings, which only a maintainer can edit, never from the branch under
review. `--config` already exists at `cli.py:88`.

**Alternative:** make `agents` settable from the environment as JSON, by adding
a `_JSON_FIELDS` coercion set alongside `_LIST_FIELDS`. Smaller CI change,
larger code change, and it puts a structured object into an environment
variable, which is harder to review.

For a custom `iac.md` that is not shipped in the image, `PRBOT_PROMPTS_DIR`
already works (`review/prompts.py:34`); the directory has to be materialised in
the job, which the same file-variable mechanism can do.

**Measure:** the MR 194 eval pair. This is the item expected to flip it.

---

## 3. Stop low-confidence high-severity findings from scoring zero

**Fixes:** cause 3.
**Cost:** a change to `ScoredFinding.from_finding` plus threshold tests.

Today a `critical` finding at 69% confidence deducts `0.0` and is not shown.
That is the rule that would have kept the score at 100 even if prbot had found
this defect at the 38% confidence the comparison reviewer assigned it.

Two independent changes, either or both:

- **Graduated deduction.** Let `borderline` deduct at a reduced weight instead
  of `0.0`, so a finding just under the line costs something.
- **Severity floor.** Never place a `critical` or `high` finding in `hidden`.
  Surface it with its confidence shown, whatever that confidence is.

Do **not** simply lower `confidence_threshold` from 70. That trades this miss
for a flood of low-confidence noise across every other repository, and the
threshold is not the defect: the `else 0.0` branch is.

**Measure:** precision and recall across the whole eval suite, not just MR 194.
This change can only increase reported findings, so the risk is false
positives. If precision drops materially, prefer the severity floor alone.

---

## 4. Give the reviewer bounded retrieval

**Fixes:** cause 2, and cause 4 properly.
**Cost:** real. The largest item here.

`runner.py:233-302` issues one `converse` call with `toolChoice` forced to the
findings tool. The model gets one turn and cannot ask for anything.

Replace with a bounded loop offering one additional tool:

```
read_file(path: str) -> str | null
```

Backed by the existing `get_file_content`, pinned to the head SHA, read-only,
with a hard call cap (start at 5), a per-file size cap, and every fetch recorded
in the audit record so the cost is visible.

**This still needs no clone.** It is the same API call `cli.py` already makes.

**Also consider a domain tool.** The specific thing that would have settled
`SEC-IAC-01` is the provider schema. A `terraform_provider_schema(resource_type)`
tool, or shipping a pre-built schema index in the image, converts a 38%-confidence
suspicion into a 95% finding. That is a bigger design question and should not be
bundled with the retrieval loop.

**Measure:** latency, cost per review, and whether findings that cite a fetched
file are more accurate than findings that do not. Hold the call cap low until
that is answered.

---

## 5. Out of scope for prbot

`prbot-review` is `allow_failure: true` in the consuming template, so it cannot
block a merge no matter how good it gets. On MR 194 the merge happened 26
seconds after the pipeline finished. All four items above change the odds
someone is warned; none creates a gate.

Whether an automated reviewer should be able to block a merge is a governance
decision in the consuming repository, recorded there as ADR 0003. It is
correctly out of prbot's hands, and it should be raised separately rather than
quietly changed by flipping `allow_failure`.

---

## Suggested issue breakdown

| # | Title | Depends on |
|---|-------|-----------|
| 1 | Add MR 194 destroy-on-import regression pair to `evals/` | - |
| 2 | Allow a repository to set its agent roster in CI without a checkout | - |
| 3 | Add an `iac.md` check specification with `IAC-` prefix | 1, 2 |
| 4 | Stop `borderline` and `hidden` findings from deducting exactly zero | 1 |
| 5 | Bounded `read_file` tool for review agents | 1 |
| 6 | Investigate: does the security agent return empty on all non-application diffs? | 1 |

Item 6 is cheap and should probably run first. It tests the central inference in
`findings.md` that has not been verified (`evidence.md` section G, item 2), and
its answer determines how much of items 3 to 5 are actually needed.
