# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-09-16

### Changed

- Both review agents now default to `au.anthropic.claude-sonnet-5` instead
  of `au.anthropic.claude-sonnet-4-6`. Override with
  `PRBOT_GENERAL_MODEL_ID` and `PRBOT_SECURITY_MODEL_ID` as before. The
  profile routes only through `ap-southeast-2` and `ap-southeast-4`, so the
  Australian residency guarantee is unchanged.
- Sonnet 5 is listed at $2/$10 per million tokens against Sonnet 4.6's
  $3/$15, but Claude 4.7 and later use a tokenizer that produces roughly 30%
  more tokens for the same text. Expect review cost to be about flat, not a
  third lower.

### Fixed

- prbot could not run in GitHub Actions at all. `secrets.GITHUB_TOKEN` is a
  GitHub App installation token, and `GET /user` is not available to one:
  GitHub answers 403, not 200. Scope validation treated that as fatal and
  every run died with `Token scope validation failed: forbidden (403)`
  before a review started. The code already had the right reasoning for
  installation tokens, but it sat behind a 200 the endpoint never returns.
  A 403 now skips the scope check the way GitLab already did for
  `CI_JOB_TOKEN`, while a 401 stays fatal because that means the credential
  is bad, and a throttled 403 stays fatal because that is a real fault.
- `get_authenticated_user()` hit the same endpoint and would have failed the
  run immediately after. It now returns an empty login on 403, which
  `reconcile()` already documents as "identity unknown, match on the marker
  alone". This is weaker than author matching, and the log says so.
- `VCSError` now carries the HTTP status it was classified from.
  `VCSAuthError` covers both 401 and 403, so without it the fix above could
  not tell a bad credential from an endpoint the token may not use.
- Opus 4.6 was priced at $15/$75 per million tokens, which are the retired
  Opus 4.1 rates. Anthropic lists Opus 4.5 and later at $5/$25, so every
  cost estimate and budget check for a run using it was three times too
  high. Two tests now guard the table: each default model must have a real
  pricing entry rather than silently falling back to the Opus upper bound,
  and no entry may exceed that fallback.

- The CI templates and every setup document pinned
  `ghcr.io/nilesuan/prbot:v0.2.0`, an image tag that is never published.
  `build.yml` tags with docker/metadata-action's
  `type=semver,pattern={{version}}`, which strips the leading `v`, so the
  git tag is `v0.2.0` while the image tag is `0.2.0`. Every review run
  failed at the digest step with `not found`. The 16 references are
  corrected and each pin now carries a comment saying why there is no `v`.
  No code and no image change: the published `0.2.0` image is correct and
  is what these references now resolve to.

## [0.2.0] - 2026-09-15

A correctness and hardening release. Several shipped controls did not do what
they claimed; those are fixed, and the tests that would have caught them are
now in CI.

### Added

- Finding outcomes. In `review` mode each finding is one comment
  thread identified by a stable fingerprint, so a repeated finding is
  not posted twice, a finding that goes away is replied to and
  resolved in its own thread, and a thread a human resolved is never
  re-raised. The counts of new, persisting, fixed and human-resolved
  findings reach the audit record and the metrics sinks.
- `PRBOT_CONTEXT_LINES` includes numbered lines of surrounding code
  around each hunk, so the enclosing function is visible to the model.
  Defaults to 0.
- A diff larger than `PRBOT_MAX_DIFF_TOKENS` is reviewed in several
  passes and the findings merged, instead of the run being refused.
- Metrics emission: a `review.metrics` event always, a JSON lines file
  via `PRBOT_METRICS_FILE`, and CloudWatch via
  `PRBOT_METRICS_NAMESPACE`. No sink can fail a review.
- Finding suppression via `[[prbot.suppress]]`, matching on check family,
  path and a severity ceiling. A reason is required, and the number of
  suppressed findings appears in every comment and in the audit record.
- `PRBOT_REVIEW_MODE=review` submits a platform review: findings land on
  their lines as inline comments, and the verdict reaches the pull
  request rather than only the exit code. Default is unchanged.
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

- `PRBOT_MAX_DIFF_TOKENS` now means tokens per review pass rather than
  the point at which a review is refused. `DiffTooLargeError` and
  `validate_diff_size` are removed: no diff is too large to review,
  only too expensive, which `BudgetExceededError` already covers with
  the same exit code.

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

- The container could not take arguments: with only a `CMD`,
  `docker run prbot --version` replaced the command and failed with
  "executable file not found". It now has an `ENTRYPOINT`.
- `pip`, `setuptools` and `wheel` were present in the runtime image
  despite S86, because the base image carries them in the system
  interpreter. Both were found by running the container smoke tests,
  which were skipped by default and which CI now runs.

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

- Datamarking identified diff structure by prefix, so a deleted line
  whose text began with `-- ` impersonated a file header and reached
  the model unmarked. Structure is now identified by position.
- File paths, which a contributor chooses and which may contain
  arbitrary printable text, are datamarked like any other content.
- Inline comment bodies now pass through secret redaction, which
  previously covered the summary comment only.
- The implicit `.prbot.toml` search is refused in CI, and the GitLab
  template sets `GIT_STRATEGY: none`, so the branch under review can
  no longer choose its reviewer's configuration.
- The fork gate is an allowlist of OWNER, MEMBER and COLLABORATOR
  rather than a blocklist of two values out of eight.
- Review threads are matched by author as well as by marker, so a
  pasted finding marker cannot suppress or resolve a finding.
- The SSRF guard resolves hostnames instead of allowing anything that
  is not a literal IP, and pagination compares scheme, host and port
  rather than host alone.
- Table cells escape the backslash before the pipe, and model-emitted
  URLs are rendered as code rather than links.
- Context fetches are bounded at 2 MiB per file.

- Refreshed the pinned base image and applied Debian security updates
  on top of it, and bumped urllib3 to 2.7.0. The Trivy gate went from
  61 CRITICAL/HIGH findings to 0. The digest pin fixes what is built
  from; it does not stop the packages inside it ageing, and the last
  successful build was six months old.
- Cleared the four remaining transitive advisories in one lockfile pass:
  idna 3.11 to 3.19, cryptography 46.0.5 to 50.0.1, pygments 2.19.2 to
  2.21.0 and requests 2.32.5 to 2.34.2. Only idna reaches the shipped
  image, where it closes CVE-2026-45409; the other three are in the
  development closure. Trivy now reports no advisory at any severity
  against `uv.lock`, and none at CRITICAL or HIGH against the image.
- pytest 8.4.2 to 9.1.1, closing GHSA-6w46-j5rx-g56g (vulnerable tmpdir
  handling). This one needed a `pyproject.toml` change rather than a
  lockfile bump, because `pytest<9` was a declared ceiling; pytest-asyncio
  moves to 1.x with it, since 0.x caps pytest below 9. Test tooling only,
  so the shipped image is untouched.

- Fork reviews are gated before the OIDC role is assumed. The previous gate
  used `exit 0` inside a step, which does not stop later steps.
- Image signature verification is pinned to this repository's workflows and
  can fail the job.

## [0.1.0]

Initial implementation.
