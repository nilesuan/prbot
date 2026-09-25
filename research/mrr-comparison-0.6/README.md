# prbot 0.6.0 against a verified local review, side by side

**Date:** 2026-09-24
**prbot version under test:** 0.6.0 (image digest
`sha256:39f4f7fc9305b4dc273e6c4b613a5c735b0f703a6798f63fe09f2dcdd75638d3`),
three agents (general, security, iac), all on `au.anthropic.claude-sonnet-5`
**Compared against:** a local five-stage review (static analysis, three
reviewers, cross-verification against the source, calibration, formatting)
run on the same head commits
**Follows:** [`../production-review-audit/`](../production-review-audit/),
which measured 0.4.0 and 0.5.2. This audit checks what 0.6.0 changed and what
it did not.

## The one-paragraph verdict

0.6.0 fixed the scoring dead zone the previous audit found, and the new iac
agent is now the most productive of the three. prbot still misses most of
what a verified review catches, and the three largest causes are mechanical
defects in prbot rather than model quality. It sends the model far more text
than it means to, and above 400k input tokens an agent returns an empty
findings array 80% of the time. It hides 81% of what the agents do produce
without recording what was hidden. And it treats a reworded finding as a new
one, so one defect opens a fresh blocking thread each time the model phrases
it differently. The first and third are fixed on the branch that carries this
write-up.

## What was pulled

- The `review.audit` record from every prbot job on
  `infrastructure/terraform-modules` MRs 249-270 and
  `infrastructure/infrastructure-core` MRs 200-209: 48 jobs, 47 with an audit
  record (the other did not reach review), 141 agent calls, $74.41 in total.
- prbot's comments and threads on the same MRs.
- The local review's calibrated findings for the seven MRs both reviewed:
  terraform-modules 267, 268, 269, 270 and infrastructure-core 207, 208, 209.
- The prbot source at `17ce356` (0.6.0 plus nothing), and MR 267's real diff
  and files, to reproduce the prompt size offline.

Only the GitLab API was used. No repository was modified to collect this.

## Side by side

| MR | prbot score, findings shown | Local review | Overlap |
|---|---|---|---|
| core 208 | 100, 0 shown of 4 | 78, a critical and two highs | none |
| modules 269 | 97, 1 of 9 | 76, a high proven by mutation test | 1 finding |
| core 207 | 100, 1 (info) of 10 | 91.8, one medium | none |
| core 209 | 94, 2 of 11 | 86, NOT READY on a failing validate job | 1 finding |
| modules 270 | 97, 1 of 12 | 94.7, two lows | none |
| modules 268 | 97, 2 of 8 | 94, four lows | none |
| modules 267 | 100, all three agents empty | 93.8, four lows outside the diff | none |

Two of the local review's 38 findings also appeared in prbot's output. The local review's
largest findings on 208 were facts outside the diff - a module pinned to a tag
that did not exist yet, and a new test file no CI shard runs - which prbot
cannot see because it reads the diff and nothing else. On 207 and 269 the
decisive facts sat in other files of the same repository (routing and
endpoint definitions), which prbot does not fetch either.

The local review is not free of fault. On one MR its orchestrator could not
dispatch sub-agents and ran the verification itself, so that verdict had no
independent check, and it cost far more per review. prbot's real advantages
are that it runs on every push, costs $1.58 a review on average, and retires
its own stale threads.

## Across all 47 prbot runs

| Measure | Value |
|---|---|
| Scores | 32 of 47 exactly 100; none below 92 |
| Verdicts | 21 APPROVE, 26 COMMENT, 0 REQUEST_CHANGES |
| Findings after de-duplication | 140 |
| Reported | 3 |
| Borderline (shown) | 24 |
| Hidden (count only) | 113 (81%) |
| Runs where every agent returned nothing | 8 |
| Findings by agent | iac 60, general 53, security 27 |
| Security agent calls returning nothing | 31 of 47 |

Empty responses by input size, per agent call:

| Input tokens | Calls | Empty (34 output tokens or fewer) |
|---|---|---|
| under 100k | 42 | 16 (38%) |
| 100k-250k | 24 | 7 (29%) |
| 250k-400k | 54 | 22 (40%) |
| 400k and over | 21 | 17 (80%) |

This is a correlation measured on one corpus, not a proven cause. Some small
diffs are genuinely clean. The jump above 400k is large enough to act on, and
the mechanism that produces the large inputs is a defect on its own terms,
below.

## Causes, ranked

### 1. The prompt is many times larger than the chunker thinks (fixed here)

Two defects compound.

`hunk_span` returned one range from a patch's first hunk to its last, and
the context excerpt was that range widened by `context_lines`. Any file
edited near both ends was sent whole. On MR 267 the excerpt was 8,112 lines
for 843 changed lines; the largest test files were sent at 924 of 969, 904 of
904 and 791 of 791 lines.

The chunker then sized each file by its raw patch, so neither the excerpt nor
datamarking counted towards `max_diff_tokens`. Datamarking makes MR 267's diff
2.31 times longer. The chunker estimated the diff at 23,257 tokens against a
100,000 limit and sent it in one call; Bedrock billed 635,393 input tokens to
the general agent alone, which returned 34 output tokens.

The validator already assumed the right thing: it measures a finding's
distance to the nearest hunk against `context_lines`, so the code between two
distant hunks was never counted as shown. It was only paid for.

Fixed by one window per hunk (merged where they overlap or touch, with the
gap marked) and by sizing each file from the block `build_user_prompt`
actually renders. On MR 267's real files the estimate becomes 217,913 and the
diff splits into three chunks.

**Still open:** `estimate_prompt_tokens` assumes 4 characters per token. The
billed count on MR 267 implies well under that for datamarked text, since the
eight-hex-digit marker tokenises badly. Measure it against Bedrock's reported
`inputTokens` across several runs before changing the constant.

### 2. Four-fifths of findings are hidden, and nothing records them

A medium or low finding below `threshold - 15` (55 at the default) becomes a
count. Its check, file, line and text are not in the comment, the job log or
the audit record. There is therefore no way to tell whether prbot found the
high that the local review found on 269 among its eight hidden findings.

All four prompts tell the model "Nothing you report is discarded for want of
confidence" (`general.md:116`, `security.md:92`, `iac.md:175`,
`adversarial.md:135`). For the hidden band that is untrue.

Recommended: write every finding, hidden ones included, into `review.audit`,
and either render hidden findings in a collapsed section or make the prompt
say what actually happens to them.

### 3. A reworded finding opened a new thread (fixed here)

The fingerprint is `sha256(file, check_id, normalised title)`, and the model
rewords titles between runs. On MR 270 one `IAC-REPLACE-01` at
`aws_bedrock_invocation_logging/main.tf:88` was posted under two fingerprints
in three pushes: reported, missing from the next run and so resolved, then
reported again after a rebase that did not touch the file. Each new
fingerprint is a new discussion, and both consuming repositories block merges
until every discussion is resolved.

Fixed by falling back, when no fingerprint matches, to an unclaimed thread of
prbot's own on the same file whose header names the same check and whose
anchored line the finding covers - the scorer's existing rule for one check
on the same lines being one defect.

**Still open:** a finding absent from a single run is resolved as fixed. With
a model this variable, resolving only after two consecutive absences would
stop the reported-resolved-reported cycle; that needs a miss count in the
state record.

### 4. Confidence does not separate true from false

Self-reported confidence sits between 30 and 60 for findings the local review
verified at 85 to 97. A fixed threshold on an uncalibrated number cannot sort
them. prbot already records `findings_fixed` and `findings_human_resolved` per
fingerprint; with fingerprints now stable across rewording, those are usable
labels for calibrating the threshold per check family. A second pass that
re-checks each finding against fetched files would help more.

### 5. Nothing outside the diff is visible

The token already has `api` scope and the adapter already fetches files at
the head commit. Bounded read-only tools - fetch a file, check a ref exists,
read the pipeline status - would let the agents settle the questions the
local review settled, inside the existing budget cap.

### 6. Smaller items

- Every agent call first fails with "Model au.anthropic.claude-sonnet-5
  rejects sampling parameters" and is retried: three wasted calls per run.
  Record the capability per model instead of discovering it on each call.
- The context module docstring still says `context_lines` defaults to 0; it
  has defaulted to 40 since 0.6.0.

## What to measure after this branch ships

1. Input tokens per agent call and the empty-response rate above and below
   400k, against the table above.
2. Findings produced per review, and the share hidden.
3. Threads opened per MR across pushes, and how many close as "no longer
   reported" and then reappear.
