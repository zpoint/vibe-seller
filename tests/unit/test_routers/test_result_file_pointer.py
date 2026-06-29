"""Unit tests for ``set_task_result`` file-pointer path resolution.

The agent composes long reports with the ``Write`` tool and passes only
the path to ``set_task_result``; the server reads the file so the UI
renders the report inline. The resolver must accept a path pointing at a
file *inside the task workspace* whether the agent passes it relative
(``./AD_AUDIT.md``) or absolute (``/Users/.../tasks/<id>/AD_AUDIT.md``),
while rejecting traversal and direct-content strings.

Regression: an absolute path that pointed at the real report used to
fall through (the leading ``/`` was stripped and joined onto task_root,
producing a nonexistent nested path), so the literal path string got
stored as the result and the UI showed a path instead of the report.
"""

from pathlib import Path

import pytest

from app.routers.tasks import resolve_workspace_result_path
from app.routers.tasks_files import looks_like_result_path


@pytest.mark.unit
class TestResolveWorkspaceResultPath:
    def _make_root(self, tmp_path: Path) -> Path:
        root = (tmp_path / 'tasks' / 'task-abc').resolve()
        root.mkdir(parents=True)
        (root / 'AD_AUDIT_2099-01-01.md').write_text(
            '# 广告优化建议\n内容', encoding='utf-8'
        )
        return root

    def test_relative_dot_slash_resolves(self, tmp_path):
        root = self._make_root(tmp_path)
        got = resolve_workspace_result_path('./AD_AUDIT_2099-01-01.md', root)
        assert got == root / 'AD_AUDIT_2099-01-01.md'

    def test_bare_filename_resolves(self, tmp_path):
        # A bare filename (no slash, no ./) that exists in task_root must
        # resolve — looks_like_result_path() flags bare ``*.md`` as a
        # pointer, so the resolver must agree or set_task_result 400s a
        # valid report (Copilot review #1/#3 on PR 203).
        root = self._make_root(tmp_path)
        got = resolve_workspace_result_path('AD_AUDIT_2099-01-01.md', root)
        assert got == root / 'AD_AUDIT_2099-01-01.md'
        # and the two helpers now agree on a bare filename
        assert looks_like_result_path('AD_AUDIT_2099-01-01.md') is True

    def test_absolute_path_inside_root_resolves(self, tmp_path):
        # The regression: agent passes the full absolute path it built
        # from cwd. Must resolve to the file, not be stored verbatim.
        root = self._make_root(tmp_path)
        abs_path = str(root / 'AD_AUDIT_2099-01-01.md')
        got = resolve_workspace_result_path(abs_path, root)
        assert got == root / 'AD_AUDIT_2099-01-01.md'

    def test_absolute_path_outside_root_rejected(self, tmp_path):
        root = self._make_root(tmp_path)
        assert resolve_workspace_result_path('/etc/passwd', root) is None

    def test_traversal_escape_rejected(self, tmp_path):
        root = self._make_root(tmp_path)
        outside = root.parent / 'secret.md'
        outside.write_text('nope', encoding='utf-8')
        assert resolve_workspace_result_path('./../secret.md', root) is None

    def test_direct_content_is_not_a_path(self, tmp_path):
        root = self._make_root(tmp_path)
        # Prose summary with no slash — treated as direct content.
        assert (
            resolve_workspace_result_path('Audit done: 2 active.', root) is None
        )

    def test_nonexistent_path_returns_none(self, tmp_path):
        root = self._make_root(tmp_path)
        assert resolve_workspace_result_path('./missing.md', root) is None

    def test_multiline_string_is_direct_content(self, tmp_path):
        root = self._make_root(tmp_path)
        # A multi-line markdown body that happens to contain a slash
        # must not be mistaken for a path.
        body = '# Report\n\nSee https://example.com/x for details.'
        assert resolve_workspace_result_path(body, root) is None

    def test_quoted_pointer_resolves(self, tmp_path):
        # Regression: the agent passed the path wrapped in literal
        # quotes ('"./AD_AUDIT_2026-06-10.md"'); resolution failed and
        # the 26-char quoted string was stored as the task result,
        # bypassing every content gate vacuously.
        root = self._make_root(tmp_path)
        for raw in (
            '"./AD_AUDIT_2099-01-01.md"',
            "'./AD_AUDIT_2099-01-01.md'",
            '  "./AD_AUDIT_2099-01-01.md"  ',
        ):
            got = resolve_workspace_result_path(raw, root)
            assert got == root / 'AD_AUDIT_2099-01-01.md', raw


@pytest.mark.unit
class TestLooksLikeResultPath:
    """Dangling-pointer detection: path-like values that fail to resolve
    must be REJECTED by set_task_result, not demoted to direct content
    (the gate-bypass that completed an audit at 3/46 with a literal
    quoted path as its result)."""

    def test_pointers_detected(self):
        for raw in (
            './AD_AUDIT_2026-06-10.md',
            '"./AD_AUDIT_2026-06-10.md"',
            '/abs/path/report.md',
            'reports/audit.html',
            './missing.md',
        ):
            assert looks_like_result_path(raw) is True, raw

    def test_content_not_detected(self):
        for raw in (
            'Audit done: ACOS 30%/ROAS 3.33 threshold applied.',  # slash+spaces
            '# Report\n\nbody',  # multiline
            'All campaigns reviewed.',
            '',
        ):
            assert looks_like_result_path(raw) is False, repr(raw)
