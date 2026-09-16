# How to improve prbot: evidence from production reviews

Analysis date: 2026-09-16. **prbot versions under test: 0.4.0 for most of the
corpus, 0.5.2 for the last few reviews.** `infrastructure-core` MR 193 pinned
`ghcr.io/nilesuan/prbot:0.4.0`, and both repos only moved to 0.5.x when MR 195
and MR 247 merged at 16:03 on 2026-09-16. Job traces confirm 0.5.2 on
`core!157` and `core!183`; the MR 194 case study records the 0.4.0 image digest
for that run. Read every finding below as a property of 0.4.0 unless it cites
0.5.x behaviour explicitly.

Source data: the GitLab REST API only. No repository was cloned. Every number
below was computed from 2,272 merge request notes pulled across 281 merge
requests in three projects, plus the prbot source tree at commit `d87e827`.

---

## The short version

prbot is running, posting, and finding almost nothing. In the 19 reviews it
posted to `infrastructure/infrastructure-core` and
`infrastructure/terraform-modules`, its two agents produced **40 findings and
exactly 1 of them reached the findings table**. Eighteen of the 19 reviews
scored exactly 100/100.

There are **two** distinct mechanisms behind that, and they need different
fixes.

**The agents are asked the wrong questions.** Neither check specification
contains a single infrastructure-as-code category, so on a Terraform diff the
agents frequently return nothing at all, at any confidence. On `core!194` the
security agent read 63 to 85k input tokens and emitted 8 to 34 output tokens,
which is an empty findings array. This is the larger cause for these two
repositories, and it is established in
[`../mr194-review-miss/findings.md`](../mr194-review-miss/findings.md) rather
than here.

**What the agents do find is then destroyed by scoring.** The reporting
threshold (70) is 20 points higher than the reporting floor the prompts give
the model (50), so findings land in a dead zone where they are collapsed into a
`<details>` block, deduct nothing from the score, and never become a comment on
a line. This is what the rest of this document measures.

The highest-value change is to give the agents checks that match the code they
review. The second is to stop a finding below the threshold from deducting
exactly zero. Neither is a model quality problem.

---

## What was pulled

| Project | ID | MRs | MRs with notes | prbot comments | local reviewer comments |
|---|---|---|---|---|---|
| `infrastructure/infrastructure-core` | 220 | 196 | 92 | 12 | 99 |
| `infrastructure/terraform-modules` | 217 | 247 | 130 | 7 | 145 |
| `platform/terraform-modules` | 177 | 74 | 59 | 15 | 54 |

prbot comments were identified by the `<!-- prbot:state:` idempotency marker
that `formatter.py` embeds, not by author name. That matters: prbot posts
under two different identities. In the two target repos it is the group access
token `group_353_bot_d0635c9ea2526cb2a0b9868b7f7496d8`. In
`platform/terraform-modules` in March to May 2026 it ran under the personal
admin account `adm-nile.suan`, which is why that corpus is easy to miss. The
marker is unambiguous: 34 notes carry it, 34 notes mention prbot at all, and
the two sets are identical.

"Local reviewer" means the `/amrr`, `/mrr` and `/pre-review` pipeline run from
the terminal, which posts under `nile.suan` in the `## Code Review` format and
carries no state marker.

---

## The measurement

Taken from prbot's own posted output. The "Agent Status" block reports how
many findings each agent returned; the findings table, the borderline
`<details>` block and the "N low-confidence findings hidden" line report what
survived.

| Corpus | Reviews | Agent findings | Reached findings table | Borderline | Hidden | Surfaced |
|---|---|---|---|---|---|---|
| infrastructure-core + terraform-modules (0.5.x) | 19 | 40 | 1 | 24 | 14 | **2.5%** |
| platform/terraform-modules (Mar to May 2026) | 15 | 98 | 47 | 44 | 7 | 48.0% |

Scores in the two target repos: 18 of 19 at exactly 100/100, one at 89/100.
Scores in the older platform corpus: 2 of 15 at 100/100, range 52 to 100.

Do not read the two rows as a clean before-and-after. They are different
languages (OpenTofu HCL against TypeScript CDKTF), different changes and
different merge requests. The row that matters on its own is the first one:
40 findings in, 1 out.

---

## Ordering: what the merge request notes alone cannot show

**This section corrected 2026-09-16 after
[`../mr194-review-miss/`](../mr194-review-miss/README.md) was found.** An
earlier draft concluded that prbot had not missed anything on `core!194`. That
was wrong, and the way it was wrong is itself one of the findings below.

prbot rewrites its comment in place rather than posting a new one. 13 of the 34
comments have `updated_at` later than `created_at`, and the state marker
timestamp always tracks `updated_at`, so **each merge request preserves only
prbot's most recent verdict**. Worse, `GET /merge_requests/:iid/commits`
returns only the post-rebase commit set, so a rebased branch hides the commits
the earlier reviews actually ran against.

Both effects hit `core!194`. Reconstructing it from
`GET /repository/commits/:sha`, which does resolve the pre-rebase commits, and
from the audit records recovered in the case study:

| Commit | Time (+10:00) | prbot verdict | Local reviewer |
|---|---|---|---|
| `eaa3480b` original | 10:09:51 | **APPROVE 100/100, 0 findings** | **64.93/100, REQUEST CHANGES, 13 findings** |
| `1db1d3a7` fix round 1 | 11:35:09 | **APPROVE 100/100, 0 findings** | - |
| `9b67058c` final (rebased) | 12:36:16 | COMMENT 100/100, 4 findings scored to zero | - |

Only the third row survives in GitLab. **prbot approved `eaa3480b` at 100/100
with zero findings, and the case study's reproduction proves that code would
have destroyed and recreated a live organization-wide IAM Access Analyzer on
the first apply after import.** That is a genuine miss, not a scoring artefact,
and the audit records show `hidden_count: 0, borderline_count: 0` on both
approving runs, so nothing was found at any confidence.

The same caution applies to `core!192`, `tfmod!246` and `core!189`, where
prbot's surviving review also ran on the final post-fix commit. Those three
have **not** been checked against their audit records, so whether prbot found
anything on their earlier commits is simply unknown from the notes.

One comparison is unaffected by all of this, because it has a single commit,
`2b74838e`, with no rebase and no intervening change. `platmod!68`:

- prbot at 17:44Z: **54/100, REQUEST_CHANGES**, 6 findings.
- Local reviewer at 18:38Z: **94/100, APPROVE + AUTO-MERGE**, 9 findings.

Forty points apart and opposite verdicts on identical code, with prbot the
harsher of the two. So prbot fails in both directions: it missed a live
infrastructure destroy at 100/100, and it demanded changes on a merge request a
human-grade review approved.

**Root cause this document under-weights.** The case study establishes a cause
larger than anything below for these repositories: neither check specification
contains a single infrastructure-as-code category. The security agent read 63
to 85k input tokens per run and emitted 8 to 34 output tokens, which is an
empty findings array. Given a Terraform diff and a checklist about SQL
injection, finding nothing is the correct answer to the question asked. That
explains the zero-finding runs; the threshold mechanism documented below
explains the four-findings-scored-to-zero run. Both are real and they are
different defects. See
[`../mr194-review-miss/findings.md`](../mr194-review-miss/findings.md).

---

## Improvements, ranked

### 1. Close the 20-point dead zone between the prompts and the scorer

**The defect.** The prompts tell the model one reporting floor and the scorer
enforces a different, higher one.

- `src/prbot/prompts/general.md:103`: "Only report findings with confidence
  >= 50."
- `src/prbot/prompts/security.md:82-84`: critical and high at >= 70, medium at
  >= 60, low and info at >= 50.
- `src/prbot/config.py:239`: `confidence_threshold: int = Field(default=70)`.
- `src/prbot/review/scorer.py:148-162`: reported at >= 70, borderline at 55 to
  69, hidden below 55.

Everything the model emits between 50 and 69 is solicited by the prompt and
then discarded by the scorer.

**The proof in the data.** Confidence values in the 34 comments are sharply
bimodal. Every finding that reached a findings table is at 70% or above (70,
72, 75, 80, 85, 88, 90, 92). Every suppressed finding is at 68% or below, and
**35 of the 68 suppressed findings sit at exactly 55%**, the lowest value that
still renders as borderline rather than vanishing. In
`infrastructure/terraform-modules`, all 9 suppressed findings are at exactly
55%. That pile-up is the model answering the instruction it was given.

**Why the score is always 100.** `scorer.py:133`:

```python
deduction = weight * (finding.confidence / 100.0) if band == "reported" else 0.0
```

Borderline and hidden findings deduct zero. `raw_score = 100.0 -
total_deductions`, so when nothing clears 70 the score is exactly 100 by
construction. That is the whole explanation for 18 of 19 reviews at 100/100.

**The fix.** Separate three decisions that are currently one:

1. *Does this finding exist?* Anything above a low floor should be published,
   with its confidence printed, exactly as the local reviewer already does.
2. *How prominent is it?* Use the threshold for presentation only, that is an
   inline thread against a collapsed list, not for existence.
3. *What does it cost the score?* Make the deduction continuous in confidence
   across all bands rather than a cliff at 70. A 55% medium finding should cost
   something, not nothing.

The local reviewer is the working proof that publishing low-confidence
findings is useful rather than noisy. On `core!194` it posted findings at 22%,
38%, 41%, 45%, 52%, 54%, 60%, 64% and 71% confidence, each with the number
shown, and the merge request author acted on them.

**Calibrate, do not guess.** Any new threshold needs a held-out measurement,
not a number that makes the current corpus look better. There is a ready-made
labelled set for this: **298 local-reviewer reviews already exist across these
three projects**, many on merge requests prbot also reviewed. They can be
pulled over the API, as this analysis did, without cloning anything.

**Do not simply lower the threshold.** `PRBOT_CONFIDENCE_THRESHOLD` is a
recognised integer override (`config.py:549-553`), neither repo sets it, and
neither has a `.prbot.toml`, both returning 404, so both run the bare 70
default. It is therefore tempting to drop it with one CI variable. Resist that
as the fix: it trades this repository's misses for low-confidence noise in
every other repository, and the threshold is not the defect. The defect is the
`else 0.0` branch at `scorer.py:133`. Prefer graduated deduction, or a severity
floor that never hides a `critical` or `high` finding whatever its confidence.
Lowering the threshold is worth doing once, briefly, as a probe to see what is
currently being hidden, and it should be reverted.

---

### 2. Stop instructing the model to suppress its own findings

**The defect.** Both prompts contain this line, at `general.md:74-75` and
`security.md:68-69`:

> "Questions to the author. A finding is a claim, not a question. If you cannot
> assert it, lower the confidence until it is filtered out."

This teaches the model to use the confidence field as a self-censoring dial
rather than as an estimate of how likely the finding is to be real. Combined
with the floor of 50 it explains the pile-up at 55 precisely: the model is
doing what it was told, parking anything it is unsure about just above the
floor, where the scorer then deletes it.

`security.md:86` pulls in the opposite direction at the same time: "When
uncertain, lower the severity rather than the confidence."

**The fix.** Make confidence mean one thing only, the probability that the
finding is real, and say so. Move the "do not ask questions" instruction to
where it belongs, which is the shape of the output, and let severity carry how
much the finding matters. The two instructions currently contradict each
other and both push findings into the dead zone.

---

### 3. Make the hallucination penalty proportionate and visible

**The defect.** `src/prbot/security/validation.py:20` sets
`HALLUCINATION_PENALTY = 40`, applied at line 111 to any finding whose line
range does not intersect a changed hunk. Forty points is larger than the
entire 30-point span between the hidden floor and full confidence. A finding
at 90% confidence about a line two lines outside a hunk drops to 50% and is
hidden outright.

This compounds with improvement 4. `context_lines` defaults to `0`
(`config.py:256`) and neither repo sets `PRBOT_CONTEXT_LINES`, so the agents
see only the hunks. They are penalised for reasoning about lines they were
never shown.

**The fix.** Scale the penalty by distance from the nearest hunk rather than
applying a flat 40, and record the penalty in the audit output so a reviewer
can see that a finding was downgraded rather than simply absent.

---

### 4. Give the agents file context

**The defect.** `context_lines` defaults to 0, and the CI template sets
`GIT_STRATEGY: none` in both repos, deliberately and for a good reason: a
checkout would let the reviewed branch place a `.prbot.toml` into the job's
working directory. The result is that prbot reviews hunks in isolation, with
no surrounding file and no repository.

This is the structural reason prbot cannot produce the local reviewer's best
findings. On `core!194` the local reviewer proved the apply gate was weak by
citing `.gitlab-ci.yml:476`, a file not in the diff at all, and separately
checked whether cited evidence files existed in the worktree. prbot deletes
findings for files outside the diff (`validation.py:90-96`), so that class of
finding is unreachable by construction.

**The fix.** The machinery already exists and is switched off.
`src/prbot/review/context.py` expands hunks by `context_lines`, and the
adapters can fetch file contents over the API without a checkout. Set a
non-zero default, and allow the agents to request specific files by path from
the API rather than from a working copy. This keeps the `GIT_STRATEGY: none`
boundary intact, because fetching a blob over the API is not the same as
letting the branch write into the job's filesystem.

---

### 5. Account for every finding the agents produced

**The defect.** The posted comment does not reconcile. On `tfmod!245` (note
112141) the Agent Status block reads "security: 4 findings", the borderline
section lists 3, and there is no hidden line. One finding is gone with no
accounting anywhere in the output.

There are three silent sinks: `deduplicate_findings` merges findings without
reporting the merge to the comment, `validate_findings_against_diff` deletes
findings for files outside the diff at `validation.py:90-96` with only a
`logger.warning`, and `apply_suppressions` returns suppressed findings that
the renderer does not always surface.

The docstring at `scorer.py:262` already states the principle: "a suppression
list nobody can see is how a review bot becomes decorative."

**The fix.** Print one reconciliation line: produced, merged, dropped as
outside the diff, suppressed by rule, hidden by confidence, reported. The
numbers should add up in the comment itself, so that a reader can see when the
pipeline is eating its own output.

---

### 6. Deduplicate across files and across non-overlapping lines

**The defect.** `scorer.py:58` requires an exact file path match plus a line
overlap before two findings can merge:

```python
if a.file_path != b.file_path or not _overlaps(a, b):
    return False
```

One defect that appears in two files can never be merged, and is therefore
deducted twice.

**The proof.** In 4 of the 6 prbot reviews that produced more than one table
row, the same check ID appears against two different files:

- `platmod!68`: `Q-ARCH-04` on `.gitlab-ci.yml` and on `README.md`, both the
  same hardcoded AWS account ID.
- `platmod!69`: `Q-ARCH-04` on `.gitlab-ci.yml` and `package.json`.
- `platmod!70`: `Q-ARCH-04` on two files, and `Q-TEST-02` twice on the *same*
  file at non-overlapping line ranges, which the `_overlaps` test also fails.
- `platmod!67`: `Q-API-01` on two files.

**The cost, verified arithmetically.** `platmod!68`'s six findings were high
at 90%, and medium at 85, 85, 80, 80 and 75%. Applying `SEVERITY_WEIGHTS` and
`scorer.py:133` gives deductions of 13.5, 6.8, 6.8, 6.4, 6.4 and 6.0, totalling
45.9, so `int(100 - 45.9) = 54`. That is exactly the 54/100 prbot posted, which
confirms the model of the scorer is correct.

Three of those six findings are facets of one defect, namely that `mergeTags`
is documented but never called, reported separately as "`mergeTags` is exported
but not called" (high, 90%), "`_provider` parameter accepted but never used"
(medium, 85%) and "No test for `mergeTags` being called" (medium, 75%). Two
more are the single account-ID defect counted in two files. The local reviewer
found the same cluster, graded it as two medium and two low findings, and
scored 94/100 APPROVE.

`min_passing_score` is 70 (`config.py:245`, `verdict.py:65`), so the
double-counting is what pushed this merge request to REQUEST_CHANGES.

**The fix.** Match on the defect rather than on coordinates. Same check ID plus
similar normalised title should merge regardless of file, and same file plus
same check ID should merge regardless of line overlap. `_normalise_title`
already exists and is only consulted after the file and overlap gate has
passed, which is the wrong order.

---

### 7. The merge gate that was designed is not operative

**The defect.** Both repos are configured for prbot's findings to hold the
merge, and the wiring is correct:

- `only_allow_merge_if_all_discussions_are_resolved=true` on both projects,
  verified over the API.
- `PRBOT_REVIEW_MODE: review` set explicitly in both CI templates.

**Zero positioned notes exist.** Across all 2,272 notes in all three projects,
prbot has never posted an inline comment. The cause is
`formatter.py:657`, `build_inline_comments(reported, diff)`, which only ever
receives the `reported` band. With 1 reported finding in 19 reviews there is
nothing to anchor, so no discussion is created, so nothing gates the merge.

Be fair about the history here: 0.5.0 and 0.5.1 had a genuine GitLab inline
bug, fixed in 0.5.2 and documented in the changelog, and the one reported
finding (`core!189`, 89/100) ran on 0.5.1 before that fix shipped. So inline
posting is better described as never yet exercised in production than as
broken. It will stay unexercised until improvement 1 lands, because the same
threshold starves it.

**The fix.** Anchor borderline findings inline as well, at lower prominence.
The merge gate is a good design and it is currently decorative.

---

### 8. Preserve review history

**The defect.** prbot finds its previous comment and rewrites it. 13 of 34
comments were updated in place. On `core!194`, note 112179 was created at
10:19 and last updated at 12:37, spanning two commits. What prbot said about
the original code is unrecoverable.

This defeats the comparison this analysis set out to make, and it will defeat
any future attempt to measure whether prbot is improving, because the record
of what it said before the fix is deleted by the fix.

**The fix.** Keep the state marker and the find-then-update behaviour for the
summary, since duplicate summaries were the 0.5.1 bug and that fix was right,
but append a short superseded-verdict log inside the comment, for example
"`4d340117`: COMMENT 100/100, 0 reported" per prior head SHA. It costs a few
lines and makes the bot auditable.

---

### 9. Add a verification pass

The local reviewer cross-checks its own findings before publishing. From
`core!194` note 112184, verbatim:

> "**13 findings posted as individual comments.** 4 were confirmed as
> originally stated by independent verification; 9 were confirmed in substance
> but had their severity or confidence adjusted downward after
> cross-verification found the underlying risk narrower than first stated."

prbot runs two agents in parallel (`review/runner.py`) and publishes whatever
comes back. There is no second look. This is why its confidence numbers are
uncalibrated model self-estimates clustering at 55, and why `platmod!68` came
out 40 points below a human-grade review of the same commit.

A verification pass is also the honest alternative to a high threshold. The
current design suppresses uncertain findings because it cannot tell good ones
from bad ones. Checking them is the better answer than hiding them.

---

### 10. Two check families prbot does not have

The local reviewer produces finding classes that prbot's check specs do not
cover at all. prbot has `Q-*` (architecture, maintainability, testing, error
handling, API contracts, completeness) and `S-*` (credentials, input
validation, auth, cryptography, data safety). On `core!194` alone the local
reviewer produced:

- **Spec and ticket compliance**: `SPEC-AC-01` and `SPEC-AC-04` checked the
  delivered code against the Jira ticket's literal wording and found the
  remediation deliberately not built as written. `SPEC-DRIFT-02` noted the
  ticket was never re-scoped to record it. `SPEC-TRACE-01` and `SPEC-TRACE-02`
  found that a document cited as authoritative throughout the change does not
  exist in the worktree.
- **Static analysis and test results**: "`tofu init -backend=false`,
  `tofu fmt -check -recursive`, and the full `layers/management` test suite
  (`tofu test`) all passed clean on this commit: **186 passed, 0 failed, 0
  skipped** (baseline before this change was 177)."

Neither is reachable from a diff alone. The spec family needs the ticket,
which prbot could fetch since the ID is in the MR title. The static analysis
family needs a working copy, which the `GIT_STRATEGY: none` boundary
deliberately forbids, so it belongs in a separate CI job whose results prbot
reads rather than inside prbot itself.

The local reviewer also records **info-severity confirmations** of things it
checked and found correct, such as `core!194`'s `SEC-IAC-05`, "Verification
result: paduafg gating is consistent across all new resources and imports".
prbot's prompts forbid this outright, at `general.md:71` and `security.md:65`,
"Praise or reassurance of any kind." That rule is right about praise and wrong
about coverage. A reviewer needs to know what was checked and cleared, not
only what failed.

---

## Suggested order of work

This list covers the scoring and delivery defects measured in this document. It
is deliberately **not** the whole remediation: the largest item, adding
infrastructure-as-code checks so the agents are asked about the code they are
actually reviewing, is sequenced in
[`../mr194-review-miss/recommendation.md`](../mr194-review-miss/recommendation.md)
and should be read first. That document also specifies the regression pair to
add to `evals/` before changing anything, which applies to every item below.

1. Improvements 1 and 2 together, since the threshold and the prompt floor are
   one defect with two halves. Calibrate against the 298 existing
   local-reviewer reviews. Prefer graduated deduction or a severity floor over
   lowering the threshold.
2. Improvement 5, the reconciliation line, because it makes every later change
   measurable.
3. Improvement 6, deduplication, which is small, self-contained and has a
   verified worked example to test against.
4. Improvements 3 and 4, context and the penalty, which need care around the
   `GIT_STRATEGY: none` boundary.
5. Improvement 7 follows from 1 at no extra cost.
6. Improvements 8, 9 and 10 are larger pieces of work.

---

## Evidence index

Every claim above traces to one of these. GitLab base URL is
`https://gitlab.padua.net.au`.

| Claim | Evidence |
|---|---|
| 40 agent findings, 1 reported, 19 reviews | Agent Status blocks in the 19 prbot comments listed below |
| 18 of 19 at 100/100 | `prbot:state` markers, `score` field |
| 55% pile-up | 35 of 68 suppressed findings, for example notes 112141, 112274 |
| Threshold 70, no repo override | `config.py:239`; both repos return 404 for `.prbot.toml`; no `PRBOT_*` threshold variable at project 220, 217 or group 353 |
| Zero deduction below 70 | `scorer.py:133` |
| Bands 70 / 55 / below | `scorer.py:148-162` |
| Prompt floor 50 | `general.md:103`, `security.md:82-84` |
| Confidence as a suppression dial | `general.md:75`, `security.md:69` |
| Penalty of 40 | `validation.py:20`, applied at `:111` |
| Findings outside the diff deleted | `validation.py:90-96` |
| `context_lines` default 0 | `config.py:256` |
| Dedup requires same file and overlap | `scorer.py:58` |
| platmod!68 score arithmetic | 13.5+6.8+6.8+6.4+6.4+6.0 = 45.9, `int(100-45.9)` = 54, matches posted 54 |
| platmod!68 head to head | prbot note 96958... see MR 68, single commit `2b74838e` |
| Zero inline comments | 0 notes with a non-null `position` carrying the prbot marker, of 2,272 |
| Merge gate enabled | `projects/220` and `projects/217`, `only_allow_merge_if_all_discussions_are_resolved=true` |
| Notes updated in place | 13 of 34 with `updated_at` != `created_at` |
| Review mode set to review | `gitlab/templates/prbot.yml` in both repos |

**The 19 prbot reviews in the two target repos**, as `project!MR` with note ID:
core!157 (112317), core!164 (112273), core!165 (112261), core!183 (112301),
core!186 (112297), core!188 (112245), core!189 (112222), core!192 (112216),
core!193 (112138), core!194 (112179), core!195 (112267), core!196 (112299),
tfmod!224 (112314), tfmod!233 (112257), tfmod!234 (112247), tfmod!239 (112274),
tfmod!245 (112141), tfmod!246 (112144), tfmod!247 (112265).

Note URLs follow the pattern
`/infrastructure/infrastructure-core/-/merge_requests/<iid>#note_<id>` and
`/infrastructure/terraform-modules/-/merge_requests/<iid>#note_<id>`.

**Reproducing this analysis.** The corpus was built with `glab api` calls only.
Merge request lists come from `projects/<id>/merge_requests?state=all`, notes
from `projects/<id>/merge_requests/<iid>/notes`, and prbot comments are
selected by testing each note body for the string `prbot:state:`.

Quotations from prbot, from the prompts and from the local reviewer are
reproduced verbatim.
