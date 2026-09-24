"""Verification pass: re-check each finding against the code before scoring.

A single call's confidence does not separate true findings from false ones.
Two identical runs on infrastructure-core MR 209 scored 87 and 96, and one
finding came back high in one and medium in the other. The self-reported
number sits at 30-60 for findings a verified review confirmed at 85-97.

This pass shows the verifier the same datamarked diff an agent saw and the
findings reported against it, and asks for a verdict on each: confirmed,
refuted or uncertain, with a confidence and a reason. The verdict replaces the
agent's confidence. Nothing is deleted: a refuted finding keeps its place at
the verifier's confidence, so the scorer decides what a low number means, and
a critical or high one stays visible however low it goes. A finding the
verifier gives no verdict on, or one whose chunk could not be verified, is
left exactly as the agent reported it.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from prbot.review.models import Finding
from prbot.vcs.models import PRDiff

logger = logging.getLogger(__name__)

VERIFY_TOOL_NAME = "report_verdicts"

_VERDICTS = ("confirmed", "refuted", "uncertain")

VERDICT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["verdicts"],
    "additionalProperties": False,
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["index", "verdict", "confidence", "reason"],
                "additionalProperties": False,
                "properties": {
                    "index": {"type": "integer", "minimum": 1},
                    "verdict": {"type": "string", "enum": list(_VERDICTS)},
                    "confidence": {
                        "type": "integer", "minimum": 0, "maximum": 100,
                    },
                    "reason": {"type": "string"},
                },
            },
        },
    },
}

VERIFIER_SYSTEM_SPEC = """\
You verify code review findings. Each finding below was reported by another
reviewer against the diff in this message. For every finding, decide from the
diff and the surrounding code shown whether the claim is true: that the code
does what the finding says, and that the stated consequence follows.

- confirmed: the code shows the claim is true.
- refuted: the code shows the claim is false, or the cited lines do not
  contain what the finding describes.
- uncertain: the deciding fact is not in what you were shown.

confidence is your probability that the finding is real, 0-100, after
checking. It replaces the reviewer's own number, so do not copy it: a claim
you confirmed by reading the lines should be high, one you could not check
should sit near the middle, and one you refuted should be low. Give a short
reason naming the line that decided it. Return a verdict for every finding
index, through the report_verdicts tool.
"""


@dataclass(frozen=True)
class VerificationStats:
    """What the pass concluded, for the comment and the audit record."""

    confirmed: int = 0
    refuted: int = 0
    uncertain: int = 0
    unverified: int = 0
    failed_chunks: int = 0


def verify_tool_config() -> dict[str, Any]:
    """Force the verifier to answer through the verdict schema."""
    return {
        "tools": [{
            "toolSpec": {
                "name": VERIFY_TOOL_NAME,
                "description": "Report a verdict for every finding index.",
                "inputSchema": {"json": VERDICT_JSON_SCHEMA},
            },
        }],
        "toolChoice": {"tool": {"name": VERIFY_TOOL_NAME}},
    }


def build_verifier_system_prompt() -> str:
    """The verifier's system prompt: its task and the datamarking rule."""
    from prbot.security.datamarking import build_datamarking_instruction

    return (
        f"{VERIFIER_SYSTEM_SPEC}\n"
        f"{build_datamarking_instruction()}\n"
    )


def build_verification_prompt(user_prompt: str, findings: list[Finding]) -> str:
    """The chunk's own prompt followed by the numbered findings to check.

    The findings are model output about untrusted content, so they are
    datamarked like the diff: a finding can quote the diff, and the diff can
    carry instructions.
    """
    from prbot.security.datamarking import apply_datamarking

    blocks = []
    for i, f in enumerate(findings, start=1):
        blocks.append(
            f"[{i}] {f.check_id} · {f.severity} · "
            f"{f.file_path}:{f.line_start}-{f.line_end}\n"
            f"Title: {apply_datamarking(f.title)}\n"
            f"Claim: {apply_datamarking(f.description)}\n"
            f"Consequence: {apply_datamarking(f.failure_scenario or '-')}"
        )
    return (
        f"{user_prompt}\n\n## Findings to verify ({len(findings)})\n\n"
        + "\n\n".join(blocks)
    )


def parse_verdicts(
    response: dict[str, Any], count: int,
) -> dict[int, tuple[str, int]]:
    """Index to (verdict, confidence), keeping only well-formed entries."""
    content = (
        ((response.get("output") or {}).get("message") or {}).get("content")
        or []
    )
    data: dict[str, Any] | None = None
    for block in content:
        use = block.get("toolUse") if isinstance(block, dict) else None
        if isinstance(use, dict) and isinstance(use.get("input"), dict):
            data = use["input"]
            break
    if data is None:
        return {}

    out: dict[int, tuple[str, int]] = {}
    for item in data.get("verdicts") or []:
        if not isinstance(item, dict):
            continue
        index, verdict = item.get("index"), item.get("verdict")
        confidence = item.get("confidence")
        if not isinstance(index, int) or not 1 <= index <= count:
            continue
        if verdict not in _VERDICTS or not isinstance(confidence, int):
            continue
        out[index] = (verdict, max(0, min(100, confidence)))
    return out


def apply_verdicts(
    findings: list[Finding], verdicts: dict[int, tuple[str, int]],
) -> tuple[list[Finding], VerificationStats]:
    """Replace each verified finding's confidence with the verifier's."""
    out: list[Finding] = []
    counts = {v: 0 for v in _VERDICTS}
    unverified = 0
    for i, f in enumerate(findings, start=1):
        if i not in verdicts:
            unverified += 1
            out.append(f)
            continue
        verdict, confidence = verdicts[i]
        counts[verdict] += 1
        out.append(dataclasses.replace(
            f, confidence=confidence, verification=verdict,
        ))
    return out, VerificationStats(
        confirmed=counts["confirmed"],
        refuted=counts["refuted"],
        uncertain=counts["uncertain"],
        unverified=unverified,
    )


async def verify_findings(
    findings: list[Finding],
    chunks: list[PRDiff],
    ask: Callable[[PRDiff, list[Finding]], Awaitable[dict[int, tuple[str, int]]]],
) -> tuple[list[Finding], VerificationStats]:
    """Verify each chunk's findings with one call, keeping the input order.

    A finding is verified against the chunk that holds its file, because
    that is the diff it was reported against.
    """
    by_chunk: dict[int, list[int]] = {}
    for i, f in enumerate(findings):
        for c, chunk in enumerate(chunks):
            if any(fd.path == f.file_path for fd in chunk.files):
                by_chunk.setdefault(c, []).append(i)
                break

    result = list(findings)
    total = VerificationStats()
    failed = 0
    placed = set()
    for c, indices in by_chunk.items():
        placed.update(indices)
        group = [findings[i] for i in indices]
        try:
            verdicts = await ask(chunks[c], group)
        except Exception as e:  # a failed check must not fail the review
            logger.warning("verify.failed chunk=%d: %s", c, e)
            failed += 1
            verdicts = {}
        updated, stats = apply_verdicts(group, verdicts)
        for i, f in zip(indices, updated, strict=True):
            result[i] = f
        total = VerificationStats(
            confirmed=total.confirmed + stats.confirmed,
            refuted=total.refuted + stats.refuted,
            uncertain=total.uncertain + stats.uncertain,
            unverified=total.unverified + stats.unverified,
        )

    unplaced = len(findings) - len(placed)
    return result, dataclasses.replace(
        total,
        unverified=total.unverified + unplaced,
        failed_chunks=failed,
    )
