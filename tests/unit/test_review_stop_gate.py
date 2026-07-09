"""The legacy REVIEW-file Stop gate must not fire for server-reviewed audits.

amazon/noon audit reports are reviewed server-side at ``set_task_result``
(``ad_completeness_review``). The Stop-hook REVIEW-file
gate (``bash_safety.check_review_status``) used to ALSO fire for them,
forcing redundant ``ads-format-review`` subagent iterations at every
Stop attempt — the agent sat at noon 1/46 polishing format instead of
drilling. The gate now skips any audit whose content a server gate
covers; only an unrecognized audit (some other platform with no
server gate) keeps the REVIEW-file loop as its fallback enforcement.
"""

import pytest

from app.ai.bash_safety import check_review_status
from app.ai.stop_gates import ad_completeness_review


@pytest.mark.unit
class TestReviewStopGate:
    def test_no_audit_no_gate(self, tmp_path):
        assert check_review_status(tmp_path) is None

    def test_amazon_noon_audit_skips_legacy_gate(self, tmp_path, monkeypatch):
        # Server-reviewed report where the completeness check PASSES: the
        # Stop hook must NOT demand a legacy REVIEW file / format-review
        # subagent — it defers to the server completeness reviewer, which
        # is satisfied, so the stop is allowed. A real full report is
        # large; stub the pure check to "passes" so this test targets the
        # wiring (legacy gate skipped, stop allowed) without a brittle
        # fixture. drill_incomplete_reason delegates to check(text, None,
        # None).
        monkeypatch.setattr(
            ad_completeness_review, 'check', lambda *a, **k: None
        )
        (tmp_path / 'AD_AUDIT_2026-06-10.md').write_text(
            '# 广告优化建议\n\n## Amazon US\n\n**进度**: drilled 31/31 active\n'
            '\n## noon EG\n\n**进度**: drilled 39/39 active\n',
            encoding='utf-8',
        )
        assert check_review_status(tmp_path) is None

    def test_amazon_noon_audit_underdrilled_blocks(self, tmp_path):
        # The 3/24 bypass: an under-drilled amazon/noon audit must block
        # the stop (completeness backstop), NOT via the legacy
        # ads-format-review path.
        (tmp_path / 'AD_AUDIT_2026-06-10.md').write_text(
            '# 广告优化建议\n\n## Amazon US\n\n**进度**: drilled 4/31 active\n'
            '\n## noon EG\n\n**进度**: drilled 2/39 active\n',
            encoding='utf-8',
        )
        deny = check_review_status(tmp_path)
        assert deny is not None
        assert 'ads-format-review' not in deny
        assert '4/31' in deny and '2/39' in deny

    def test_underdrilled_stop_path_fails_open(self, tmp_path):
        # Bounded: an agent that keeps ending its turn on an incomplete
        # report is blocked, but only up to STALL_CAP times, then the
        # stop-path fails open so it is not trapped forever.
        acr = ad_completeness_review

        txt = (
            '# 广告优化建议\n\n## Amazon US\n\n**进度**: drilled 1/31 active\n'
        )
        tid = 'stoppath-failopen'
        acr.reset_progress(tid)
        outs = [
            acr.drill_incomplete_reason(txt, tid)
            for _ in range(acr.STALL_CAP + 2)
        ]
        assert outs[0] is not None
        assert outs[-1] is None  # failed open after STALL_CAP blocks
        acr.reset_progress(tid)

    def test_unrecognized_audit_keeps_gate(self, tmp_path):
        # An audit no server gate recognizes (some other platform) still
        # falls back to the REVIEW-file loop as its only enforcement.
        (tmp_path / 'AD_AUDIT_2026-06-10.md').write_text(
            '# 广告优化建议 — 某平台\n\n## 某场景\n\n| 计划 | 建议 |\n',
            encoding='utf-8',
        )
        deny = check_review_status(tmp_path)
        assert deny is not None
        assert 'ads-report-review' in deny

    def test_ad_skill_task_requires_active_reviewer(
        self, tmp_path, monkeypatch
    ):
        # An ad-skill-bound task must pass the coverage floor AND the
        # active ads-report-review reviewer. Floor passing but no REVIEW
        # file → still denied (reviewer never ran).
        import app.ai.stop_gates as sg

        monkeypatch.setattr(
            sg, 'recorded_skills', lambda t: frozenset({'amazon-ads'})
        )
        monkeypatch.setattr(
            ad_completeness_review, 'check', lambda *a, **k: None
        )
        (tmp_path / 'AD_AUDIT_2026-07-09.md').write_text(
            '# r\n\n## Amazon SA\n', encoding='utf-8'
        )
        deny = check_review_status(tmp_path)
        assert deny is not None and 'ads-report-review' in deny

    def test_ad_skill_task_passes_with_reviewer_ok(self, tmp_path, monkeypatch):
        import app.ai.stop_gates as sg

        monkeypatch.setattr(
            sg, 'recorded_skills', lambda t: frozenset({'amazon-ads'})
        )
        monkeypatch.setattr(
            ad_completeness_review, 'check', lambda *a, **k: None
        )
        (tmp_path / 'AD_AUDIT_2026-07-09.md').write_text(
            '# r\n\n## Amazon SA\n', encoding='utf-8'
        )
        (tmp_path / 'REVIEW_2026-07-09_iter1.md').write_text(
            '# Review\nStatus: ok\n', encoding='utf-8'
        )
        assert check_review_status(tmp_path) is None

    def test_review_file_nonstandard_name_accepted(self, tmp_path, monkeypatch):
        # A weak model may name the review file <PRODUCT>_REVIEW_<date>.md
        # instead of REVIEW_<date>_iter<N>.md. As long as it has a Status
        # line and isn't an EXEC_ review, the Status is what gates.
        import app.ai.stop_gates as sg

        monkeypatch.setattr(
            sg, 'recorded_skills', lambda t: frozenset({'amazon-ads'})
        )
        monkeypatch.setattr(
            ad_completeness_review, 'check', lambda *a, **k: None
        )
        (tmp_path / 'AD_AUDIT_2026-07-09.md').write_text(
            '# r\n\n## Amazon SA\n', encoding='utf-8'
        )
        (tmp_path / 'WIDGET006_REVIEW_2026-07-09.md').write_text(
            '# review\nStatus: ok\n', encoding='utf-8'
        )
        assert check_review_status(tmp_path) is None

    def test_exec_review_not_counted_as_report_review(
        self, tmp_path, monkeypatch
    ):
        # An EXEC_REVIEW_* file must NOT satisfy the phase-3 report gate.
        import app.ai.stop_gates as sg

        monkeypatch.setattr(
            sg, 'recorded_skills', lambda t: frozenset({'amazon-ads'})
        )
        monkeypatch.setattr(
            ad_completeness_review, 'check', lambda *a, **k: None
        )
        (tmp_path / 'AD_AUDIT_2026-07-09.md').write_text(
            '# r\n\n## Amazon SA\n', encoding='utf-8'
        )
        (tmp_path / 'EXEC_REVIEW_2026-07-09_iter1.md').write_text(
            '# exec\nStatus: ok\n', encoding='utf-8'
        )
        deny = check_review_status(tmp_path)
        assert deny is not None and 'ads-report-review' in deny

    def test_ad_skill_task_without_report_denied(self, tmp_path, monkeypatch):
        # Bound to an ad skill but no AD_AUDIT_*.md at all → denied, even
        # though a differently-named report might exist (closes the
        # filename-escape hole).
        import app.ai.stop_gates as sg

        monkeypatch.setattr(
            sg, 'recorded_skills', lambda t: frozenset({'noon-ads'})
        )
        (tmp_path / 'widget_AD_COMPARISON.md').write_text(
            '# r\n', encoding='utf-8'
        )
        deny = check_review_status(tmp_path)
        assert deny is not None and 'AD_AUDIT' in deny
