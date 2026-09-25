"""Tests for markdown formatter (story-5-5)."""

from __future__ import annotations

from prbot.review.formatter import (
    _format_agent_status,
    _format_borderline_section,
    _format_findings_table,
    _format_footer,
    _format_header,
    format_review_comment,
    truncate_comment,
)
from prbot.review.models import AgentError, AgentResult, Finding, TokenUsage
from prbot.review.scorer import ReviewScore, ScoredFinding
from prbot.review.verdict import ReviewVerdict


def _make_finding(
    *,
    severity: str = "medium",
    confidence: int = 75,
    check_id: str = "Q-ARCH-01",
    title: str = "Test finding",
) -> Finding:
    return Finding(
        id="test-1",
        category="general",
        check_id=check_id,
        title=title,
        description="Test description",
        file_path="src/test.py",
        line_start=1,
        line_end=10,
        severity=severity,
        confidence=confidence,
        suggestion="Fix it",
    )


def _make_scored(
    *,
    severity: str = "medium",
    confidence: int = 75,
) -> ScoredFinding:
    finding = _make_finding(severity=severity, confidence=confidence)
    return ScoredFinding.from_finding(finding, threshold=70)


def _make_score(
    clamped: int = 95,
    critical_override: bool = False,
) -> ReviewScore:
    return ReviewScore(
        raw_score=float(clamped),
        clamped_score=clamped,
        total_deductions=100.0 - clamped,
        finding_count=1,
        critical_override=critical_override,
    )


class TestFormatHeader:
    """Tests for _format_header."""

    def test_approve_header(self) -> None:
        header = _format_header(ReviewVerdict.APPROVE, _make_score(95))
        assert "APPROVE" in header
        assert "95/100" in header
        assert "✅" in header

    def test_request_changes_header(self) -> None:
        header = _format_header(
            ReviewVerdict.REQUEST_CHANGES, _make_score(40),
        )
        assert "REQUEST_CHANGES" in header
        assert "❌" in header

    def test_critical_override_note(self) -> None:
        header = _format_header(
            ReviewVerdict.REQUEST_CHANGES,
            _make_score(0, critical_override=True),
        )
        assert "critical override" in header


class TestFormatFindingsTable:
    """Tests for _format_findings_table."""

    def test_no_findings(self) -> None:
        assert _format_findings_table([]) == "No issues found."

    def test_table_contains_finding(self) -> None:
        scored = _make_scored(severity="high", confidence=85)
        result = _format_findings_table([scored])
        assert "Q-ARCH-01" in result
        assert "high" in result
        assert "85%" in result

    def test_sorted_by_severity(self) -> None:
        critical = _make_scored(severity="critical", confidence=80)
        low = _make_scored(severity="low", confidence=90)
        result = _format_findings_table([low, critical])
        crit_pos = result.index("critical")
        low_pos = result.index("low")
        assert crit_pos < low_pos

    def test_the_table_does_not_repeat_the_detail(self) -> None:
        result = _format_findings_table([_make_scored()])
        assert "Fix it" not in result
        assert "Test description" not in result


class TestFormatBorderlineSection:
    """Tests for _format_borderline_section."""

    def test_empty_borderline(self) -> None:
        assert _format_borderline_section([]) == ""

    def test_collapsed_section(self) -> None:
        finding = _make_finding(confidence=60)
        scored = ScoredFinding(finding=finding, band="borderline", deduction=0)
        result = _format_borderline_section([scored])
        assert "<details>" in result
        assert "</details>" in result
        assert "Borderline" in result


class TestFormatAgentStatus:
    """Tests for _format_agent_status."""

    def test_success_status(self) -> None:
        result = AgentResult(
            agent="general",
            findings=[],
            token_usage=TokenUsage(1000, 200, 0.0),
            latency_ms=500,
            model_id="test",
        )
        status = _format_agent_status([result])
        assert "general" in status
        assert "✅" in status

    def test_error_status(self) -> None:
        error = AgentError(
            agent="security",
            error_type="timeout",
            message="timed out",
            retryable=True,
        )
        status = _format_agent_status([error])
        assert "security" in status
        assert "❌" in status
        assert "retryable" in status


class TestFormatFooter:
    """Tests for _format_footer."""

    def test_disclaimer_present(self) -> None:
        footer = _format_footer(0, "")
        assert "AI model" in footer
        assert "may contain errors" in footer

    def test_hidden_count_shown(self) -> None:
        footer = _format_footer(5, "")
        assert "5 low-confidence findings listed, not scored" in footer

    def test_state_html_included(self) -> None:
        footer = _format_footer(0, "<!-- state -->")
        assert "<!-- state -->" in footer


class TestFormatReviewComment:
    """Tests for format_review_comment integration."""

    def test_complete_comment(self) -> None:
        score = _make_score(90)
        reported = [_make_scored()]
        comment = format_review_comment(
            ReviewVerdict.COMMENT,
            score,
            reported,
            [],
            0,
            [AgentResult(
                agent="general",
                findings=[],
                token_usage=TokenUsage(1000, 200, 0.0),
                latency_ms=500,
                model_id="test",
            )],
        )
        assert "COMMENT" in comment
        assert "90/100" in comment

    def test_both_agents_failed(self) -> None:
        """G-28: Both agents failed → Review Incomplete."""
        score = _make_score(100)
        errors = [
            AgentError(agent="general", error_type="timeout", message="t/o"),
            AgentError(agent="security", error_type="timeout", message="t/o"),
        ]
        comment = format_review_comment(
            ReviewVerdict.COMMENT, score, [], [], 0, errors,
        )
        assert "Review Incomplete" in comment
        assert "general" in comment
        assert "security" in comment


class TestTruncateComment:
    """Tests for progressive truncation."""

    def test_the_footer_survives_a_hard_cut(self) -> None:
        """Without the state record the next run cannot find its comment."""
        comment = (
            "## Header\n\n" + ("Content. " * 200) + "\n\n---\nFooter"
        )
        result = truncate_comment(comment, 300)
        assert "Header" in result
        assert result.endswith("\n---\nFooter")
        assert len(result) <= 300

    def test_preserves_short_comment(self) -> None:
        comment = "Short comment"
        assert truncate_comment(comment, 1000) == comment

    def test_a_comment_with_no_footer_is_still_bounded(self) -> None:
        """The footer rule is prbot's own, so it may not be there at all."""
        assert len(truncate_comment("x" * 500, 100)) == 100


class TestModelOutputIsSanitised:
    """B7: title, check_id, file_path and prose went into the comment raw.

    The four prompt-injection layers all stop at the model boundary. Nothing
    checked what the model wrote into a comment prbot then posts with
    pull-requests: write.
    """

    @staticmethod
    def _scored(**overrides: object) -> ScoredFinding:
        defaults: dict[str, object] = {
            "id": "general-1",
            "category": "general",
            "check_id": "Q-ERR-01",
            "title": "A finding",
            "description": "A description",
            "file_path": "src/app.py",
            "line_start": 1,
            "line_end": 2,
            "severity": "medium",
            "confidence": 85,
            "suggestion": "A suggestion",
        }
        defaults.update(overrides)
        return ScoredFinding(
            finding=Finding(**defaults),  # type: ignore[arg-type]
            band="reported",
            deduction=1.0,
        )

    @staticmethod
    def _unescaped_pipes(row: str) -> int:
        """Count cell separators: an escaped pipe renders as a literal."""
        import re as _re

        return len(_re.findall(r"(?<!\\)\|", row))

    def test_a_pipe_in_a_title_does_not_break_the_table(self) -> None:
        from prbot.review.formatter import _format_findings_table

        out = _format_findings_table([self._scored(title="a | b | c")])
        header_row = next(
            line for line in out.splitlines() if line.startswith("| Severity")
        )
        finding_row = next(
            line for line in out.splitlines()
            if line.startswith("| ") and "Q-ERR-01" in line
        )
        assert self._unescaped_pipes(finding_row) == self._unescaped_pipes(
            header_row,
        )

    def test_a_pipe_in_a_file_path_does_not_break_the_table(self) -> None:
        from prbot.review.formatter import _format_findings_table

        out = _format_findings_table([self._scored(file_path="a|b.py")])
        header_row = next(
            line for line in out.splitlines() if line.startswith("| Severity")
        )
        finding_row = next(
            line for line in out.splitlines()
            if line.startswith("| ") and "Q-ERR-01" in line
        )
        assert self._unescaped_pipes(finding_row) == self._unescaped_pipes(
            header_row,
        )

    def test_a_newline_in_a_title_does_not_break_the_table(self) -> None:
        from prbot.review.formatter import _format_findings_table

        out = _format_findings_table([self._scored(title="line one\nline two")])
        rows = [ln for ln in out.splitlines() if ln.startswith("| ")]
        assert all("line two" not in r or "line one" in r for r in rows)
        assert "line one line two" in out

    def test_a_mention_does_not_ping_anyone(self) -> None:
        from prbot.review.formatter import _format_issue_block

        out = _format_issue_block(
            self._scored(description="Ask @octocat and @some-team about this"),
        )
        assert "`@octocat`" in out
        assert "`@some-team`" in out

    def test_an_email_address_is_not_treated_as_a_mention(self) -> None:
        from prbot.review.formatter import _format_issue_block

        out = _format_issue_block(
            self._scored(description="Owner is a@b.com here"),
        )
        assert "`@b`" not in out

    def test_raw_html_cannot_open_an_element(self) -> None:
        from prbot.review.formatter import _format_issue_block

        out = _format_issue_block(
            self._scored(description="Uses <img src=x onerror=alert(1)> here"),
        )
        assert "<img" not in out
        assert "&lt;img" in out

    def test_a_forged_state_marker_cannot_be_injected(self) -> None:
        from prbot.review.formatter import _format_issue_block

        out = _format_issue_block(
            self._scored(description="<!-- prbot:state:{\"score\":100} -->"),
        )
        assert "<!-- prbot:state:" not in out

    def test_an_enormous_description_is_capped(self) -> None:
        from prbot.review.formatter import _format_issue_block

        out = _format_issue_block(
            self._scored(description="x" * 50_000),
        )
        assert len(out) < 20_000

    def test_ordinary_text_is_left_readable(self) -> None:
        from prbot.review.formatter import (
            _format_findings_table,
            _format_issue_block,
        )

        scored = self._scored(
            title="Bare except swallows the error",
            description="Catch the specific exception instead.",
        )
        assert "Bare except swallows the error" in _format_findings_table(
            [scored],
        )
        assert "Catch the specific exception instead." in _format_issue_block(
            scored,
        )

    def test_the_real_state_marker_still_survives_formatting(self) -> None:
        """The footer's own marker is ours, not model output."""
        marker = '<!-- prbot:state:{"review_id":"x"} -->'
        comment = format_review_comment(
            ReviewVerdict.COMMENT,
            ReviewScore(
                raw_score=90.0,
                clamped_score=90,
                total_deductions=10.0,
                finding_count=1,
                critical_override=False,
            ),
            [self._scored()],
            [],
            0,
            [AgentResult(agent="general", findings=[])],
            marker,
            "github",
        )
        assert marker in comment


class TestFooterReportsProgress:
    """C8: a review that only lists what is wrong reads as an immovable wall."""

    def test_resolved_findings_are_reported(self) -> None:
        from prbot.review.formatter import _format_footer

        footer = _format_footer(0, "", fixed_count=3)
        assert "3" in footer
        assert "resolved since the last review" in footer

    def test_nothing_is_said_when_nothing_was_resolved(self) -> None:
        from prbot.review.formatter import _format_footer

        assert "resolved since" not in _format_footer(0, "")


class TestAgentStatusAggregatesChunks:
    """GEN-ARCH-01: one bullet per outcome meant one per agent per chunk."""

    @staticmethod
    def _result(agent: str, findings: int, tokens: int, ms: int) -> AgentResult:
        from prbot.review.models import TokenUsage

        return AgentResult(
            agent=agent,
            findings=[],
            token_usage=TokenUsage(tokens, 0, 0.0),
            latency_ms=ms,
            model_id="m",
        )

    def test_one_line_per_agent_not_per_chunk(self) -> None:
        from prbot.review.formatter import _format_agent_status

        out = _format_agent_status([
            self._result("general", 0, 100, 10),
            self._result("security", 0, 200, 20),
            self._result("general", 0, 300, 30),
            self._result("security", 0, 400, 40),
        ])
        lines = [ln for ln in out.splitlines() if ln.startswith("- ")]
        assert len(lines) == 2

    def test_figures_are_summed_across_chunks(self) -> None:
        from prbot.review.formatter import _format_agent_status

        out = _format_agent_status([
            self._result("general", 0, 100, 10),
            self._result("general", 0, 300, 30),
        ])
        assert "400 tokens" in out
        assert "40ms" in out

    def test_the_pass_count_is_shown_when_chunked(self) -> None:
        from prbot.review.formatter import _format_agent_status

        out = _format_agent_status([
            self._result("general", 0, 100, 10),
            self._result("general", 0, 300, 30),
        ])
        assert "2 passes" in out

    def test_a_single_pass_says_nothing_about_passes(self) -> None:
        from prbot.review.formatter import _format_agent_status

        out = _format_agent_status([self._result("general", 0, 100, 10)])
        assert "passes" not in out

    def test_a_partial_failure_is_visible(self) -> None:
        from prbot.review.formatter import _format_agent_status

        out = _format_agent_status([
            self._result("general", 0, 100, 10),
            AgentError(agent="general", error_type="throttled", message="m"),
        ])
        assert "1 of 2" in out or "1/2" in out
        assert "throttled" in out


class TestCellEscapingCannotBeNeutralised:
    """SEC-DATA-02: escaping the pipe without the backslash is undone.

    _cell turned `|` into `\\|`, but text already containing `\\|` became
    `\\\\|`. A table row is split on pipes before inline parsing and a
    backslash consumes the character after it, so the first backslash ate the
    second and the pipe split the cell anyway.
    """

    @staticmethod
    def _cells(row: str) -> int:
        """Pipes that actually split a cell.

        A pipe is escaped only when preceded by an ODD number of
        backslashes: `\\|` is an escaped backslash followed by a live pipe.
        A naive "not preceded by a backslash" check cannot see that, which is
        the whole bug.
        """
        live = 0
        for i, ch in enumerate(row):
            if ch != "|":
                continue
            slashes = 0
            j = i - 1
            while j >= 0 and row[j] == "\\":
                slashes += 1
                j -= 1
            if slashes % 2 == 0:
                live += 1
        return live

    def test_a_backslash_pipe_cannot_split_a_cell(self) -> None:
        from prbot.review.formatter import _cell

        out = _cell(r"a \| b", 200)
        assert self._cells(out) == 0, f"cell still splits: {out!r}"

    def test_a_doubled_backslash_pipe_cannot_split_a_cell(self) -> None:
        from prbot.review.formatter import _cell

        assert self._cells(_cell(r"a \\| b", 200)) == 0

    def test_a_plain_pipe_is_still_escaped(self) -> None:
        from prbot.review.formatter import _cell

        assert self._cells(_cell("a | b", 200)) == 0

    def test_ordinary_text_is_unchanged(self) -> None:
        from prbot.review.formatter import _cell

        assert _cell("Bare except swallows the error", 200) == (
            "Bare except swallows the error"
        )


class TestSanitiseNeutralisesLinks:
    """SEC-DATA-01: model text could post a clickable link."""

    def test_a_markdown_link_is_defused(self) -> None:
        from prbot.review.formatter import _sanitise

        out = _sanitise("See [the docs](https://evil.example.com/x) now", 2000)
        assert "](https://evil.example.com" not in out

    def test_a_bare_url_is_not_clickable(self) -> None:
        from prbot.review.formatter import _sanitise

        out = _sanitise("Fetch https://evil.example.com/x for details", 2000)
        # Not an intact URL any more, so the autolinker does not see one.
        assert "https://evil.example.com/x" not in out

    def test_the_url_is_still_readable(self) -> None:
        from prbot.review.formatter import _sanitise

        out = _sanitise("See https://example.com/a for details", 2000)
        # The break is a zero-width space, so a reader sees the same text.
        assert out.replace("\u200b", "") == (
            "See https://example.com/a for details"
        )

    def test_ordinary_prose_is_unchanged(self) -> None:
        from prbot.review.formatter import _sanitise

        assert _sanitise("Catch the specific exception.", 2000) == (
            "Catch the specific exception."
        )


class TestLinksCannotBeReconstituted:
    """SEC-DATA-01: a code span is not containment.

    GFM autolinks a bare 'www.' host with no scheme, and a stray backtick in
    model text closes the span that was supposed to hold the URL.
    """

    def test_a_bare_www_host_is_defused(self) -> None:
        from prbot.review.formatter import _sanitise

        out = _sanitise("Send it to www.attacker.example/collect?d=1", 2000)
        assert "www.attacker.example/collect" not in out

    def test_a_scheme_url_is_defused(self) -> None:
        from prbot.review.formatter import _sanitise

        out = _sanitise("See https://attacker.example/x now", 2000)
        assert "https://attacker.example/x" not in out

    def test_a_stray_backtick_cannot_open_a_span(self) -> None:
        from prbot.review.formatter import _sanitise

        out = _sanitise("a ` b https://attacker.example/x c", 2000)
        assert "https://attacker.example/x" not in out

    def test_the_host_is_still_readable(self) -> None:
        from prbot.review.formatter import _sanitise

        out = _sanitise("See https://example.com/a for details", 2000)
        assert "example.com" in out

    def test_ordinary_prose_is_unchanged(self) -> None:
        from prbot.review.formatter import _sanitise

        assert _sanitise("Catch the specific exception.", 2000) == (
            "Catch the specific exception."
        )


class TestEveryFailureIsShown:
    """GEN-ERR-02: a totally failed agent showed only its first error."""

    def test_all_errors_are_listed_when_nothing_succeeded(self) -> None:
        from prbot.review.formatter import _format_agent_status

        out = _format_agent_status([
            AgentError(agent="general", error_type="throttled", message="one"),
            AgentError(agent="general", error_type="timeout", message="two"),
            AgentError(agent="general", error_type="internal", message="three"),
        ])
        for token in ("throttled", "timeout", "internal"):
            assert token in out

    def test_the_pass_count_is_visible_on_total_failure(self) -> None:
        from prbot.review.formatter import _format_agent_status

        out = _format_agent_status([
            AgentError(agent="general", error_type="throttled", message="one"),
            AgentError(agent="general", error_type="timeout", message="two"),
        ])
        assert "2" in out


class TestFindingsReconcile:
    """Every finding the agents produced must be accounted for (D5).

    tfmod!245 reported "security: 4 findings" in its Agent Status block,
    listed three under Borderline, and had no hidden line. One finding was
    gone with no accounting anywhere in the output. There are three silent
    sinks - de-duplication, the not-in-the-diff drop, and suppression rules -
    and none of them said anything in the comment.
    """

    @staticmethod
    def _outcome(n: int) -> AgentResult:
        return AgentResult(
            agent="security",
            findings=[_make_finding(confidence=55) for _ in range(n)],
            token_usage=TokenUsage(100, 10, 0.0),
            latency_ms=1,
            model_id="m",
        )

    def _comment(self, **kwargs: object) -> str:
        from prbot.review.formatter import format_review_comment

        defaults: dict[str, object] = {
            "verdict": ReviewVerdict.COMMENT,
            "score": ReviewScore(100.0, 100, 0.0, 0, False),
            "reported": [],
            "borderline": [],
            "hidden_count": 0,
            "outcomes": [self._outcome(0)],
        }
        defaults.update(kwargs)
        return format_review_comment(**defaults)  # type: ignore[arg-type]

    def test_the_comment_states_what_became_of_every_finding(self) -> None:
        body = self._comment(
            outcomes=[self._outcome(2)],
            hidden_count=1,
            produced_count=4,
            dropped_count=1,
            suppressed_count=0,
            score=ReviewScore(100.0, 100, 0.0, 1, False, merged_count=1),
        )
        assert "4 produced" in body
        assert "1 merged" in body
        assert "1 outside the diff" in body
        assert "1 low confidence, listed but not scored" in body

    def test_nothing_is_said_when_nothing_was_lost(self) -> None:
        """A clean run must not grow a line of accounting noise."""
        body = self._comment(produced_count=0)
        assert "produced" not in body

    def test_a_silent_sink_is_visible(self) -> None:
        """Agents produced 4, three are shown, one vanished."""
        body = self._comment(
            outcomes=[self._outcome(3)],
            borderline=[_make_scored(confidence=60) for _ in range(3)],
            produced_count=4,
            dropped_count=1,
        )
        assert "4 produced" in body
        assert "1 outside the diff" in body


def _clean_outcome() -> AgentResult:
    return AgentResult(
        agent="iac",
        findings=[],
        token_usage=TokenUsage(1, 1, 0.0),
        latency_ms=1,
        model_id="m",
    )


class TestHeaderDoesNotDenyShownFindings:
    """"No issues found." printed above a red critical is a lie.

    The header keyed off the reported band alone, so once borderline
    findings began to be shown and scored the summary could assert there was
    nothing wrong directly above a list saying otherwise.
    """

    def test_borderline_findings_are_not_no_issues(self) -> None:
        from prbot.review.formatter import format_review_comment

        body = format_review_comment(
            ReviewVerdict.COMMENT,
            _make_score(clamped=91),
            [],
            [_make_scored(severity="critical", confidence=38)],
            0,
            [_clean_outcome()],
        )
        assert "No issues found." not in body

    def test_a_genuinely_clean_review_still_says_so(self) -> None:
        from prbot.review.formatter import format_review_comment

        body = format_review_comment(
            ReviewVerdict.APPROVE,
            _make_score(clamped=100),
            [],
            [],
            0,
            [_clean_outcome()],
        )
        assert "No issues found." in body


class TestLowConfidenceFindingsAreListed:
    """Hidden findings are listed, collapsed, so they can be inspected."""

    @staticmethod
    def _hidden(title: str = "Guard can be bypassed") -> ScoredFinding:
        finding = _make_finding(
            severity="low", confidence=20, check_id="IAC-SCOPE-02",
            title=title,
        )
        return ScoredFinding.from_finding(finding, threshold=70)

    def _comment(self, hidden: list[ScoredFinding]) -> str:
        return format_review_comment(
            ReviewVerdict.APPROVE, _make_score(100), [], [], len(hidden),
            [_clean_outcome()], hidden=hidden,
        )

    def test_a_hidden_finding_is_listed_with_its_confidence(self) -> None:
        out = self._comment([self._hidden()])
        assert "Low-confidence findings (1)" in out
        assert "IAC-SCOPE-02" in out
        assert "20% confidence" in out
        assert "Guard can be bypassed" in out

    def test_the_list_is_collapsed(self) -> None:
        out = self._comment([self._hidden()])
        at = out.index("Low-confidence findings (1)")
        assert out.rfind("<details>", 0, at) != -1

    def test_the_list_says_it_does_not_affect_the_score(self) -> None:
        out = self._comment([self._hidden()])
        assert "not scored" in out

    def test_no_hidden_findings_means_no_section(self) -> None:
        assert "Low-confidence findings" not in self._comment([])

    def test_the_footer_no_longer_says_hidden(self) -> None:
        out = self._comment([self._hidden()])
        assert "hidden" not in out.lower()

    def test_the_list_is_dropped_before_anything_else_when_oversize(
        self,
    ) -> None:
        many = [self._hidden("x" * 150) for _ in range(600)]
        out = self._comment(many)
        assert len(out) <= 65536
        assert "Low-confidence findings" not in out or "truncated" in out


class TestNoIssuesIsNotClaimedOverAList:
    def test_only_low_confidence_findings_is_not_no_issues(self) -> None:
        low = ScoredFinding.from_finding(
            _make_finding(severity="low", confidence=20), threshold=70,
        )
        out = format_review_comment(
            ReviewVerdict.APPROVE, _make_score(100), [], [], 1,
            [_clean_outcome()], hidden=[low],
        )
        assert "No issues found." not in out
        assert "No issues above the reporting threshold." in out
        assert "1 low-confidence finding(s) listed below." in out
