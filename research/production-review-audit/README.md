# Audit: what prbot actually posted across 34 production reviews

**Date:** 2026-09-16
**Subject:** every prbot review posted to `infrastructure/infrastructure-core`
(project 220), `infrastructure/terraform-modules` (217) and
`platform/terraform-modules` (177)
**prbot versions under test:** 0.4.0 for most of the corpus, 0.5.2 for the last
few reviews, split at 16:03 on 2026-09-16

## The one-paragraph verdict

prbot is posting and finding almost nothing. Across the 19 reviews it posted to
the two `infrastructure/` repositories, its agents produced 40 findings and
exactly 1 reached the findings table, with 18 of the 19 reviews scoring exactly
100/100. Two separate mechanisms cause this: the check specifications contain no
infrastructure-as-code category, so on Terraform diffs the agents often return
nothing at any confidence, and whatever they do return is then scored to zero
because a finding below the confidence threshold deducts exactly `0.0`. prbot
also fails in the opposite direction, scoring a merge request 40 points harsher
than a human-grade review of the identical commit by counting one defect three
times. The local reviewer is better at reviewing; prbot is better at showing up.

## Files

| File | Contents |
|------|----------|
| `summary.md` | Plain English. Which reviewer is better, why, and how to close the gap without a checkout. |
| `analysis.md` | Full evidence. Ten ranked improvements, per-finding numbers, source citations, evidence index. |

## Relationship to `../mr194-review-miss/`

The two are complementary and should be read together.

- `mr194-review-miss/` is depth: one merge request, the audit records, a
  runnable proof that prbot approved a live-infrastructure destroy, and the
  root cause that follows from it. It establishes the **largest** cause,
  which is missing infrastructure-as-code check coverage.
- This directory is breadth: 34 reviews across three projects, the aggregate
  rate at which findings are suppressed, the cross-file de-duplication defect,
  and the one clean same-commit head-to-head against the local reviewer.

Where they overlap they agree, with one exception recorded here for honesty: an
earlier draft of `summary.md` and `analysis.md` concluded that MR 194 was not a
missed finding. That was wrong. It was corrected on 2026-09-16 after the case
study was found, and the reason the error was possible is itself documented in
`analysis.md` under "Ordering: what the merge request notes alone cannot show".

One item in `mr194-review-miss/findings.md` is version-dependent and worth
flagging. Its section 7 states that `allow_failure: true` means prbot can never
gate a merge. That held for 0.4.0, which posted a summary comment only. Under
0.5.x with `PRBOT_REVIEW_MODE: review`, and with
`only_allow_merge_if_all_discussions_are_resolved=true` verified on both
projects, an unresolved prbot discussion would hold the merge. The gate now
exists in principle. It remains inoperative in practice, because prbot has
posted zero inline comments across all 2,272 notes examined.

## Method

GitLab REST API only. No repository was cloned. Merge request lists from
`projects/<id>/merge_requests?state=all`, notes from
`projects/<id>/merge_requests/<iid>/notes`, and prbot's own comments selected by
testing each note body for the string `prbot:state:`. Pre-rebase commits, which
`merge_requests/<iid>/commits` does not return, were resolved through
`repository/commits/<sha>`.

## Status

Analysis only. No prbot code was changed as part of this work. Every
recommendation in `analysis.md` is unimplemented and unmeasured.
