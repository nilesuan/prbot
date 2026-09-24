"""Tests for the verification pass.

A single call's confidence is noise-level: two identical runs on
infrastructure-core MR 209 scored 87 and 96, and one finding came back high in
one and medium in the other. A verified review re-checks every claim against
the code before it scores anything. This pass does that with one further call
per chunk, and replaces each finding's confidence with the verifier's.
"""

from __future__ import annotations

from typing import Any

import pytest

from prbot.review.models import Finding
from prbot.review.verifier import (
    VERIFY_TOOL_NAME,
    apply_verdicts,
    build_verification_prompt,
    parse_verdicts,
)


def _finding(**overrides: Any) -> Finding:
    base: dict[str, Any] = {
        "id": "f", "category": "iac", "check_id": "IAC-REPLACE-01",
        "title": "Rule keys shift when the list is reordered",
        "description": "d", "file_path": "nacls.tf",
        "line_start": 10, "line_end": 12, "severity": "medium",
        "confidence": 40,
    }
    base.update(overrides)
    return Finding(**base)


class TestThePrompt:
    def test_each_finding_is_numbered_after_the_diff(self) -> None:
        out = build_verification_prompt(
            "DIFF", [_finding(), _finding(title="Second")],
        )
        assert out.index("DIFF") < out.index("[1]") < out.index("[2]")

    def test_the_findings_are_datamarked(self) -> None:
        """Model output about untrusted content is itself untrusted."""
        from prbot.security.datamarking import get_session_mark

        out = build_verification_prompt("DIFF", [_finding()])
        section = out[out.index("[1]"):]
        assert f"^{get_session_mark()}^" in section

    def test_the_location_and_check_are_given(self) -> None:
        out = build_verification_prompt("DIFF", [_finding()])
        assert "nacls.tf:10-12" in out
        assert "IAC-REPLACE-01" in out


class TestParsingVerdicts:
    @staticmethod
    def _response(verdicts: list[dict[str, Any]]) -> dict[str, Any]:
        return {"output": {"message": {"content": [{"toolUse": {
            "name": VERIFY_TOOL_NAME, "input": {"verdicts": verdicts},
        }}]}}}

    def test_well_formed_verdicts_are_read(self) -> None:
        out = parse_verdicts(self._response([
            {"index": 1, "verdict": "confirmed", "confidence": 90,
             "reason": "r"},
        ]), count=1)
        assert out == {1: ("confirmed", 90)}

    def test_an_out_of_range_index_is_ignored(self) -> None:
        out = parse_verdicts(self._response([
            {"index": 7, "verdict": "confirmed", "confidence": 90,
             "reason": "r"},
        ]), count=2)
        assert out == {}

    def test_an_unknown_verdict_is_ignored(self) -> None:
        out = parse_verdicts(self._response([
            {"index": 1, "verdict": "maybe", "confidence": 90, "reason": "r"},
        ]), count=1)
        assert out == {}

    def test_confidence_is_clamped(self) -> None:
        out = parse_verdicts(self._response([
            {"index": 1, "verdict": "confirmed", "confidence": 250,
             "reason": "r"},
        ]), count=1)
        assert out == {1: ("confirmed", 100)}

    def test_no_tool_call_yields_nothing(self) -> None:
        assert parse_verdicts({"output": {"message": {"content": []}}}, 1) == {}


class TestApplyingVerdicts:
    def test_a_confirmed_finding_takes_the_verifiers_confidence(self) -> None:
        [out], stats = apply_verdicts([_finding()], {1: ("confirmed", 92)})
        assert out.confidence == 92
        assert out.verification == "confirmed"
        assert stats.confirmed == 1

    def test_a_refuted_finding_is_kept_at_the_verifiers_confidence(self) -> None:
        """Nothing is deleted: the scorer decides what a low number means."""
        [out], stats = apply_verdicts([_finding()], {1: ("refuted", 5)})
        assert out.confidence == 5
        assert out.verification == "refuted"
        assert stats.refuted == 1

    def test_a_finding_without_a_verdict_is_unchanged(self) -> None:
        """A verifier that skips one must not silently rescore it."""
        [out], stats = apply_verdicts([_finding(confidence=40)], {})
        assert out.confidence == 40
        assert out.verification == ""
        assert stats.unverified == 1

    def test_order_is_preserved(self) -> None:
        findings = [_finding(title="a"), _finding(title="b")]
        out, _ = apply_verdicts(findings, {2: ("confirmed", 80)})
        assert [f.title for f in out] == ["a", "b"]
        assert [f.confidence for f in out] == [40, 80]


class TestGroupingByChunk:
    @pytest.mark.asyncio
    async def test_each_chunk_is_verified_once_with_its_own_findings(
        self,
    ) -> None:
        from prbot.review.verifier import verify_findings
        from prbot.vcs.models import FileDiff, PRDiff

        chunks = [
            PRDiff(files=[FileDiff(path="a.tf", status="modified", patch="")]),
            PRDiff(files=[FileDiff(path="b.tf", status="modified", patch="")]),
        ]
        seen: list[tuple[str, int]] = []

        async def ask(chunk: PRDiff, findings: list[Finding]):
            seen.append((chunk.files[0].path, len(findings)))
            return {i + 1: ("confirmed", 99) for i in range(len(findings))}

        findings = [
            _finding(file_path="a.tf"), _finding(file_path="b.tf"),
            _finding(file_path="a.tf", title="other"),
        ]
        out, stats = await verify_findings(findings, chunks, ask)
        assert sorted(seen) == [("a.tf", 2), ("b.tf", 1)]
        assert [f.confidence for f in out] == [99, 99, 99]
        assert [f.file_path for f in out] == ["a.tf", "b.tf", "a.tf"]
        assert stats.confirmed == 3

    @pytest.mark.asyncio
    async def test_a_chunk_without_findings_is_not_verified(self) -> None:
        from prbot.review.verifier import verify_findings
        from prbot.vcs.models import FileDiff, PRDiff

        chunks = [
            PRDiff(files=[FileDiff(path="a.tf", status="modified", patch="")]),
        ]
        calls = 0

        async def ask(chunk: PRDiff, findings: list[Finding]):
            nonlocal calls
            calls += 1
            return {}

        await verify_findings([], chunks, ask)
        assert calls == 0

    @pytest.mark.asyncio
    async def test_a_failed_verification_leaves_the_findings_as_they_were(
        self,
    ) -> None:
        from prbot.exceptions import BedrockError
        from prbot.review.verifier import verify_findings
        from prbot.vcs.models import FileDiff, PRDiff

        chunks = [
            PRDiff(files=[FileDiff(path="a.tf", status="modified", patch="")]),
        ]

        async def ask(chunk: PRDiff, findings: list[Finding]):
            raise BedrockError("throttled")

        out, stats = await verify_findings(
            [_finding(file_path="a.tf")], chunks, ask,
        )
        assert out[0].confidence == 40
        assert stats.unverified == 1
        assert stats.failed_chunks == 1
