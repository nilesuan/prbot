# prbot against the local reviewer: which is better, and how to close the gap without cloning

Plain-English companion to [`analysis.md`](analysis.md), which carries the full
evidence, the per-finding numbers and the source citations behind every claim
here. Read it alongside [`../mr194-review-miss/`](../mr194-review-miss/README.md),
the single-case study that proves the worst outcome in this document.

Analysis date: 2026-09-16. prbot versions under test: **0.4.0 for most of the
corpus, 0.5.2 for the last few reviews**, split at 16:03 on 2026-09-16 when
MR 195 and MR 247 moved both repos off 0.4.0. Built from 2,272 merge request
notes across 281 merge requests in `infrastructure/infrastructure-core`,
`infrastructure/terraform-modules` and `platform/terraform-modules`, pulled
over the GitLab REST API. Nothing was cloned.

---

## The short answer

**The local reviewer is much better at reviewing. prbot is better at showing
up.** They are barely competing on the same axis at the moment.

The gap is almost entirely about what each one is allowed to see, and what
survives the scoring afterwards. It is not about the model. prbot's agents are
working: across the 19 reviews it posted to the two repos they produced **40
findings, and exactly 1 of them reached the findings table**. Eighteen of the
19 reviews scored exactly 100/100.

---

## Why the local reviewer wins

| | Local reviewer | prbot |
|---|---|---|
| What it sees | The whole repository on disk | Only the changed lines, with **zero** lines of surrounding context |
| What it runs | `tofu fmt`, `tofu validate`, the full `tofu test` suite | Nothing |
| Reads the Jira ticket | Yes | No |
| Checks its own findings | Yes, a second verification pass | No |
| What it publishes | Everything, with the confidence printed next to it | Only findings it is at least 70% sure about |

Given that table, prbot finding nothing is the expected outcome rather than a
surprise.

A concrete example. On `core!194` the local reviewer posted 13 findings at
confidences of 22%, 38%, 41%, 45%, 52%, 54%, 60%, 64% and 71%, each with the
number shown so the author could judge for themselves. It proved one of them
by reading `.gitlab-ci.yml:476`, a file that was not in the diff at all, and it
checked whether documents cited in the change actually existed. prbot cannot
produce any of that: it deletes findings for files outside the diff, and it
hides anything below 70%.

---

## The correction worth knowing about

"prbot is too soft" is the wrong lesson. It is miscalibrated in **both**
directions.

On `platmod!68` there is exactly one commit, `2b74838e`, with no changes in
between the two reviews, so it is a genuinely fair comparison of the same code:

- **prbot: 54/100, REQUEST_CHANGES.**
- **Local reviewer: 94/100, APPROVE.**

Forty points apart, opposite verdicts, and prbot was the harsher one. It got
there by counting a single defect three separate times, because its
de-duplication only merges findings that share an identical file path
(`scorer.py:58`).

So prbot hides almost everything, and then over-punishes the few findings that
get through.

It also fails in the other direction, and this is the worse half. On `core!194`
prbot reviewed three commits and **approved the first two at 100/100 with zero
findings**. The code it approved would have destroyed and recreated a live
organization-wide IAM Access Analyzer on the first apply after import, which is
proven by a runnable reproduction in
[`../mr194-review-miss/repro/`](../mr194-review-miss/repro/README.md). The local
reviewer scored that same commit 64.93/100 and filed 13 findings, and the author
fixed the defect before merge.

An earlier draft of this document said MR 194 was not a miss. That was wrong,
and it was wrong for an instructive reason: prbot overwrites its own comment, so
GitLab preserves only its last verdict, and the merge request commits endpoint
hides pre-rebase commits. Both approving runs are invisible unless you read the
audit records. The bot's own lack of an audit trail is what concealed its worst
result.

---

## Where prbot genuinely wins

It runs automatically on every merge request, in roughly 20 to 30 seconds, for
pennies, with nobody having to remember to invoke it. The local reviewer only
runs when you run it. That is a real advantage and it is worth keeping.

---

## The root cause, in one paragraph

Your prompt tells the model "Only report findings with confidence >= 50"
(`general.md:103`). Your scorer throws away anything under 70
(`config.py:239`), and gives a zero score deduction to everything it throws
away (`scorer.py:133`). That 20-point gap is where 39 of the 40 findings died.
Worse, the prompts also instruct the model to "lower the confidence until it is
filtered out" (`general.md:74-75`, `security.md:68-69`), which is an
instruction to hide its own work. The data shows it obeying precisely: **35 of
the 68 suppressed findings sit at exactly 55%**, the lowest value that still
renders as borderline rather than vanishing entirely.

---

## How to fix it without cloning

`GIT_STRATEGY: none` is there for a good reason, documented in your own CI
template: a checkout would let the branch under review drop a `.prbot.toml`
into the job's working directory. **Keep it.** You do not have to give it up to
fix any of this.

### 1. Turn on file context. One CI variable, no code change.

This is the one to do first, and the capability is already built and switched
off.

prbot already knows how to read any file in the repo over the API.
`get_file_content()` at `src/prbot/vcs/gitlab.py:150` calls
`/api/v4/projects/{id}/repository/files/{path}/raw?ref={sha}`, which is the
same endpoint used to read your CI templates during this analysis without
cloning anything. It is declared on the adapter Protocol
(`src/prbot/vcs/protocol.py:37`) and implemented for GitHub as well
(`src/prbot/vcs/github.py:188`).

It is only ever called when `context_lines > 0` (`cli.py:460-466`), and
`context_lines` defaults to `0` (`config.py:256`). Neither repo overrides it,
and neither repo has a `.prbot.toml` at all, so both are running the bare
default.

The effect today: prbot reviews naked diff hunks with no surrounding code, and
then docks itself 40 confidence points whenever a finding touches a line just
outside a hunk (`validation.py:20`), a line it was never shown.

`PRBOT_CONTEXT_LINES` is a recognised integer override, so setting it as a
group CI variable on `infrastructure/` gives prbot full surrounding file
context with no working copy and no code change.

### 2. Fix the scoring mismatch. Prompt and threshold only.

Align the prompt's reporting floor with the scorer's threshold, make the score
deduction continuous in confidence rather than a cliff at 70, and delete the
"lower the confidence until it is filtered out" instruction. None of this
touches a repository.

Calibrate the new threshold rather than guessing it. You already have **298
local-reviewer reviews** sitting in these repos as a labelled comparison set,
pullable over the API exactly as this analysis was.

### 3. Let prbot read the rest of the pipeline. Still no clone.

prbot does not need to run `tofu test` to get the static-analysis signal the
local reviewer has. **Those jobs already run in the same pipeline.** prbot can
read their status and logs over the GitLab jobs API, which gets it "186 passed,
0 failed" without a checkout.

The same trick applies to the ticket. The ID is already in the merge request
title, for example `IOPS-1479: ...`, and the ticket is one API call away. That
unlocks the whole spec-compliance family of findings the local reviewer
produces and prbot has no checks for at all.

### 4. Everything else is pure code inside prbot.

Cross-file de-duplication, a reconciliation line so the numbers in the comment
add up, posting borderline findings inline so your merge gate actually engages,
and keeping review history instead of overwriting it. None of these touch a
repository. See [`analysis.md`](analysis.md) for the detail.

Worth knowing on the merge gate: both repos have
`only_allow_merge_if_all_discussions_are_resolved=true` and set
`PRBOT_REVIEW_MODE: review`, so the design is right, but prbot has posted
**zero** inline comments across all 2,272 notes. Only findings in the reported
band are eligible to be anchored to a line, and with one reported finding in 19
reviews there has never been anything to anchor. The gate is currently
decorative, and fixing the threshold fixes it at no extra cost.

---

## If you only do one thing this week

Set `PRBOT_CONTEXT_LINES` as a group CI variable on `infrastructure/`, and
separately, as a one-off probe, drop `PRBOT_CONFIDENCE_THRESHOLD` for a day to
see what is currently being hidden. Then revert the threshold.

That costs nothing and ships today. Be clear about what it is: a measurement,
not the fix. Leaving the threshold low trades this repository's misses for
low-confidence noise everywhere else, and the real defect is that a finding
below the line deducts exactly zero rather than a reduced amount. Fixing the
check coverage, so the agents are asked about infrastructure at all, is the
larger job and the one that would have caught the destroy on MR 194.
