"""The ads-report reviewer verdict gate (``stop_gates.report_reviewer``).

This is the shared enforcement both completion paths use — the Stop hook
(``bash_safety.check_review_status``) and the ``set_task_result`` MCP call
(``routers/tasks.py``). Gating only the Stop hook let a backend that
finishes via ``set_task_result`` complete a shallow-but-covering report
with the reviewer never spawned (the all-ads slip-through). These tests
pin the verdict logic directly; the two callers just delegate here.
"""

import os

import pytest

from app.ai.stop_gates import report_reviewer as rr


@pytest.mark.unit
class TestReviewerVerdict:
    def _audit(self, tmp_path):
        (tmp_path / 'AD_AUDIT_2026-07-09.md').write_text(
            '# r\n\n## Amazon SA\n', encoding='utf-8'
        )

    def test_none_dir(self):
        assert rr.reviewer_verdict(None) is None

    def test_no_report_no_review_still_requires_reviewer(self, tmp_path):
        # Always-require: even a task with no AD_AUDIT and no REVIEW must
        # route to the reviewer (which then signs off fast if there was
        # nothing to verify). The server never pre-judges lookup vs report.
        deny = rr.reviewer_verdict(tmp_path)
        assert deny is not None and 'ads-report-review' in deny

    def test_no_review_file_denies(self, tmp_path):
        # The core slip-through: report on disk, floor passed, but the
        # reviewer never ran → must deny, naming the reviewer to spawn.
        self._audit(tmp_path)
        deny = rr.reviewer_verdict(tmp_path)
        assert deny is not None
        assert 'ads-report-review' in deny and 'Reviewer never ran' in deny

    def test_status_ok_passes(self, tmp_path):
        self._audit(tmp_path)
        (tmp_path / 'REVIEW_2026-07-09_iter1.md').write_text(
            '# Review\nStatus: ok\n', encoding='utf-8'
        )
        assert rr.reviewer_verdict(tmp_path) is None

    def test_status_gaps_denies_with_pointer(self, tmp_path):
        self._audit(tmp_path)
        (tmp_path / 'REVIEW_2026-07-09_iter2.md').write_text(
            '# Review\nStatus: gaps\nMissing word-level drill on 3 campaigns\n',
            encoding='utf-8',
        )
        deny = rr.reviewer_verdict(tmp_path)
        assert deny is not None
        assert 'iter 2' in deny and 'iter3' in deny

    def test_incomplete_before_cap_denies(self, tmp_path):
        self._audit(tmp_path)
        (tmp_path / 'REVIEW_2026-07-09_iter2.md').write_text(
            '# Review\nStatus: incomplete\n', encoding='utf-8'
        )
        deny = rr.reviewer_verdict(tmp_path)
        assert deny is not None and 'only valid at iter' in deny

    def test_incomplete_at_cap_accepts(self, tmp_path):
        self._audit(tmp_path)
        (
            tmp_path / f'REVIEW_2026-07-09_iter{rr.REVIEW_MAX_ITERS}.md'
        ).write_text('# Review\nStatus: incomplete\n', encoding='utf-8')
        assert rr.reviewer_verdict(tmp_path) is None

    def test_nonstandard_filename_accepted(self, tmp_path):
        # A weak model may name it <PRODUCT>_REVIEW_<date>.md; the Status
        # line gates, not the filename.
        self._audit(tmp_path)
        (tmp_path / 'WIDGET006_REVIEW_2026-07-09.md').write_text(
            '# review\nStatus: ok\n', encoding='utf-8'
        )
        assert rr.reviewer_verdict(tmp_path) is None

    def test_lowercase_review_name_accepted(self, tmp_path):
        # A weak model may write a lowercase name; case-sensitive glob
        # used to MISS it and falsely trap the run.
        self._audit(tmp_path)
        (tmp_path / 'review_2026-07-09.md').write_text(
            '# review\nStatus: ok\n', encoding='utf-8'
        )
        assert rr.reviewer_verdict(tmp_path) is None

    def test_preview_substring_not_counted(self, tmp_path):
        # ``PREVIEW.md`` contains "review" as a substring but is not a
        # review file — the token match must reject it, so the gate still
        # denies (reviewer never ran).
        self._audit(tmp_path)
        (tmp_path / 'PREVIEW.md').write_text(
            '# preview\nStatus: ok\n', encoding='utf-8'
        )
        deny = rr.reviewer_verdict(tmp_path)
        assert deny is not None and 'Reviewer never ran' in deny

    def test_exec_review_not_counted(self, tmp_path):
        # An EXEC_REVIEW_* (phase-4 execution review) must NOT satisfy the
        # report reviewer.
        self._audit(tmp_path)
        (tmp_path / 'EXEC_REVIEW_2026-07-09_iter1.md').write_text(
            '# exec\nStatus: ok\n', encoding='utf-8'
        )
        deny = rr.reviewer_verdict(tmp_path)
        assert deny is not None and 'Reviewer never ran' in deny

    def test_missing_status_line_denies(self, tmp_path):
        self._audit(tmp_path)
        (tmp_path / 'REVIEW_2026-07-09_iter1.md').write_text(
            '# review with no status\n', encoding='utf-8'
        )
        deny = rr.reviewer_verdict(tmp_path)
        assert deny is not None and 'Status:' in deny

    def test_newest_by_mtime_wins_not_highest_iter(self, tmp_path):
        # A stale higher-iter verdict from a prior audit cycle must not
        # gate today's audit — the most recently WRITTEN review governs.
        self._audit(tmp_path)
        old = tmp_path / 'REVIEW_2026-05-01_iter7.md'
        old.write_text('# old\nStatus: gaps\n', encoding='utf-8')
        new = tmp_path / 'REVIEW_2026-07-09_iter1.md'
        new.write_text('# new\nStatus: ok\n', encoding='utf-8')
        os.utime(old, (1_600_000_000, 1_600_000_000))
        os.utime(new, (1_700_000_000, 1_700_000_000))
        assert rr.reviewer_verdict(tmp_path) is None

    def test_leading_ok_but_bolded_incomplete_denies(self, tmp_path):
        # The live failure: a reviewer top-stamps ``Status: ok`` but its
        # conclusion is ``**Status: incomplete**`` (bolded, off line-start).
        # The gate used to read only the leading ``ok`` and pass a report
        # with a known error. It must fail-closed on the conflict.
        self._audit(tmp_path)
        (tmp_path / 'REVIEW_2026-07-09_iter1.md').write_text(
            'Status: ok\n\n# Review\n\n'
            '## Conclusion\n\n**Status: incomplete**\n'
            'One numerical error: total is 140 but should be 145.\n',
            encoding='utf-8',
        )
        deny = rr.reviewer_verdict(tmp_path)
        assert deny is not None
        # incomplete at iter 1 (< cap) → keep iterating, and the conflict
        # is surfaced so the reviewer rewrites a single coherent verdict.
        assert 'only valid at iter' in deny
        assert 'conflicting Status' in deny

    def test_leading_ok_but_gaps_in_body_denies(self, tmp_path):
        # Same fail-closed rule for a body ``Status: gaps`` under a leading
        # ``ok`` — gaps is the most-conservative verdict and must win.
        self._audit(tmp_path)
        (tmp_path / 'REVIEW_2026-07-09_iter2.md').write_text(
            'Status: ok\n# Review\n- Status: gaps\nmissing drill\n',
            encoding='utf-8',
        )
        deny = rr.reviewer_verdict(tmp_path)
        assert deny is not None
        assert 'iter 2' in deny and 'iter3' in deny

    def test_repeated_ok_only_still_passes(self, tmp_path):
        # Two ``ok`` lines is not a conflict — a genuinely clean review
        # must still pass (no false trap).
        self._audit(tmp_path)
        (tmp_path / 'REVIEW_2026-07-09_iter1.md').write_text(
            'Status: ok\n# Review\nAll totals reconcile.\nStatus: ok\n',
            encoding='utf-8',
        )
        assert rr.reviewer_verdict(tmp_path) is None


@pytest.mark.unit
class TestPartialBanner:
    def test_banner_marks_unverified(self):
        banner = rr.partial_banner()
        assert 'Unverified' in banner or 'UNVERIFIED' in banner
        assert banner.endswith('\n')
