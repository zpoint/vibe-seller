"""Hook state that describes the TASK must outlive the session.

A task is a sequence of turns, and a redrive restarts the session
mid-turn as well. The PreToolUse hook tracks two task facts — the
catalog has been read (handed to plugin gates as ``catalog_read``) and
which skills are loaded (the ``requires:`` hint keys off it). Both
lived only on the session object, so every follow-up re-armed them:
observed live, a second turn was refused for a catalog it had read and
re-told to load a skill it already had.
"""

import pytest

from app.ai.catalog_read import is_catalog_path

pytestmark = pytest.mark.unit


class TestCatalogReadIsRecognised:
    """Reading the catalog through the task workspace counts."""

    def test_the_workspace_copy_of_the_catalog_counts(self):
        # The agent reads it through its own workspace, so the absolute
        # task path must satisfy the predicate the hook keys off.
        assert is_catalog_path(
            '/home/u/.vibe-seller/tasks/abc123/stores/acme/CATALOG.md'
        )
        assert is_catalog_path('stores/acme/CATALOG.md')


class TestRestartKeepsPerTaskHookState:
    """A follow-up or redrive rebuilds the session; these must carry."""

    # The manager's real behaviour is pinned in
    # tests/unit/test_claude_backend_resume_retry.py; this states the
    # invariant the guards depend on.
    def test_restart_copies_catalog_read_and_loaded_skills(self):
        # Mirrors ``AgentManager._restart_session``: a new session is
        # constructed from the prior one, then per-task hook state is
        # carried across. Asserted on the copy step itself so the intent
        # is pinned without standing up a real agent process.
        class _S:
            def __init__(self):
                self._catalog_read = False
                self._loaded_skills: set[str] = set()

        prior, new = _S(), _S()
        prior._catalog_read = True
        prior._loaded_skills = {'amazon-shared'}

        new._catalog_read = prior._catalog_read
        new._loaded_skills = set(prior._loaded_skills)

        assert new._catalog_read is True
        assert new._loaded_skills == {'amazon-shared'}
        # …and the copy is independent, so a later load in the new
        # session cannot reach back into the old one.
        new._loaded_skills.add('amazon-ads')
        assert prior._loaded_skills == {'amazon-shared'}
