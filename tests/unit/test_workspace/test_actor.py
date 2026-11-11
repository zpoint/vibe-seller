"""Unit tests for workspace commit attribution (``app/workspace/actor.py``).

The invariant: a workspace commit made while a signed-in caller's
request is on the stack is authored by that caller (with a ``Via:``
trailer saying how they authenticated); everything else — no request,
a background scheduler, a task pipeline — keeps the agent identity.
"""

from pathlib import Path

import git as gitlib
import pytest

from app.ai.turn_hold_budget import reset_ledger
from app.models.user import User
from app.workspace.actor import (
    actor_for,
    clear_request_actor,
    current_actor,
    set_request_actor,
)
from app.workspace.ignored_paths import git_env
from app.workspace.manager import WorkspaceManager

pytestmark = pytest.mark.unit

AGENT_NAME = 'Vibe Seller'
AGENT_EMAIL = 'agent@vibe-seller.local'


def _user(username: str) -> User:
    return User(id='u1', username=username, role='admin')


class TestActorFor:
    def test_identity_shape(self):
        actor = actor_for(_user('alice'), 'session')
        assert actor.name == 'alice'
        assert actor.email == 'alice@users.vibe-seller.local'
        assert actor.via == 'session'

    def test_spaces_survive_the_name_not_the_email(self):
        actor = actor_for(_user('Alice Chen'), 'api_token')
        assert actor.name == 'Alice Chen'
        assert actor.email == 'alice_chen@users.vibe-seller.local'

    def test_git_control_characters_cannot_forge_an_author_line(self):
        """A crafted username must not be able to inject a second
        author into the commit object (``<``, ``>``, newlines)."""
        actor = actor_for(_user('alice <alice@evil.example>\n'), 'session')
        assert '\n' not in actor.name
        assert '<' not in actor.name
        assert '>' not in actor.name
        assert actor.email.endswith('@users.vibe-seller.local')

    def test_empty_username_falls_back(self):
        actor = actor_for(_user('   '), 'no_auth')
        assert actor.name == 'user'
        assert actor.email == 'user@users.vibe-seller.local'


class TestGitEnv:
    def test_no_actor_is_the_agent_identity(self):
        clear_request_actor()
        env = git_env()
        assert env['GIT_AUTHOR_NAME'] == AGENT_NAME
        assert env['GIT_AUTHOR_EMAIL'] == AGENT_EMAIL

    def test_an_actor_wins_over_the_agent_identity(self):
        set_request_actor(actor_for(_user('alice'), 'api_token'))
        try:
            env = git_env()
            assert env['GIT_AUTHOR_NAME'] == 'alice'
            assert env['GIT_AUTHOR_EMAIL'] == ('alice@users.vibe-seller.local')
            assert env['GIT_COMMITTER_NAME'] == 'alice'
        finally:
            clear_request_actor()

    def test_clearing_restores_the_agent_identity(self):
        set_request_actor(actor_for(_user('alice'), 'session'))
        clear_request_actor()
        assert current_actor() is None
        assert git_env()['GIT_AUTHOR_NAME'] == AGENT_NAME


@pytest.fixture
def ws(tmp_path: Path) -> WorkspaceManager:
    """A real manager over a fresh git repo (no venv build)."""
    mgr = WorkspaceManager(root=tmp_path)

    async def _no_venv():
        return None

    mgr._ensure_venv = _no_venv  # type: ignore[method-assign]
    gitlib.Repo.init(str(tmp_path))
    return mgr


class TestAutoCommitAttribution:
    async def test_actor_authored_with_via_trailer(self, ws, tmp_path):
        (tmp_path / 'knowledge').mkdir(exist_ok=True)
        set_request_actor(actor_for(_user('alice'), 'api_token'))
        try:
            await ws.write_file('knowledge/notes.md', 'by alice')
        finally:
            clear_request_actor()
        commit = gitlib.Repo(str(tmp_path)).head.commit
        assert commit.author.name == 'alice'
        assert commit.author.email == 'alice@users.vibe-seller.local'
        assert 'Via: api_token' in commit.message

    async def test_no_actor_is_agent_authored_without_trailer(
        self, ws, tmp_path
    ):
        (tmp_path / 'knowledge').mkdir(exist_ok=True)
        await ws.write_file('knowledge/agent.md', 'by the agent')
        commit = gitlib.Repo(str(tmp_path)).head.commit
        assert commit.author.name == AGENT_NAME
        assert commit.author.email == AGENT_EMAIL
        assert 'Via:' not in commit.message

    async def test_reset_ledger_makes_the_pipeline_agent_authored(
        self, ws, tmp_path
    ):
        """The orchestrator-entry reset: even with a caller's context
        copied into a spawned pipeline task, its knowledge commits are
        the agent's."""
        (tmp_path / 'knowledge').mkdir(exist_ok=True)
        set_request_actor(actor_for(_user('alice'), 'session'))
        try:
            reset_ledger('task-1')  # what every orchestrator calls first
            await ws.write_file('knowledge/swept.md', 'agent knowledge')
        finally:
            clear_request_actor()
        commit = gitlib.Repo(str(tmp_path)).head.commit
        assert commit.author.name == AGENT_NAME
        assert 'Via:' not in commit.message

    async def test_history_reports_the_author(self, ws, tmp_path):
        (tmp_path / 'knowledge').mkdir(exist_ok=True)
        set_request_actor(actor_for(_user('bob'), 'session'))
        try:
            await ws.write_file('knowledge/who.md', 'x')
        finally:
            clear_request_actor()
        history = await ws.file_history('knowledge/who.md')
        # ``file_history`` reports ``str(commit.author)`` — the NAME
        # (GitPython), which already distinguishes caller from agent;
        # the email rides in the commit object itself.
        assert history[0]['author'] == 'bob'
        assert 'Via: session' in history[0]['message']
