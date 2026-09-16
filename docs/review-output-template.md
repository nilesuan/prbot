# prbot Review Output Template

This is the contract for everything prbot writes onto a pull or merge request.
It governs three things at once: the JSON each review agent must return, the
inline comment posted on the offending line, and the single summary comment.

A change to prbot's output is a change to this file first. The prompts in
`src/prbot/prompts/` and the renderer in `src/prbot/review/formatter.py` are
implementations of this document, not independent sources of truth.

## 1. Principles

1. **One issue, one comment, on the line it is about.** The reader should find
   the problem where the problem is, not in a wall of text at the bottom of the
   page.
2. **Say what is wrong. Nothing else.** No praise, no summary of what the change
   does, no restating the code back to the author, no questions, no
   encouragement. If a sentence does not identify a defect, its consequence, or
   its remedy, it does not belong in the output.
3. **Every issue has the same shape.** Same labels, same order, every time,
   regardless of which agent found it. A reader who has seen one prbot comment
   has seen them all, and a script that parses one can parse them all.
4. **Nothing is written twice.** Detail lives in exactly one place. If a finding
   is anchored to a line, its detail is in that inline comment and the summary
   only indexes it.
5. **Degrade by saying so.** When prbot cannot anchor a finding, cannot fit a
   comment, or an agent failed, the output states that plainly rather than
   quietly dropping it.

## 2. Delivery shape

prbot posts, per review:

| Artifact | Count | Contents |
|----------|-------|----------|
| Inline comment | one per anchorable reported finding | The full issue block (section 4) |
| Platform review | one per run, review mode only, GitHub only | Verdict, counts, and a pointer (section 5.2) |
| Summary comment | exactly one, rewritten in place on re-runs | Verdict, counts, index, unanchored issues, agent status (section 5) |

`review_mode` (`PRBOT_REVIEW_MODE`) selects the delivery. `review` is the
default: findings land on their lines and the verdict reaches the pull request.
`comment` posts the summary alone, for a token that cannot submit reviews or a
team that does not want line comments; see section 6.4 for what changes.

A finding is **anchorable** when its `line_end` falls on a line the diff
actually covers, as computed by `_anchor_line` in
`src/prbot/review/formatter.py`. Everything else is **unanchored** and is
rendered in the summary instead. A finding is never silently dropped for being
unanchored.

An anchor carries both sides of its line: `line` is the new-side number and
`old_line` is the same line in the base revision, present only when the line
exists in both. GitHub identifies a line by `line` plus `side: RIGHT` and
needs no more. GitLab builds a line code from the position and refuses one
that names only the new side of a line that was not added, which is most
anchors, so omitting `old_line` there loses the comment.

## 3. The finding record

Every agent returns findings against `FINDING_JSON_SCHEMA` in
`src/prbot/review/models.py`. The fields below are the whole contract; the
rendered output is a direct projection of them.

| Field | Required | Rendered as | Rule |
|-------|----------|-------------|------|
| `check_id` | yes | Comment header, index column | Must be a check ID defined in that agent's spec, e.g. `S-CRED-01`. Never invented. |
| `title` | yes | Index column `Issue` | A noun phrase naming the defect, under 80 characters. "Hardcoded AWS secret key", not "Consider using Secrets Manager". States the fault, not the fix, not a question. Appears in the summary index only; the inline comment is identified by its check ID. |
| `description` | yes | `**Problem:**` | 1-3 sentences. What is wrong, and why that is wrong. Present tense, no hedging. |
| `failure_scenario` | yes | `**Impact:**` | 1-2 sentences. The concrete trigger, then the wrong outcome, in that order. Name the input, config or sequence. If this sentence cannot be written, the finding is not reportable. |
| `suggestion` | optional | `**Fix:**` | 1-2 sentences. The smallest change that removes the defect. Omitted entirely when there is no concrete fix - never filled with "review this" or "consider refactoring". |
| `file_path` | yes | Index column `Location` | Path exactly as it appears in the diff. |
| `line_start`, `line_end` | yes | Anchor and `Location` | New-side line numbers. `line_end` is the anchor line. |
| `severity` | yes | Badge and index column | One of `critical`, `high`, `medium`, `low`, `info`. Calibration is per-agent spec. |
| `confidence` | yes | Header and index column | Integer 0-100. How sure the agent is that the defect is real and reachable - not how bad it is. Severity carries badness. |
| `reported_by` | derived | Agreement note | Set by deduplication. More than one entry renders `· reported by N agents`. |

### 3.1 `failure_scenario` is asked of every agent

It was previously required only of the adversarial agent and left empty by the
general and security agents. Under this template it is in the schema's
`required` list, so every agent must supply it: `**Impact:**` is part of the
fixed shape, and a finding whose consequence cannot be stated is one the reader
cannot weigh.

The renderer still tolerates an empty one by dropping the line, rather than
printing a label with nothing after it. That is a backstop against a model
ignoring the instruction, not a licence to omit it - prbot's recall must not
depend on treating a compliance failure as fatal.

## 4. Inline issue comment template

This is the exact body posted on the line. No other block, heading or sentence
may appear in it.

```markdown
**`{check_id}`** · {severity} · {confidence}% confidence{agreement}

**Problem:** {description}

**Impact:** {failure_scenario}

**Fix:** {suggestion}

<!-- prbot:finding:{fingerprint} -->
```

Rules:

- **Order is fixed.** Header, Problem, Impact, Fix, marker. Always.
- **Labels are fixed.** Exactly `**Problem:**`, `**Impact:**`, `**Fix:**`. No
  synonyms, no severity-dependent wording.
- **Problem is always present. Impact and Fix are dropped when empty**, never
  rendered as a bare label. An empty Impact is a prompt-compliance problem
  (section 3.1); an empty Fix is ordinary, because not every defect has a
  one-line remedy.
- `{agreement}` is ` · reported by {n} agents` when `len(reported_by) > 1`, and
  empty otherwise. Two agents independently finding the same defect is
  evidence, and the reader should see it.
- `{fingerprint}` is `finding_fingerprint(finding)` from
  `src/prbot/review/identity.py` - sha256 of file, check and normalised title,
  first 16 hex characters. It is invisible in rendered markdown and is what
  lets the next run recognise this thread instead of posting a duplicate.
- Every model-supplied value passes through `_sanitise()` before rendering:
  `&`, `<`, `>`, `[` and `]` are escaped, `@handles` are backticked so no one
  is notified, and URLs are broken with a zero-width space so nothing the model
  emitted becomes a clickable link.
- The same renderer, `_format_issue_block()`, produces this body and the
  unanchored entries in the summary. There is one implementation of this
  template, not two.

### 4.1 Worked example

```markdown
**`S-CRED-01`** · critical · 92% confidence

**Problem:** The AWS secret access key is written as a string literal in the
client constructor instead of being read from the environment or the
credentials provider.

**Impact:** Anyone with read access to the repository, including every fork
and the full git history, obtains live production credentials.

**Fix:** Delete the literal and let boto3 resolve credentials from the default
provider chain, as `auth/credentials.py` already does.

<!-- prbot:finding:9f2c41ab77e30d18 -->
```

## 5. Summary comment template

One comment per review, found and rewritten in place via the
`<!-- prbot:state:... -->` marker. It indexes, it does not repeat.

```markdown
## ❌ REQUEST_CHANGES · Score: 62/100

1 critical · 1 high · 1 medium

### Issues

| Severity | Check | Location | Confidence | Issue |
|----------|-------|----------|------------|-------|
| 🔴 critical | `S-CRED-01` | `src/prbot/vcs/github.py:88-91` | 92% | Hardcoded AWS secret key |
| 🟠 high | `Q-ERR-02` | `src/prbot/review/runner.py:140-146` | 78% | Bedrock timeout swallowed without logging |
| 🟡 medium | `Q-COMP-03` | `CHANGELOG.md:1-1` | 71% | No changelog entry for the new flag |

Each issue above is commented on its line in the Files tab, except the 1 listed below.

### Not anchored to a line (1)

These lines fall outside the diff, so they have no inline thread.

**`Q-COMP-03`** · medium · 71% confidence

**Problem:** The change adds the `--review-mode` flag but `CHANGELOG.md` has no
entry for it.

**Impact:** An operator upgrading reads the changelog, sees no new flag, and
does not learn the behaviour changed.

**Fix:** Add the flag under the Unreleased heading.

<details>
<summary>Borderline findings (2)</summary>

- 🟡 medium · `Q-MAINT-02` · `src/prbot/cli.py:310-330` · 64% confidence · Retry loop duplicated from vcs/retry.py
- 🔵 low · `Q-TEST-02` · `tests/review/test_scorer.py:44-51` · 58% confidence · Boundary at the confidence threshold is untested

</details>

### Agent Status

- **general**: ✅ 4 findings, 12431 tokens, 8204ms
- **security**: ✅ 2 findings, 9880 tokens, 7110ms

---
_3 low-confidence findings hidden._
_1 finding(s) suppressed by configuration._
_2 finding(s) resolved since the last review._

_This review was generated by an AI model and may contain errors. Findings should be verified by a human reviewer._

<!-- prbot:state:... -->
```

Rules:

- **Header.** The badge is ✅ for `APPROVE`, 💬 for `COMMENT`, ❌ for
  `REQUEST_CHANGES`, followed by ` _(critical override)_` when
  `score.critical_override` is set.
- **Count line.** Severities with a count of zero are omitted. The whole line is
  omitted when there are no reported findings.
- **Issues table.** One row per reported finding, anchored or not, sorted by
  severity then by descending confidence. `Location` is
  `` `{file_path}:{line_start}-{line_end}` ``. The `Issue` column is the
  `title` only - no description, because the description is in the inline
  comment.
- **No per-finding detail sections under the table.** The summary indexes.
- **Delivery note.** Present in review mode only. It names the number of rows
  that have no thread rather than claiming every issue has one.
- **Not anchored.** Full issue blocks, using the section 4 template without the
  fingerprint marker. This is the only copy of that detail, so it is not a
  repetition. The section is omitted when every finding was anchored.
- **Borderline.** Findings scored into the borderline band. One line each, never
  a full block, always inside `<details>`. Omitted when empty.
- **Agent status.** Aggregated per agent name, not per outcome, so a chunked
  review shows one line per agent with summed figures and a pass count.
- **Footer.** Hidden, suppressed and resolved counts each appear only when
  non-zero. The disclaimer is mandatory and survives truncation. The state
  record is always last.

### 5.1 Why the summary is a comment and not the review body

In review mode the summary is still posted as an ordinary comment, not as the
body of the platform review. A review object cannot be found and rewritten by
a later run, so putting the summary in one would repost the whole thing on
every push, and would leave the state record somewhere `find_bot_comment` does
not look - which is what the unchanged-commit skip reads.

Because summary and inline comments are submitted in the same call, the comment
URLs do not exist when the table is rendered. The `Location` column is therefore
a path and line range rather than a link.

### 5.2 Platform review body

The review object carries the verdict and the inline comments. Its body says
only what the review itself has to say, and is never empty, because GitHub
rejects a `REQUEST_CHANGES` review with a blank body.

GitLab has no review object: the comments are discussions and the verdict is
approve or unapprove, so there is nowhere for this body to go and the adapter
ignores it. It must not be posted as a note - the summary comment is already
that note, and a second one carries no state record, so nothing could find it
again and a copy accumulated on every run.

```markdown
## ❌ REQUEST_CHANGES · Score: 62/100

1 critical · 1 high · 1 medium

2 issue(s) commented on their lines. The full index is in prbot's summary comment.
```

With no reported findings the body is the header followed by
`No issues found.`

An event the platform refuses is not fatal. GitHub does not permit the Actions
token to approve a pull request, and nobody may approve their own, so the
adapter retries: first without the inline positions, then as a plain `COMMENT`,
keeping as much as each attempt allows. The verdict still reaches the exit code,
so losing the event is acceptable and losing the review is not.

## 6. Required cases

### 6.1 No findings

```markdown
## ✅ APPROVE · Score: 100/100

No issues found.

### Agent Status

- **general**: ✅ 0 findings, 9120 tokens, 6401ms
- **security**: ✅ 0 findings, 8004 tokens, 5233ms

---

_This review was generated by an AI model and may contain errors. Findings should be verified by a human reviewer._

<!-- prbot:state:... -->
```

`No issues found.` is the whole body. It is not an invitation to praise the
change.

### 6.2 Every agent failed

No verdict and no score, because neither was computed. One line per failed
agent naming the error type and message, then the disclaimer and state record.
This is the `_format_review_incomplete` path.

### 6.3 Truncation

A comment that exceeds the platform limit (65536 for GitHub, 1000000 for
GitLab) is **rebuilt with less in it**, not cut apart as rendered markdown, so
every attempt is a well-formed comment. Each attempt stops as soon as it fits:

1. Everything.
2. Without the borderline block, replaced by
   `_Borderline findings truncated for size._`
3. Also without the `info` and `low` issue blocks, replaced by
   `_N low-severity issue detail(s) truncated for size._`
4. Last resort: a hard cut that preserves the disclaimer and the state record,
   marked `_...truncated for size..._`. Without the state record the next run
   cannot find its own comment.

Inline comments are never truncated as a group. An individual body is bounded
by the field caps in section 7 instead.

### 6.4 `comment` mode

With `review_mode = "comment"` nothing is anchored, so every reported finding
renders as a full issue block under the `### Issues` table, in severity then
confidence order. There is no delivery note and no "Not anchored" heading,
because nothing was ever going to be anchored. The per-issue shape is
identical.

## 7. Hard limits

Applied by the renderer regardless of what the model returns, so that one
finding cannot consume the platform comment limit on its own:

| Field | Cap | Constant |
|-------|-----|----------|
| `title` | 200 chars | `_MAX_TITLE` |
| `description` | 4000 chars | `_MAX_DESCRIPTION` |
| `failure_scenario` | 4000 chars | `_MAX_DESCRIPTION` |
| `suggestion` | 2000 chars | `_MAX_SUGGESTION` |
| `file_path` | 400 chars | `_MAX_PATH` |

These are a backstop. The prompts ask for 1-3 sentences; hitting a cap means
the agent ignored the instruction, and the output is cut rather than trusted.

## 8. Banned content

None of the following may appear anywhere prbot writes. This list is the
operational meaning of "only say what went wrong", and it is carried into every
agent's check spec under `## Reporting Rules`.

- Praise or reassurance of any kind, including "good catch", "nicely
  structured", "this is a solid change".
- A summary of what the diff does. The author wrote it.
- Restating the code in prose when the comment is already attached to that code.
- Questions to the author. A finding is a claim, not an enquiry. If it cannot be
  asserted, lower the confidence until it is filtered out.
- Hedging stacks: "you might possibly want to consider perhaps".
- Second person coaching, next steps, or anything addressed to the author rather
  than about the code.
- The agent's own reasoning process, retries, or token usage anywhere except the
  Agent Status block.
- Emoji other than the fixed verdict badges (✅ 💬 ❌), severity badges
  (🔴 🟠 🟡 🔵 ⚪) and agent status marks (✅ ⚠️ ❌).
- Headings, horizontal rules or nested markdown inside an inline comment body.
- Any link. URLs from the model are defused by the renderer; the template has no
  place for one.

## 9. Where this is enforced

| Concern | Location |
|---------|----------|
| Field contract and required fields | `FINDING_JSON_SCHEMA`, `src/prbot/review/models.py` |
| What agents are told to produce | `src/prbot/prompts/{general,security,adversarial}.md`, "Output Format" and "Reporting Rules" |
| One issue block, both call sites | `_format_issue_block()`, `src/prbot/review/formatter.py` |
| Inline comments and the anchor test | `build_inline_comments()`, `unanchored_findings()`, `_anchor_line()`, same file |
| Summary body and truncation order | `format_review_comment()` and its `_format_*` helpers, same file |
| Platform review body | `format_review_event_body()`, same file |
| Escaping and link defusing | `_sanitise()`, `_cell()`, same file |
| Thread identity | `src/prbot/review/identity.py` |
| Delivery, and the refused-event fallback | `submit_review()` in `src/prbot/vcs/{github,gitlab}.py` |
| New-side to old-side line mapping | `map_new_to_old()`, `src/prbot/vcs/diff_parser.py` |
| Tests for this document | `tests/review/test_output_template.py` |
