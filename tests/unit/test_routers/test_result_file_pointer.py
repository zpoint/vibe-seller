"""Unit tests for the two ``set_task_result`` deliverable modes.

The agent composes long reports with the ``Write`` tool and hands over
either the TEXT (``result``) or a DECLARED file (``result_file``); the
server reads the file so the UI renders the report inline. A path that
points inside the task workspace resolves whether the agent passes it
relative (``./AD_AUDIT.md``), bare (``AD_AUDIT.md``) or absolute
(``/Users/…/tasks/<id>/AD_AUDIT.md``); traversal and outside paths never
resolve.

Regression #1 (kept fixed): an absolute path that pointed at the real
report used to fall through — the leading ``/`` was stripped and joined
onto task_root, producing a nonexistent nested path — so the literal path
string got stored as the result and the UI showed a path instead of the
report.

Regression #2 (the shape guess, removed): a ``result`` with no spaces that
ended in a document extension, or started with ``./`` or ``/``, was
REFUSED with a 400 when no such file existed. That judged an obligation
from the shape of the agent's own output, so a plain text answer named
``report.md`` or ``/2025-Q3`` could not be submitted at all — with no
round limit, unlike every real check. Whether a submission is a pointer is
now declared by the agent (``result_file``); ``result`` is always text,
and a ``result`` that names a REAL workspace file is still read (the
legacy pointer mode the skill docs use).
"""

from pathlib import Path

from fastapi import HTTPException
import pytest

from app.routers.task_submission import resolve_submitted_result
from app.routers.tasks_files import (
    resolve_declared_result_path,
    resolve_workspace_result_path,
)

pytestmark = pytest.mark.unit


def _root(tmp_path: Path) -> Path:
    root = (tmp_path / 'tasks' / 'task-abc').resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / 'AD_AUDIT_2099-01-01.md').write_text(
        '# 广告优化建议\n内容', encoding='utf-8'
    )
    return root


@pytest.mark.unit
class TestResolveWorkspaceResultPath:
    """The LEGACY inferred pointer mode: resolve, never refuse."""

    def test_relative_dot_slash_resolves(self, tmp_path):
        root = _root(tmp_path)
        got = resolve_workspace_result_path('./AD_AUDIT_2099-01-01.md', root)
        assert got == root / 'AD_AUDIT_2099-01-01.md'

    def test_bare_filename_resolves(self, tmp_path):
        # Skill docs pass the path with no "./" prefix
        # (`set_task_result("AD_AUDIT_<date>.md")`), so a bare filename
        # that exists in task_root must resolve or those calls store a
        # literal path as the result.
        root = _root(tmp_path)
        got = resolve_workspace_result_path('AD_AUDIT_2099-01-01.md', root)
        assert got == root / 'AD_AUDIT_2099-01-01.md'

    def test_absolute_path_inside_root_resolves(self, tmp_path):
        # The regression: agent passes the full absolute path it built
        # from cwd. Must resolve to the file, not be stored verbatim.
        root = _root(tmp_path)
        abs_path = str(root / 'AD_AUDIT_2099-01-01.md')
        got = resolve_workspace_result_path(abs_path, root)
        assert got == root / 'AD_AUDIT_2099-01-01.md'

    def test_absolute_path_outside_root_rejected(self, tmp_path):
        root = _root(tmp_path)
        assert resolve_workspace_result_path('/etc/passwd', root) is None

    def test_traversal_escape_rejected(self, tmp_path):
        root = _root(tmp_path)
        outside = root.parent / 'secret.md'
        outside.write_text('nope', encoding='utf-8')
        assert resolve_workspace_result_path('./../secret.md', root) is None

    def test_direct_content_is_not_a_path(self, tmp_path):
        root = _root(tmp_path)
        # Prose summary with no slash — treated as direct content.
        assert (
            resolve_workspace_result_path('Audit done: 2 active.', root) is None
        )

    def test_nonexistent_path_returns_none(self, tmp_path):
        root = _root(tmp_path)
        assert resolve_workspace_result_path('./missing.md', root) is None

    def test_multiline_string_is_direct_content(self, tmp_path):
        root = _root(tmp_path)
        # A multi-line markdown body that happens to contain a slash
        # must not be mistaken for a path.
        body = '# Report\n\nSee https://example.com/x for details.'
        assert resolve_workspace_result_path(body, root) is None

    def test_quoted_pointer_resolves(self, tmp_path):
        # Regression: the agent passed the path wrapped in literal
        # quotes ('"./AD_AUDIT_2026-06-10.md"'); resolution failed and
        # the 26-char quoted string was stored as the task result.
        root = _root(tmp_path)
        for raw in (
            '"./AD_AUDIT_2099-01-01.md"',
            "'./AD_AUDIT_2099-01-01.md'",
            '  "./AD_AUDIT_2099-01-01.md"  ',
        ):
            got = resolve_workspace_result_path(raw, root)
            assert got == root / 'AD_AUDIT_2099-01-01.md', raw


@pytest.mark.unit
class TestResolveDeclaredResultPath:
    """``result_file``: the agent states it, so no shape check applies."""

    def test_declared_name_without_an_extension_resolves(self, tmp_path):
        root = _root(tmp_path)
        (root / 'FINAL_REPORT').write_text('body', encoding='utf-8')
        assert resolve_declared_result_path('FINAL_REPORT', root) == (
            root / 'FINAL_REPORT'
        )
        # The inferred mode would never even try this name — no slash,
        # no document extension. That difference is the point.
        assert resolve_workspace_result_path('FINAL_REPORT', root) is None

    def test_declared_nested_path_resolves(self, tmp_path):
        root = _root(tmp_path)
        nested = root / 'out'
        nested.mkdir()
        (nested / 'summary').write_text('s', encoding='utf-8')
        assert resolve_declared_result_path('./out/summary', root) == (
            nested / 'summary'
        )

    def test_declared_missing_file_returns_none(self, tmp_path):
        root = _root(tmp_path)
        assert resolve_declared_result_path('./NOPE.md', root) is None

    def test_declared_escape_returns_none(self, tmp_path):
        root = _root(tmp_path)
        (root.parent / 'outside.md').write_text('x', encoding='utf-8')
        assert resolve_declared_result_path('../outside.md', root) is None
        assert resolve_declared_result_path('/etc/passwd', root) is None

    def test_declared_multiline_returns_none(self, tmp_path):
        root = _root(tmp_path)
        assert resolve_declared_result_path('a\nb.md', root) is None


@pytest.mark.unit
class TestResolveSubmittedResult:
    """The router-facing contract: text is text, a file is declared."""

    async def test_plain_text_that_looks_like_a_path(
        self, tmp_path
    ):
        """THE REGRESSION THIS FIX WAS MADE FOR.

        ``report.md`` and ``/2025-Q3`` as the agent's ANSWER are prose,
        not pointers: no file by that name exists, so nothing was
        delivered-but-lost. The old shape check 400'd both; neither is a
        refusal the agent can ever satisfy except by deleting its answer.
        """
        root = _root(tmp_path)
        for text in ('report.md', '/2025-Q3', './report.md', 'AUDIT.md'):
            got, from_file = await resolve_submitted_result(text, None, root)
            assert got == text, text
            assert from_file is False, text

    async def test_declared_file_is_read(self, tmp_path):
        root = _root(tmp_path)
        got, from_file = await resolve_submitted_result(
            None, './AD_AUDIT_2099-01-01.md', root
        )
        assert got.startswith('# 广告优化建议')
        assert from_file is True

    async def test_declared_missing_file_is_refused(self, tmp_path):
        """A DECLARATION that names nothing is an error, not a guess."""
        root = _root(tmp_path)
        with pytest.raises(HTTPException) as exc:
            await resolve_submitted_result(None, './gone.md', root)
        assert exc.value.status_code == 400
        assert 'result_file' in str(exc.value.detail)

    async def test_legacy_real_path_still_resolves(self, tmp_path):
        """Backward compatibility for the calls the skill docs show."""
        root = _root(tmp_path)
        got, from_file = await resolve_submitted_result(
            'AD_AUDIT_2099-01-01.md', None, root
        )
        assert got.startswith('# 广告优化建议')
        assert from_file is True

    async def test_both_fields_prefer_the_declaration(self, tmp_path):
        # The request model rejects both; the resolver must still have a
        # defined answer if one ever reaches it: the declaration wins.
        root = _root(tmp_path)
        got, from_file = await resolve_submitted_result(
            'plain text', './AD_AUDIT_2099-01-01.md', root
        )
        assert from_file is True
        assert got != 'plain text'
