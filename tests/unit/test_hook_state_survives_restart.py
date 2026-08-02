"""Guard state that describes the TASK must outlive the session.

A task is a sequence of turns now, and a gate refusal restarts the
session mid-turn as well. Two PreToolUse guards track facts that belong
to the task, not to the process running it:

* the catalog-first guard, once the agent has read the store manifest;
* the skill-prereq check, once a required skill is loaded.

Both lived only on the session object, so every follow-up and every
redrive re-armed them. Observed live on a two-turn ad task: the agent
read ``stores/<slug>/CATALOG.md`` as its fourth action, and in the second
turn was denied five times for searching ``stores/`` — with the message
"Read the catalog first". It was trying to read the ad-change TSVs it
needed in order to satisfy a cooldown gate that was refusing its report.
The same restart re-triggered a skill-prereq error for a skill already
loaded earlier in the same task.

Neither denial was satisfiable from the agent's side, which is the shape
of failure worth pinning: a guard the agent cannot clear is worse than
no guard.
"""

import inspect

import pytest

from app.ai import claude_backend_manager as mgr
from app.ai.bash_safety import check_catalog_first, is_catalog_path

pytestmark = pytest.mark.unit


class TestTheGuardIsSatisfiableAtAll:
    """Reading the catalog must actually clear the guard."""

    CMD = 'ls stores/acme/ads/amazon/SA/'

    def test_it_blocks_before_the_catalog_is_read(self):
        assert check_catalog_first(self.CMD, catalog_read=False)

    def test_it_clears_once_the_catalog_is_read(self):
        assert check_catalog_first(self.CMD, catalog_read=True) is None

    def test_the_workspace_copy_of_the_catalog_counts(self):
        # The agent reads it through its own workspace, so the absolute
        # task path must satisfy the predicate the hook keys off.
        assert is_catalog_path(
            '/home/u/.vibe-seller/tasks/abc123/stores/acme/CATALOG.md'
        )
        assert is_catalog_path('stores/acme/CATALOG.md')


class TestRestartKeepsPerTaskHookState:
    """A follow-up or redrive rebuilds the session; these must carry."""

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

    def test_the_manager_actually_carries_them(self):
        # The behaviour above only helps if _restart_session does it.
        src = inspect.getsource(mgr)
        assert 'new_session._catalog_read = prior._catalog_read' in src, (
            'a follow-up would re-arm the catalog-first guard, denying '
            'the agent for something it already did'
        )
        assert 'new_session._loaded_skills = set(prior._loaded_skills)' in src
