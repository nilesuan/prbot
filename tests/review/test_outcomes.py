"""Tests for finding identity and outcome reconciliation (C8).

Each finding is one comment thread. A fix is a reply in that thread and the
thread being resolved. That makes the pull request itself the store: no new
infrastructure, and the record of what happened is where the people who did
it are looking.

Two things fall out of giving a finding a stable identity. Re-reviewing stops
duplicating inline comments, which C1 shipped without. And the outcome of
every finding becomes measurable, which is the prerequisite for calibrating
the confidence thresholds rather than choosing them.
"""

from __future__ import annotations

from typing import Any

from prbot.review.identity import (
    FINDING_MARKER,
    extract_fingerprint,
    finding_fingerprint,
    marker_for,
)
from prbot.review.models import Finding
from prbot.review.outcomes import reconcile
from prbot.review.scorer import ScoredFinding
from prbot.vcs.models import ReviewThread


def _finding(**overrides: Any) -> Finding:
    base: dict[str, Any] = {
        "id": "general-1",
        "category": "general",
        "check_id": "Q-ERR-01",
        "title": "Bare except swallows the error",
        "description": "d",
        "file_path": "src/app.py",
        "line_start": 10,
        "line_end": 12,
        "severity": "medium",
        "confidence": 85,
    }
    base.update(overrides)
    return Finding(**base)


def _scored(finding: Finding) -> ScoredFinding:
    return ScoredFinding(finding=finding, band="reported", deduction=1.0)


def _thread(fingerprint: str, **overrides: Any) -> ReviewThread:
    base: dict[str, Any] = {
        "id": "t1",
        "comment_id": 101,
        "body": f"some finding text\n{marker_for(fingerprint)}",
        "resolved": False,
        "path": "src/app.py",
        "line": 11,
    }
    base.update(overrides)
    return ReviewThread(**base)


class TestFingerprintIsStable:
    def test_the_same_finding_fingerprints_the_same(self) -> None:
        assert finding_fingerprint(_finding()) == finding_fingerprint(_finding())

    def test_moving_lines_does_not_change_it(self) -> None:
        """Lines move when code above them changes; the defect does not."""
        assert finding_fingerprint(_finding()) == finding_fingerprint(
            _finding(line_start=400, line_end=402),
        )

    def test_confidence_and_severity_do_not_change_it(self) -> None:
        assert finding_fingerprint(_finding()) == finding_fingerprint(
            _finding(severity="critical", confidence=99),
        )

    def test_a_different_file_changes_it(self) -> None:
        assert finding_fingerprint(_finding()) != finding_fingerprint(
            _finding(file_path="src/other.py"),
        )

    def test_a_different_check_changes_it(self) -> None:
        assert finding_fingerprint(_finding()) != finding_fingerprint(
            _finding(check_id="Q-ERR-02"),
        )

    def test_a_different_title_changes_it(self) -> None:
        assert finding_fingerprint(_finding()) != finding_fingerprint(
            _finding(title="Something else entirely"),
        )

    def test_trivial_rewording_does_not_change_it(self) -> None:
        """Punctuation and case are not a new defect."""
        assert finding_fingerprint(_finding()) == finding_fingerprint(
            _finding(title="Bare except swallows the error."),
        )


class TestMarkerRoundTrip:
    def test_a_marker_round_trips(self) -> None:
        fp = finding_fingerprint(_finding())
        assert extract_fingerprint(f"body text\n{marker_for(fp)}") == fp

    def test_the_marker_is_an_html_comment(self) -> None:
        assert marker_for("abc").startswith("<!--")
        assert marker_for("abc").endswith("-->")
        assert FINDING_MARKER in marker_for("abc")

    def test_a_body_without_a_marker_yields_nothing(self) -> None:
        assert extract_fingerprint("just a human comment") is None

    def test_a_malformed_marker_yields_nothing(self) -> None:
        assert extract_fingerprint("<!-- prbot:finding: -->") is None


class TestReconcile:
    def test_a_finding_with_no_thread_is_new(self) -> None:
        report = reconcile([_scored(_finding())], [])
        assert len(report.new) == 1
        assert report.persisting == []
        assert report.fixed == []

    def test_a_finding_with_an_open_thread_is_persisting(self) -> None:
        finding = _finding()
        thread = _thread(finding_fingerprint(finding))
        report = reconcile([_scored(finding)], [thread])
        assert report.new == []
        assert len(report.persisting) == 1
        assert report.fixed == []

    def test_a_thread_with_no_finding_is_fixed(self) -> None:
        thread = _thread(finding_fingerprint(_finding()))
        report = reconcile([], [thread])
        assert report.new == []
        assert len(report.fixed) == 1

    def test_a_resolved_thread_is_not_reported_again(self) -> None:
        """A human resolving it is the strongest acceptance signal there is."""
        finding = _finding()
        thread = _thread(finding_fingerprint(finding), resolved=True)
        report = reconcile([_scored(finding)], [thread])
        assert report.new == []
        assert report.persisting == []
        assert len(report.human_resolved) == 1

    def test_an_already_resolved_thread_is_not_re_resolved(self) -> None:
        thread = _thread(finding_fingerprint(_finding()), resolved=True)
        report = reconcile([], [thread])
        assert report.fixed == []

    def test_threads_without_our_marker_are_ignored(self) -> None:
        """Human review threads are none of prbot's business."""
        human = ReviewThread(
            id="t9", comment_id=9, body="Looks good to me",
            resolved=False, path="src/app.py", line=3,
        )
        report = reconcile([_scored(_finding())], [human])
        assert len(report.new) == 1
        assert report.fixed == []

    def test_a_mixed_run_splits_correctly(self) -> None:
        kept = _finding()
        gone = _finding(check_id="Q-MAINT-03", title="Long function")
        fresh = _finding(check_id="Q-API-01", title="Missing validation")
        threads = [
            _thread(finding_fingerprint(kept), id="a", comment_id=1),
            _thread(finding_fingerprint(gone), id="b", comment_id=2),
        ]
        report = reconcile([_scored(kept), _scored(fresh)], threads)
        assert [s.finding.check_id for s in report.new] == ["Q-API-01"]
        assert [s.finding.check_id for s, _ in report.persisting] == ["Q-ERR-01"]
        assert [t.id for t in report.fixed] == ["b"]

    def test_counts_are_exposed_for_metrics(self) -> None:
        report = reconcile([_scored(_finding())], [])
        assert report.counts() == {
            "findings_new": 1,
            "findings_persisting": 0,
            "findings_fixed": 0,
            "findings_human_resolved": 0,
        }


class TestOnlyOurOwnThreadsAreReconciled:
    """SEC-AUTH-02: the marker was the only identity check.

    Anyone who can comment on a pull request can paste prbot's finding
    marker into a review comment. Without an authorship check that comment
    became a thread prbot believed it had written: a marker matching a
    current finding suppressed that finding as already-reported, and one
    matching nothing got a 'no longer reported' reply and was resolved.
    """

    @staticmethod
    def _thread(fingerprint: str, author: str, **kw: Any) -> ReviewThread:
        base: dict[str, Any] = {
            "id": "t1", "comment_id": 1,
            "body": f"text\n{marker_for(fingerprint)}",
            "resolved": False, "path": "src/app.py", "line": 11,
            "author": author,
        }
        base.update(kw)
        return ReviewThread(**base)

    def test_a_forged_thread_does_not_suppress_a_finding(self) -> None:
        finding = _finding()
        forged = self._thread(finding_fingerprint(finding), "attacker")
        report = reconcile([_scored(finding)], [forged], bot_user="prbot[bot]")
        assert len(report.new) == 1
        assert report.persisting == []

    def test_a_forged_thread_is_not_replied_to_or_resolved(self) -> None:
        forged = self._thread(finding_fingerprint(_finding()), "attacker")
        report = reconcile([], [forged], bot_user="prbot[bot]")
        assert report.fixed == []

    def test_our_own_thread_still_reconciles(self) -> None:
        finding = _finding()
        ours = self._thread(finding_fingerprint(finding), "prbot[bot]")
        report = reconcile([_scored(finding)], [ours], bot_user="prbot[bot]")
        assert len(report.persisting) == 1

    def test_authorship_matching_is_case_insensitive(self) -> None:
        finding = _finding()
        ours = self._thread(finding_fingerprint(finding), "PRBot[Bot]")
        report = reconcile([_scored(finding)], [ours], bot_user="prbot[bot]")
        assert len(report.persisting) == 1

    def test_an_unknown_bot_user_falls_back_to_the_marker(self) -> None:
        """If identity cannot be established, do not silently drop state."""
        finding = _finding()
        ours = self._thread(finding_fingerprint(finding), "prbot[bot]")
        report = reconcile([_scored(finding)], [ours], bot_user="")
        assert len(report.persisting) == 1

    def test_a_thread_with_no_author_recorded_is_ignored(self) -> None:
        finding = _finding()
        anon = self._thread(finding_fingerprint(finding), "")
        report = reconcile([_scored(finding)], [anon], bot_user="prbot[bot]")
        assert len(report.new) == 1


def _posted_body(finding: Finding) -> str:
    """The body prbot actually posts for a finding, marker included."""
    from prbot.review.formatter import _format_issue_block

    return _format_issue_block(_scored(finding), marker=True)


class TestCheckIdRoundTrip:
    def test_the_check_id_is_read_back_from_a_posted_body(self) -> None:
        from prbot.review.identity import extract_check_id

        body = _posted_body(_finding(check_id="IAC-REPLACE-01"))
        assert extract_check_id(body) == "IAC-REPLACE-01"

    def test_a_body_without_the_header_yields_nothing(self) -> None:
        from prbot.review.identity import extract_check_id

        assert extract_check_id("some finding text") is None


class TestARewordedFindingKeepsItsThread:
    """The model rewords its titles between runs; the defect is the same.

    The fingerprint hashes the title, so a reworded title used to orphan the
    thread: on terraform-modules MR 270 one IAC-REPLACE-01 at main.tf:88 was
    posted under two fingerprints in three pushes, and each new one was a new
    blocking discussion. When no fingerprint matches, a thread is still ours
    if it is on the same file, names the same check, and the finding's lines
    cover the line the thread is anchored on - the rule the scorer already
    uses for two reports of one check on the same lines being one defect.
    """

    _BEFORE = _finding(
        check_id="IAC-REPLACE-01",
        title="iam_role_name has no lifecycle protection",
        line_start=60, line_end=88,
    )
    _AFTER = _finding(
        check_id="IAC-REPLACE-01",
        title="IAM role name/path override forces replacement of the role",
        line_start=60, line_end=88,
    )

    def _thread_from(self, finding: Finding, **overrides: Any) -> ReviewThread:
        base: dict[str, Any] = {
            "id": "t1", "comment_id": 101,
            "body": _posted_body(finding),
            "resolved": False,
            "path": finding.file_path,
            "line": finding.line_end,
        }
        base.update(overrides)
        return ReviewThread(**base)

    def test_the_guard_the_titles_really_fingerprint_differently(self) -> None:
        assert finding_fingerprint(self._BEFORE) != finding_fingerprint(
            self._AFTER,
        )

    def test_a_reworded_finding_persists_on_its_open_thread(self) -> None:
        thread = self._thread_from(self._BEFORE)
        report = reconcile([_scored(self._AFTER)], [thread])
        assert report.new == []
        assert [t.id for _, t in report.persisting] == ["t1"]
        assert report.fixed == []

    def test_a_reworded_finding_is_not_re_raised_on_a_resolved_thread(
        self,
    ) -> None:
        thread = self._thread_from(self._BEFORE, resolved=True)
        report = reconcile([_scored(self._AFTER)], [thread])
        assert report.new == []
        assert [t.id for t in report.human_resolved] == ["t1"]

    def test_lines_that_miss_the_anchor_are_a_different_defect(self) -> None:
        elsewhere = _finding(
            check_id="IAC-REPLACE-01", title="guardrail name is ForceNew",
            line_start=200, line_end=210,
        )
        thread = self._thread_from(self._BEFORE)
        report = reconcile([_scored(elsewhere)], [thread])
        assert len(report.new) == 1
        assert [t.id for t in report.fixed] == ["t1"]

    def test_a_different_check_on_the_same_lines_is_a_different_defect(
        self,
    ) -> None:
        other = _finding(
            check_id="S-DATA-01", title="role can read every log",
            line_start=60, line_end=88,
        )
        thread = self._thread_from(self._BEFORE)
        report = reconcile([_scored(other)], [thread])
        assert len(report.new) == 1

    def test_a_different_file_is_a_different_defect(self) -> None:
        moved = _finding(
            check_id="IAC-REPLACE-01", title="reworded",
            file_path="src/other.py", line_start=60, line_end=88,
        )
        thread = self._thread_from(self._BEFORE)
        report = reconcile([_scored(moved)], [thread])
        assert len(report.new) == 1

    def test_an_exact_match_wins_the_thread(self) -> None:
        """A thread is claimed once, and by its own fingerprint first."""
        thread = self._thread_from(self._BEFORE)
        report = reconcile(
            [_scored(self._AFTER), _scored(self._BEFORE)], [thread],
        )
        assert [sf.finding.title for sf, _ in report.persisting] == [
            self._BEFORE.title,
        ]
        assert [sf.finding.title for sf in report.new] == [self._AFTER.title]

    def test_a_thread_without_a_check_header_is_matched_exactly_or_not_at_all(
        self,
    ) -> None:
        thread = _thread("0" * 16, line=70)
        report = reconcile([_scored(self._AFTER)], [thread])
        assert len(report.new) == 1

    def test_a_forged_thread_is_not_claimed_by_rewording(self) -> None:
        thread = self._thread_from(self._BEFORE, author="mallory")
        report = reconcile(
            [_scored(self._AFTER)], [thread], bot_user="prbot",
        )
        assert len(report.new) == 1
