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


@pytest.mark.unit
class TestReviewStopGate:
    def test_no_audit_no_gate(self, tmp_path):
        assert check_review_status(tmp_path) is None

    def test_amazon_noon_audit_skips_legacy_gate(self, tmp_path):
        # Server-reviewed report (amazon/noon combo headers): the Stop
        # hook must NOT demand a REVIEW file / format-review subagent.
        (tmp_path / 'AD_AUDIT_2026-06-10.md').write_text(
            '# 广告优化建议\n\n## Amazon US\n\n**进度**: drilled 4/31 active\n'
            '\n## noon EG\n\n**进度**: drilled 2/39 active\n',
            encoding='utf-8',
        )
        assert check_review_status(tmp_path) is None

    def test_unrecognized_audit_keeps_gate(self, tmp_path):
        # An audit no server gate recognizes (some other platform) still
        # falls back to the REVIEW-file loop as its only enforcement.
        (tmp_path / 'AD_AUDIT_2026-06-10.md').write_text(
            '# 广告优化建议 — 某平台\n\n## 某场景\n\n| 计划 | 建议 |\n',
            encoding='utf-8',
        )
        deny = check_review_status(tmp_path)
        assert deny is not None
        assert 'ads-format-review' in deny
