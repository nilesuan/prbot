"""Measure the cost and shape of each datamarking strategy (B2).

The token cost is deterministic and is measured here. The half that matters
more, whether a strategy changes finding precision, needs real Bedrock calls
and is not something this script can answer. Run prbot twice over the same
set of pull requests with PRBOT_DATAMARK_DIFF set differently, then compare
the audit records.

Usage:
    uv run python scripts/measure_datamarking.py <git-rev-range>
    uv run python scripts/measure_datamarking.py HEAD~20..HEAD
"""

from __future__ import annotations

import subprocess
import sys

from prbot.review.prompts import estimate_prompt_tokens
from prbot.security.datamarking import (
    apply_datamarking,
    apply_diff_datamarking,
)


def _collect(rev_range: str) -> list[tuple[str, str]]:
    """One (label, patch) pair per commit in the range."""
    shas = subprocess.run(
        ["git", "rev-list", rev_range],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    out: list[tuple[str, str]] = []
    for sha in shas:
        patch = subprocess.run(
            ["git", "show", "--format=", "-U3", sha],
            capture_output=True, text=True, check=True,
        ).stdout
        if patch.strip():
            out.append((sha[:8], patch))
    return out


def main(argv: list[str]) -> int:
    rev_range = argv[1] if len(argv) > 1 else "HEAD~10..HEAD"
    samples = _collect(rev_range)
    if not samples:
        print(f"no diffs in {rev_range}")
        return 1

    strategies = {
        "none": lambda p: p,
        "structure-preserving": apply_diff_datamarking,
        "mark-everything": apply_datamarking,
    }

    totals = dict.fromkeys(strategies, 0)
    print(f"{len(samples)} diffs from {rev_range}\n")
    print(f"{'strategy':<24}{'est. tokens':>14}{'vs none':>10}")
    print("-" * 48)

    for name, fn in strategies.items():
        totals[name] = sum(
            estimate_prompt_tokens(fn(patch)) for _, patch in samples
        )

    baseline = totals["none"] or 1
    for name in strategies:
        ratio = totals[name] / baseline
        print(f"{name:<24}{totals[name]:>14,}{ratio:>9.2f}x")

    print()
    print("Input cost per review at $3.00/M input tokens:")
    for name in strategies:
        per_review = totals[name] / len(samples)
        # Two agents each receive the prompt
        print(
            f"  {name:<24}${(per_review / 1_000_000) * 3.00 * 2:>8.4f}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
