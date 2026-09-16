# Case study: prbot approved a live-infrastructure destroy at 100/100

**Date:** 2026-09-16
**Subject:** `infrastructure/infrastructure-core!194` (GitLab project 220), IOPS-1479
**prbot version under test:** image `ghcr.io/nilesuan/prbot:0.4.0`, digest
`sha256:49c3a14457f5b5584bfed7c285df6c41662f9ae5e150eb5673623cfe06566d89`

## The one-paragraph verdict

prbot reviewed an OpenTofu change three times. On the first two commits it
returned `verdict=APPROVE score=100 findings=0 hidden=0`. A controlled
experiment run against the real `terraform-provider-aws` 5.100.0 proves the
code it approved would have destroyed and recreated a live, organization-wide
IAM Access Analyzer on the first apply after import. A separate multi-agent
review of the same commit returned 13 findings and REQUEST CHANGES, and the
author fixed the defect before merge. prbot's miss was **not** caused by its
lack of a repository checkout. Everything needed to find the defect was in the
four-file diff prbot already had. It missed because neither of its two check
specifications contains a single infrastructure-as-code category, so the diff
matched nothing it was asked to look for.

## Files

| File | Contents |
|------|----------|
| `findings.md` | Root-cause analysis. Four independent causes, ranked. |
| `evidence.md` | Every factual claim with its source, separated into verified and unverified. |
| `recommendation.md` | Ordered remediation, cheapest first, with the measurement gate for each. |
| `repro/` | Runnable reproduction of the destroy proof. |

## Why this is in `research/` and not `docs/`

`docs/` holds setup guides that a user follows. This is an evidence record
behind a design change, in the same shape as
`research/automated-pr-review-checklist/`. Nothing here is a user-facing
instruction.

## Status

Analysis only. No prbot code was changed as part of this work. The
recommendations in `recommendation.md` are unimplemented and unmeasured.
