# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-09-15

A correctness and hardening release. Several shipped controls did not do what
they claimed; those are fixed, and the tests that would have caught them are
now in CI.

### Added

- A commit that has already been reviewed is skipped rather than
  reviewed again, carrying the previous verdict through to the exit
  code. `PRBOT_FORCE_REVIEW` or `--force-review` overrides it.
- Configurable agent roster. Adding a reviewer is configuration rather
  than a change to three modules, and each agent may set its own model.
- Optional adversarial agent (`X-*` checks), which asks for a concrete
  failure scenario rather than a category match, and drops any finding
  that does not carry one. Off by default: a third agent is roughly 50%
  more spend per review.

- CI workflow running `ruff check` and `pytest` on every push and pull
  request. Nothing previously ran either.
- Structured output enforcement: `FINDING_JSON_SCHEMA` is sent as a forced
  Bedrock tool, with `temperature: 0` and an explicit `maxTokens`.
- `PRBOT_MIN_PASSING_SCORE`, separating the quality score a review must reach
  from the confidence at which a single finding blocks.
- `PRBOT_MAX_OUTPUT_TOKENS` and `PRBOT_DATAMARK_DIFF`.
- Real cost tracking: token counts are priced and recorded on the audit
  record, and compared with the budget after the run.
- Retry with jitter for VCS rate limits, server errors and transport faults,
  honouring `Retry-After`.
- Deduplication of findings both agents report, recording the agreement.
- End-to-end tests covering the pipeline from configuration to posted comment,
  and tests for adapter selection and base URL resolution.
- `scripts/measure_datamarking.py` for comparing datamarking strategies.

### Changed

- Exclusion patterns use gitignore semantics via `pathspec`. Patterns such as
  `vendor/**` and `**/node_modules/*` previously matched nothing.
- All log records, from stdlib logging as well as structlog, go through one
  redaction processor and render as JSON on stdout.
- Findings are validated against whole hunks rather than added lines only, so
  a finding about a deleted or context line is no longer penalised.
- Secret and PII redaction requires evidence that a value is sensitive, keeps
  labels where they aid the reader, and covers every prose field of a finding.
- Model output is sanitised before entering the posted comment.
- Datamarking preserves hunk headers, file headers and line prefixes.
- Token scope validation runs, and skips rather than fails for token types
  that cannot report their own scopes.
- The IAM policy covers every geographic inference profile prefix and the
  foundation models a cross-region profile routes to.
- GitLab OIDC uses boto3's native web identity support instead of an AWS CLI
  that is not in the image.
- Review workflows gate at job level, verify a pinned signer identity, and run
  the digest they verified.
- `tenacity` removed as an unused dependency; `pathspec` added.

### Fixed

- `dry_run` could not be set from an environment variable or TOML.
- List-valued settings could not be set from environment variables.
- An unexpected exception exited 1, indistinguishable from a blocking review.
- A non-JSON body or a malformed pagination header crashed rather than raising
  a typed error.
- GitHub's rate-limit 403 was classified as an auth failure.
- Pagination was unbounded and followed a server-supplied URL to any host with
  the token attached.
- The region pattern rejected GovCloud and ISO regions; the `au.` profile
  group excluded Melbourne.
- A large text file whose patch GitHub omits was dropped as binary.
- Documentation defaults, examples and architecture no longer contradict the
  code.

### Security

- Fork reviews are gated before the OIDC role is assumed. The previous gate
  used `exit 0` inside a step, which does not stop later steps.
- Image signature verification is pinned to this repository's workflows and
  can fail the job.

## [0.1.0]

Initial implementation.
