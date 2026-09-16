# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2026-09-16

Findings now land on the lines they are about, and everything prbot writes has
one fixed shape. The contract lives in
[`docs/review-output-template.md`](docs/review-output-template.md): the prompts
and the renderer are implementations of that document rather than three places
that each decide the format for themselves.

### Changed

- **`PRBOT_REVIEW_MODE` now defaults to `review`.** Each finding that lands on
  a line the diff covers is posted as a comment on that line. Set
  `PRBOT_REVIEW_MODE=comment` for the previous behaviour of one summary
  comment, which is what a token that cannot submit reviews needs.
- **Every issue has one shape, everywhere.** Header, then `**Problem:**`,
  `**Impact:**` and `**Fix:**` in that order, rendered by a single function so
  an inline comment and a summary entry cannot drift apart. The old
  `**How it breaks:**` and `**Suggestion:**` labels are gone.
- **The summary comment indexes rather than repeats.** It carries the verdict,
  a severity count line, and one table row per issue giving its check, location
  and confidence. The per-finding detail sections underneath the table are
  gone: that detail is in the inline comment. A finding whose lines fall
  outside the diff has no inline thread, so the summary carries it in full
  under `Not anchored to a line` - detail is written in exactly one place.
- **`failure_scenario` is required of every agent**, not only the adversarial
  one, because it is rendered as the `**Impact:**` line. The general and
  security check specs ask for it, and the schema lists it as required. A
  finding that arrives without one still renders, minus that line, rather than
  printing a bare label.
- **Every check spec carries `## Reporting Rules`**, which is the list of what
  may never appear in a finding: praise, a summary of what the change does,
  restating the code, questions to the author, hedging stacks, coaching, and
  emoji or links in the prose.
- The empty-review body is now `No issues found.` rather than
  `_No findings to report._`, and the em dash separators in the header, agent
  status and error lines are now middots.
- An oversized comment is rebuilt with less in it rather than cut apart as
  rendered markdown, so every attempt is well-formed and each thing dropped is
  stated. `truncate_comment()` is now the last-resort hard cut only and takes
  `(comment, limit)`.

### Fixed

- **The summary is no longer the platform review body.** A review object cannot
  be found and rewritten by a later run, so review mode reposted the whole
  summary on every push and left the state record somewhere `find_bot_comment`
  does not look, which is what the unchanged-commit skip reads. The summary is
  now one comment, rewritten in place, in both modes; the review carries the
  verdict, the inline comments and a short body pointing at it.
- **A review event the platform refuses no longer fails the run.** GitHub does
  not permit the Actions token to approve a pull request and nobody may approve
  their own, so an `APPROVE` verdict returned 422 and exited 3. The adapter now
  retries without the inline positions, then as a plain `COMMENT`, keeping as
  much as each attempt allows.

### Added

- `docs/review-output-template.md`, the output contract, and
  `tests/review/test_output_template.py`, which holds the renderer to it.
- `unanchored_findings()` and `format_review_event_body()` in
  `prbot.review.formatter`.

## [0.4.1] - 2026-09-16

The GitLab CI template had never worked. Both defects were found by running
it on two real GitLab projects, and neither was reachable from the GitHub
side: that path calls `docker run` directly and never asks for a shell, and
it passes the region through the workflow rather than through GitLab
variable expansion. Nothing in the container changed.

### Fixed

- The template did not clear the image entrypoint. The image declares
  `ENTRYPOINT ["prbot"]`, and GitLab appends its shell-detection command
  rather than replacing the entrypoint, so the container ran
  `prbot sh -c '...'` and argparse refused it:
  `prbot: error: unrecognized arguments: sh -c if [ -x /bin/bash ]`. The job
  exited 2 before a review started. The template, the README example and
  both `docs/gitlab-setup.md` examples now set `entrypoint: [""]`.
- Behind that, the template set `PRBOT_AWS_REGION: $PRBOT_AWS_REGION`, which
  is self-referential. GitLab does not expand it, and because job-level
  variables take precedence the definition shadowed the project or group
  variable with the literal string, so prbot refused to start with
  `aws_region must match format like 'us-east-1': '$PRBOT_AWS_REGION'`.
  Setting the variable correctly could not help, because the job overrode it
  with a value that cannot expand. The line is removed; a CI/CD variable of
  that name already reaches the job.

### Added

- Two tests that would have caught the above. One asserts every image in the
  GitLab template clears its entrypoint, and first asserts the Dockerfile
  still declares one so it cannot become vacuous. The other rejects any
  variable whose value is only a reference to itself, in `$FOO` or `${FOO}`
  form. The previous tests read the `image` key only as a string, so a bare
  string and a mapping with the entrypoint cleared were indistinguishable.

## [0.4.0] - 2026-09-16

The container now runs Python 3.14, and the dependencies it ships crossed two
majors. Nothing in prbot's own interface changed: no CLI flag, environment
variable, config key or exit code moved, and a review behaves as it did in
0.3.2. The version is minor because what runs the tool underneath is not the
same runtime any more.

### Changed

- Base image `python:3.12-slim` to `python:3.14-slim`, giving Python 3.14.7
  in the container. The full suite was run under 3.14 before taking it, not
  only inside the image, and reports the same 957 passed as on 3.12.
- Runtime dependencies bumped: pathspec 0.12.1 to 1.1.1, structlog 25.5.0
  to 26.1.0, boto3 and botocore 1.42.64 to 1.43.94, pydantic 2.12.5 to
  2.13.5, s3transfer 0.16.0 to 0.19.2.
- pathspec 1.0 renamed its registered pattern factory, so the exclusion
  matcher now asks for `gitignore` rather than `gitwildmatch`. The names are
  inverted between majors (0.x deprecates `gitignore`, 1.x deprecates
  `gitwildmatch`), so the floor is pinned at `pathspec>=1,<2` instead of
  branching on the installed version. Matching behaviour is unchanged.
  Taking the bump without this refuses every exclusion pattern in the
  project: `_compile` turns any exception into `ConfigError`, so the
  deprecation arrives as a hard failure rather than a warning.

### Fixed

- The Dockerfile stripped pip, setuptools and wheel from the runtime image
  using a hardcoded `python3.12` site-packages path. Any base image bump
  pointed every `rm` at a path that does not exist, and `rm -rf` succeeds on
  a missing path, so the build stayed green while the installers survived.
  On a `python:3.14-slim` base that put pip back in the image, and pip
  vendors its own copy of msgpack, so the image gained two HIGH advisories
  (msgpack and setuptools) that the 3.12 image did not have. The path is now
  asked of the interpreter, and the build fails if either package survives
  the strip.
- `test_no_pip_in_runtime` could not see this. It runs `which pip`, and
  `/usr/local/bin/pip` is not version-specific, so the binary was removed
  while the package stayed. A new test checks the filesystem instead, which
  is what Trivy scans. It deliberately does not check `import pip`: PATH
  puts the virtual environment's interpreter first and that cannot see
  system site-packages, so an import check passes even when every file is
  still present.

### Security

- Every GitHub Action bumped, each pinned to a full commit SHA that was
  checked against its published tag: `actions/checkout` v4.2.2 to v7.0.1,
  `aws-actions/configure-aws-credentials` v4.0.2 to v6.2.4,
  `docker/build-push-action` v6.18.0 to v7.3.0, `docker/login-action` v3.4.0
  to v4.6.0, `docker/metadata-action` v5.7.0 to v6.2.0,
  `docker/setup-buildx-action` v3.10.0 to v4.3.0,
  `sigstore/cosign-installer` v3.8.1 to v4.1.2, `aquasecurity/trivy-action`
  0.35.0 to 0.36.0. `metadata-action` v6 changes its default runtime to Node
  24 and how `#` is handled inside list inputs; it does not change semver tag
  generation, which every image pin depends on.
- Development dependencies bumped: pytest-cov 6.3.0 to 7.1.0, respx 0.22.0
  to 0.23.1, ruff 0.15.5 to 0.16.7. None reach the container.

## [0.3.2] - 2026-09-16

### Added

- `.github/dependabot.yml`. Nothing watched dependencies automatically
  before; the base image went six months without a rebuild and reached 61
  CRITICAL/HIGH Trivy findings in the meantime. Three ecosystems are
  covered: `uv` for the Python dependencies, `docker` for the digest-pinned
  base image, and `github-actions` for the SHA-pinned actions.
- Updates are grouped by whether a package reaches the shipped container,
  which is the distinction that matters when triaging. Of the five
  advisories open in September 2026, only idna was in the runtime closure.
  Security updates are split the same way, so a fix that reaches users is
  never queued behind a test-only one.
- `tests/test_dependabot_config.py` asserts the configuration's shape. A
  malformed `dependabot.yml` does not fail loudly: Dependabot simply stops
  opening pull requests, which looks the same as having nothing to update.

### Known issues

- A security advisory whose fix sits above a declared version ceiling
  produces an alert but no pull request, because Dependabot will not widen a
  constraint the project set and the `uv` ecosystem has no
  `versioning-strategy` option yet (dependabot/dependabot-core#12162). Every
  direct dependency here has an upper bound, so triage from the alert list
  rather than the pull request list. `GHSA-6w46-j5rx-g56g` was exactly this
  case. The configuration documents the check to run.

## [0.3.1] - 2026-09-16

### Fixed

- Both review agents failed on every run under 0.3.0. Claude Sonnet 5
  refuses `temperature` and `top_p` outright, answering
  `ValidationException: \`temperature\` is deprecated for this model`, and
  prbot always sent `temperature: 0`. The call is now retried once with
  `maxTokens` alone when a model rejects a sampling parameter, matched on
  the parameter name rather than a model list so it does not go stale.
  `maxTokens` and the forced tool survive the retry. Any other
  `ValidationException` still fails immediately.
- An agent that gave up never logged why. The audit record stores only
  `status="error:AgentError"` and the verdict logs "Both agents failed"
  with no cause, so the failure above was indistinguishable from a
  permissions problem without reproducing it by hand. Every terminal agent
  failure now logs its type and message.

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
