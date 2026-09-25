# Live measurements for the follow-up fixes

**Date:** 2026-09-24
**Method:** prbot run locally in dry-run mode (`PRBOT_DRY_RUN=1`) against real
merge requests, with Bedrock calls going to the development account through
the same model the CI jobs use (`au.anthropic.claude-sonnet-5`) and the same
three-agent roster. Nothing was posted. 29 billed runs, $17.62 in total, plus
two sub-cent probes of prompt caching and tool use.
**Reference:** the local verified review's findings on the same head commits,
from [`README.md`](README.md).

Three merge requests were still open and so still reviewable:
terraform-modules 269, infrastructure-core 208 and infrastructure-core 209.
The others had been merged and prbot skips a merged request.

## What each change did when measured

| Change | Result | Shipped as |
|---|---|---|
| Per-hunk context windows (#42) | MR 267's user prompt fell to 66% of its 0.6.0 size, MR 262's to 35% | fix |
| Rendered-size chunking (#42) | Moderate diffs now split; exposed the next defect | fix |
| Chunk awareness (#42) | Without it, MR 269's variables.tf chunk reported the NACL resources in the other chunk as missing, at critical and high. With it, both false findings were gone | fix |
| Low-confidence findings listed and audited (#43) | MR 208's 7 findings, all previously a bare count, are now each visible with check, location and confidence | fix |
| Temperature sent only when configured (#43) | The rejected first call per agent is gone | fix |
| Token estimate calibrated (#43) | Fitted on 129 billed calls; see the fixture | fix |
| `read_file` (#44) | MR 269: agents read 129-286 lines each and found nothing a run without reads missed, at $1.02 against $0.63 | **off by default** |
| Verification pass (#45) | MR 269: 100/100 with nothing reported became 86/100 with three reported; the finding behind the verified review's high went from 45% to 75%. MR 208: its one finding confirmed. +19-38% cost | **off by default** until a labelled evaluation exists |
| An S-INFRA family in the security spec | Fewer findings, not more (below) | **not shipped** |

## The security agent

The security agent returned an empty findings array on 31 of 47 production
calls. Its empty rate does not depend on prompt size (68% under 250k tokens,
64% above), unlike the iac agent's (4% and 44%), so it is not the oversized
prompts. The obvious explanation was the checklist: it asks only
application-code questions (injection, sessions, TLS, SSRF).

An S-INFRA family of six provider-neutral checks was written and measured
with the security agent alone, three runs of each spec on two merge requests:

| MR | Current spec | With S-INFRA |
|---|---|---|
| terraform-modules 269 | 3, 1, 2 findings | 1, 1, 0 |
| infrastructure-core 208 | 0, 1, 1 | 0, 0, 1 |

The current spec already reached infrastructure defects on MR 269 by filing
them as S-AUTH-02. The new family did not raise the count, and the
hypothesis is rejected on this evidence. What the security agent needs is
still open: its empty calls spend 756-1,316 output tokens before answering
with nothing, which says it considered the diff and found nothing that fit.

## Known limitations still open

- **Run-to-run variance is large.** Two identical runs on MR 209 scored 87
  and 96; three identical security-only runs on MR 269 produced 3, 1 and 2
  findings. Any single before-and-after comparison here is one sample.
  Verification is the only change aimed at this, and it has not been
  measured for variance.
- **Cross-agent duplicates survive.** On MR 208 three agents reported the
  hardcoded office CIDRs at the same lines under Q-ARCH-04, S-CRED-01 and
  IAC-SCOPE-03. De-duplication merges different checks only when the titles
  match after normalisation, and these share 27-40% of their subject words,
  under the existing 50% agreement bar. Lowering the bar to catch this case
  would be fitting a constant to one example, so it is left.
- **The budget check is conservative on output.** It assumes every call uses
  its whole `max_output_tokens` (8192); measured calls used 8 to 2,636. That
  is a true upper bound, and it is why the extras are dropped on MR 209 at
  the default $5.
- **The facts outside the diff remain out of reach by default.** The two
  largest verified findings on MR 208 were a module pinned to a tag that did
  not yet exist in another repository, and a test file no CI shard runs.
  `read_file` can reach the second but not the first, and agents given reads
  did not reach either.
