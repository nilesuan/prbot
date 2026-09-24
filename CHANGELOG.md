# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **The context excerpt is a window per hunk, not the whole span between
  them.** One range ran from a patch's first hunk to its last, so any file
  edited near both ends was sent whole. On terraform-modules MR 267 that was
  8,112 excerpt lines for 843 changed lines. Windows that overlap or touch are
  merged, and the gap between the rest is marked.
- **Chunks are sized by the prompt that is sent.** The chunker sized the raw
  patch, so neither the excerpt nor datamarking counted towards
  `max_diff_tokens`: MR 267 was estimated at 23,257 tokens, sent in one call,
  and billed 635k input tokens per agent for an empty review. Above 400k input
  tokens agents returned nothing on 80% of calls across 47 production runs.
  Each chunk's prompt now lists the pull request's other changed files as
  reviewed separately: without it, a chunk holding only `variables.tf` on MR
  269 reported the NACL resources in the other chunk as missing, at critical
  and high severity. The header, description and file list every chunk
  repeats count towards the limit too. In the prompt the description is cut
  to its first 8,000 characters and the list to 200 paths of at most 300
  characters each, because datamarking made a 65,536-character description a
  159,849-token prompt. Paths shown in a prompt lose Unicode line breaks as
  well as ASCII control characters.
- **A reworded finding stays on the thread it already has.** The fingerprint
  includes the title, and the model rewords titles between runs, so one
  defect was posted as a new blocking discussion each time. With no exact
  fingerprint match, a thread on the same file naming the same check whose
  anchored line the finding covers is now taken to be the same defect. Only
  an open thread prbot can prove it wrote is matched this way. With
  `GITHUB_TOKEN` prbot cannot read its own login, so matching stays exact
  there; otherwise anyone could write a thread that takes a real finding. A
  resolved thread is matched only by its exact fingerprint, so a new defect
  of the same check on that line is not filed under someone's resolution.
  When two findings fit one thread the nearer one gets it, and a thread whose
  line is still reported for its check is not closed as fixed.
- **A review is not approved when an agent failed on part of the diff.** A
  review with no findings was approved when every agent failed on one chunk
  and succeeded on another, although nobody had reviewed that chunk's files.
  It is now a COMMENT, and a pass that no agent completed fails the job
  with exit code 3, as every agent failing does. A blocker found in another
  chunk still requests changes.
- **A blocker blocks even when another agent failed.** A critical or high
  finding one agent confirmed, or a score below the passing mark, became a
  COMMENT with exit 0 whenever another agent produced no result at all, so
  making one agent fail waved the other's finding through. Incomplete data
  still never approves, but what it found now blocks, with exit 1.
- **Low-confidence findings are listed and audited, not reduced to a count.**
  A finding below the borderline band became a number, so 113 of 140
  production findings could not be inspected. They are now listed one line
  each in a collapsed "Low-confidence findings, listed but not scored"
  section, and `review.audit` carries every finding's fingerprint, check,
  severity, confidence, band and location (no model prose, per G-11). The
  prompts no longer claim every below-threshold finding counts towards the
  score.
- **A thread prbot resolved is reopened when its finding comes back.** One run
  missing a finding resolved its thread, and the finding returning was then
  counted as a human decision and never raised again. Threads now carry who
  resolved them; one resolved by prbot itself is reopened with a reply, and
  counted as `findings_reopened`. A thread a person resolved is left alone.
  Telling the two apart needs prbot's own login, which `GITHUB_TOKEN` cannot
  read: set the new `PRBOT_BOT_LOGIN` to `github-actions[bot]` there, as this
  repository's workflows and the setup guide now do. GraphQL also gave an
  app's login without the `[bot]` suffix as a thread's author and with it as
  the resolver, so one app read as two people; a Bot's login now carries the
  suffix everywhere.
- **The audit log's finding entries are redacted and capped.** Log redaction
  scanned only top-level values, so a token in a finding's check id reached
  the log, and only the path was capped: a 5,010-character check id was
  logged whole. The redactor now walks nested values, every string in an
  entry is capped at 256 characters, and a check id that is not a short code
  drops its finding.
- **The token estimate is calibrated against billed tokens.** It assumed 4
  characters per token; datamarked prompts measure 1.27-1.91 against
  Bedrock's billed `inputTokens` on 129 production calls, so it undercounted
  every one, by a median of 1.69 times. It is now 1.5 with a 1.2 multiplier,
  which overestimates all 126 distinct measured calls, by a median of 1.26
  times. The measurements are committed as a test fixture, and tests fail if
  the estimate undercounts any of them, overestimates the median by 1.5 times
  or any by 2, or refuses the largest of them at the default budget. Budget
  checks are correspondingly stricter.

### Changed

- **`temperature` is sent only when configured (`PRBOT_TEMPERATURE`).** The
  default model rejects it, so every review made one failed call per agent
  before the real one. It is unset by default; set `0` for a model that
  accepts it. A repository pinned to an older model that relied on
  temperature 0 should set it explicitly.

### Added

- **A verification pass, off by default (`PRBOT_VERIFY`).** One further call
  per chunk checks each finding against the code and replaces its confidence
  with the verdict's; refuted findings are demoted, not deleted, and the
  footer and audit record say what was concluded. On terraform-modules MR 269
  it moved a true finding from 45% to 75%, into the reported band, for 38%
  more cost. When the worst case exceeds the budget it is dropped, after
  reads and before the review.
- **`read_file`, an opt-in tool for reading beyond the diff
  (`PRBOT_TOOL_TURNS`, default 0).** An agent may read other files of the
  repository at the head revision before reporting, bounded to 200 lines and
  20,000 characters per read and 600 lines and 60,000 characters per agent,
  which is what the budget check prices, with binary, generated and excluded
  paths refused and content datamarked. Only a path's canonical spelling is
  read, so `config/./prod.env` cannot reach an excluded `config/prod.env`. At
  most five reads are answered per turn, each inside the review's time budget,
  and a read that fails is reported as unreadable rather than cached as
  absent. An agent that fails after reading keeps what it was billed for in
  the audit record. Turns reuse a Bedrock prompt cache, token usage now
  counts and prices cache reads and writes, and the budget check prices the
  worst case and drops the reads rather than the review when they do not fit.
  Off by default: on terraform-modules MR 269 the agents read 129-286 lines
  each and found nothing a run without reads missed, at 61% more cost.

The evidence is in `research/mrr-comparison-0.6/`.

## [0.6.0] - 2026-09-16

A review-quality release. prbot was running, posting, and finding almost
nothing: across the 19 reviews it posted to two production repositories its
agents produced 40 findings, exactly 1 reached the findings table, and 18 of
the 19 scored exactly 100/100. On one merge request it approved, twice, at
100/100 with zero findings, a change that would have destroyed and recreated a
live organization-wide IAM Access Analyzer on the next apply.

The evidence behind every change here is in `research/production-review-audit/`
and `research/mr194-review-miss/`. Two causes ran through all of it: the agents
were asked the wrong questions, and what they did find was then destroyed by
scoring.

### Added

- **An infrastructure-as-code check specification.** Neither shipped spec
  contained a single infrastructure category, so on a Terraform diff an empty
  findings array was the correct answer to the question asked, and that is what
  came back. `iac.md` adds 24 checks in six families - `IAC-ADOPT`,
  `IAC-REPLACE`, `IAC-SCOPE`, `IAC-PROVIDER`, `IAC-SECRET`, `IAC-TEST` - each
  written as a property of declarative infrastructure rather than of any
  provider, and tested to contain no provider-specific identifiers. Off by
  default; add it to the roster where it earns its place.
- **A findings reconciliation line in the comment footer.** De-duplication, the
  not-in-the-diff drop and suppression rules all removed findings silently, so
  a comment could report "4 findings" in Agent Status and show three with
  nothing accounting for the fourth. The footer now names what became of each:
  produced, merged, outside the diff, suppressed, hidden, shown. Printed only
  when something was actually lost.
- **Preserved review history.** prbot rewrites its own comment, so only its
  most recent verdict survived and what it said about the code before a fix was
  deleted by the fix. The state record now carries up to ten superseded
  verdicts and the comment renders them in a collapsed section. Records written
  by earlier versions have no history and still parse.
- **`PRBOT_AGENTS` and `PRBOT_SUPPRESS` as JSON environment variables.** The
  agent roster is a list of objects, which comma-splitting cannot express, so
  it could only be set from TOML - and a CI run cannot reach a TOML file,
  because the implicit search is refused in CI and `GIT_STRATEGY: none` leaves
  no file to point `--config` at. A repository had no supported way to add an
  agent to its own reviews.

### Changed

- **A finding below the reporting threshold no longer deducts exactly zero.**
  This is why 18 of 19 reviews scored 100/100: with nothing clearing the
  threshold, `total_deductions` was `0.0` by construction. Borderline findings
  now deduct at half their full weight, so uncertainty moves the score without
  deciding the verdict.
- **A critical or high finding is never hidden.** However low its confidence it
  is surfaced as borderline with that confidence printed. It is deliberately
  not promoted into reported, and a low-confidence critical does not block on
  its own. The destroy described above was filed by a human-grade reviewer at
  38% confidence, which prbot would have hidden entirely.
- **Findings are matched on the defect, not on coordinates.** De-duplication
  required an exact file match and a line overlap before it would consider
  whether two reports described the same problem, so one defect was deducted
  once per file it appeared in. That counted a single hardcoded account id
  twice and one unimplemented helper three times, producing 54/100
  REQUEST_CHANGES where a human-grade review of the same commit scored 94/100
  APPROVE.
- **Borderline findings are posted on their line.** Inline comments were built
  from the reported band alone, which almost nothing reaches, so prbot had
  never posted an inline comment while both consuming repositories had review
  mode on and gated merges on unresolved discussions.
- **`PRBOT_CONTEXT_LINES` now defaults to `40`, was `0`.** The API-backed
  retrieval shipped switched off, so every agent reviewed naked hunks and was
  then penalised for citing lines just outside them. This needs no checkout and
  does not weaken the `GIT_STRATEGY: none` boundary. It is a starting point to
  be measured; `0` restores the previous behaviour.
- **The hallucination penalty is charged in proportion to distance.** A flat 40
  points for any gap at all is larger than the whole borderline band and enough
  to silence a 90%-confidence finding over one line of drift. Nothing is
  charged inside the context window the agent was shown, and the charge rises
  with distance beyond it. With context off, the previous behaviour is exact.
- **The prompts no longer teach the model to suppress its own findings.** Every
  spec told it to "lower the confidence until it is filtered out" while the
  scorer discarded everything below the threshold. It shows in the data: 35 of
  68 suppressed findings sat at exactly 55, the lowest value that still
  rendered. All four specs now define confidence as the probability the finding
  is real, say that severity and confidence answer different questions, and
  state that nothing is discarded for want of confidence.

### Upgrading

Scores will fall and more findings will appear. That is the intent: the
previous numbers were produced by a scorer that could not deduct for most of
what it was given. Repositories that gate on `PRBOT_MIN_PASSING_SCORE` should
expect the change and recalibrate rather than raise the threshold back.

Cost per review rises with `PRBOT_CONTEXT_LINES` at 40; set it to `0` to
restore the previous spend while losing the precision it buys.

## [0.5.2] - 2026-09-16

A fix to inline comments on GitLab, found by running 0.5.1 on a real merge
request. GitHub is unaffected.

### Fixed

- **Inline comments failed on GitLab for any line that was not added.** The
  discussion position carried only `new_line`, and GitLab builds a comment's
  line code from the position: for a line that exists in both revisions it
  needs both sides, and refused the rest with
  `400 Bad request - Note {:line_code=>["can't be blank", "must be a valid
  line code"]}`. A finding is anchored to its `line_end`, which is a context
  line whenever the last line it describes was not itself added, so this was
  the common case and not an edge one. The whole point of the release - a
  comment on the line it is about - was therefore failing on GitLab and
  degrading to a warning in the job log.

  `InlineComment` now carries `old_line`, `map_new_to_old()` in
  `diff_parser` derives it from the patch, and the GitLab adapter sends it
  exactly when the line has an old side. Naming an old side for an added
  line is equally invalid, so the key is present only when it is real.

## [0.5.1] - 2026-09-16

A fix to what 0.5.0 does on GitLab, found by running it on two real merge
requests. Nothing changes on GitHub.

### Fixed

- **prbot posted two summary notes on every GitLab merge request.** 0.5.0 made
  the summary its own comment so it could be rewritten in place, but
  `GitLabAdapter.submit_review` still posted the review body as a note, and
  GitLab has no review object for that body to go in. The result was the
  summary's header appearing a second time, with no `prbot:state` marker, so
  `find_bot_comment` could never match it and a fresh copy accumulated on
  every run. The adapter now posts the discussions and the verdict only, and
  `body` is documented as unused where a platform has no review object.

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
