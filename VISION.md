# prbot — Vision and where the implementation diverged

## Purpose

A containerised PR/MR review bot that runs as a GitHub Actions or GitLab CI
job. It fetches the diff and metadata, runs review agents against AWS Bedrock,
aggregates findings with confidence scoring, and posts a structured review
comment.

## What the original vision proposed, and what was built

This file previously described a design that was never implemented, which made
it actively misleading. The record of what changed is worth more than a
restatement of the architecture, which `README.md` and `CLAUDE.md` already
cover accurately.

| Original vision | What exists | Why |
|---|---|---|
| Five agents: arch, quality, security, testing, api+iac | Two agents: general and security | Five calls over the same diff cost five times as much and produced overlapping findings. One general agent covering Q-ARCH through Q-COMP, plus a dedicated security agent, was the smallest split that kept security prompts free of quality noise. |
| `vcs/base.py` abstract base class | `vcs/protocol.py` Protocol | Structural subtyping means the test fake satisfies the interface without inheriting from it, so the fake cannot drift from the real adapters silently. |
| `review/agents.py` holding agent definitions | Agent list built in `cli.py` | Two entries did not justify a module. This is the next thing to change: a configuration-driven roster is what lets a repository add its own agent. |
| `gh` and `glab` CLI tools in the container | httpx calls to the REST APIs | The CLIs would have been two more binaries to pin, scan and trust, for an HTTP call each. |
| Structured JSON output "with a defined schema" | A forced Bedrock tool carrying `FINDING_JSON_SCHEMA` | The schema existed from the start and was not sent until the output was actually constrained by it. Until then the shape was a request in the prompt and the parser guessed. |
| Opus for the security agent | Sonnet for both by default | Configurable per agent. Opus roughly quintuples the cost of the security half; whether it finds enough more to justify that is a question for measurement, not a default. |

## Design decisions that held

1. **Prompts as data.** Check specs are markdown bundled as package data in
   `src/prbot/prompts/`. Changing a check is a markdown edit and an image
   rebuild, not a code change. `PRBOT_PROMPTS_DIR` overrides the directory.
2. **Scoring in code, not in the model.** The model reports a severity and a
   confidence per finding. Weighting, banding, deduplication and the verdict
   are deterministic Python, so the same findings always produce the same
   verdict.
3. **No tool use for the review itself.** All context is fetched before the
   call. The one tool in the conversation is the findings schema, which
   constrains the output rather than granting the model reach.
4. **Untrusted content stays in the user message.** The system prompt carries
   the role and the checks; the diff and metadata never enter it.

## Known open questions

- Whether datamarking the patch content, as opposed to the metadata, helps or
  hurts finding precision. `PRBOT_DATAMARK_DIFF` and
  `scripts/measure_datamarking.py` exist to answer it with data.
- Whether giving the model the surrounding code, rather than the hunks alone,
  raises precision enough to justify the tokens. `PRBOT_CONTEXT_LINES` exists
  to answer it.
- Whether the adversarial agent, asked to construct a concrete failure rather
  than match a category, finds enough that the other two miss to justify a
  third of the spend. It ships disabled for exactly that reason.
- How well fingerprint matching survives a finding being reworded
  substantially between runs. A large rewording orphans the thread and the
  finding is posted again as new. The normalisation handles punctuation and
  case; it cannot handle a model that describes the same defect in genuinely
  different words.
