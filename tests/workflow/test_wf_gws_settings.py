"""Workflow tests for the Google Workspace integration toggle.

Shim `gws` with tests/fixtures/fake_gws.sh on PATH so the full
enable→install→disable round-trip runs without network.
"""

from pathlib import Path

import pytest

from app.models.app_settings import AppSettings
from app.workspace import gws_integration

pytestmark = pytest.mark.workflow


FIXTURES = Path(__file__).parent.parent / 'fixtures'
FAKE_GWS = FIXTURES / 'fake_gws.sh'


@pytest.fixture
def with_fake_gws(monkeypatch):
    """Prepend a bin dir containing a `gws` → fake_gws.sh symlink."""

    def _install(tmpdir: Path, auth: str = 'ok') -> Path:
        bin_dir = tmpdir / 'bin'
        bin_dir.mkdir(parents=True, exist_ok=True)
        link = bin_dir / 'gws'
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(FAKE_GWS)
        monkeypatch.setenv('PATH', f'{bin_dir}:/usr/bin:/bin')
        monkeypatch.setenv('GWS_FAKE_AUTH', auth)
        return bin_dir

    return _install


@pytest.fixture
def isolated_gws_dir(monkeypatch, tmp_path):
    """Redirect gws_integration's workspace writes into tmp_path."""
    monkeypatch.setattr(gws_integration, 'VIBE_SELLER_DIR', tmp_path)
    (tmp_path / '.claude' / 'skills').mkdir(parents=True)
    return tmp_path


class TestStatusEndpoint:
    async def test_status_missing_binary(
        self, admin_client, isolated_gws_dir, monkeypatch
    ):
        monkeypatch.setenv('PATH', '')
        r = await admin_client.get('/api/settings/google-workspace/status')
        assert r.status_code == 200
        body = r.json()
        assert body['binary'] is False
        assert body['auth'] is False
        assert body['enabled'] is False
        assert body['installed'] is False

    async def test_status_ok(
        self, admin_client, isolated_gws_dir, with_fake_gws, tmp_path
    ):
        with_fake_gws(tmp_path, auth='ok')
        r = await admin_client.get('/api/settings/google-workspace/status')
        body = r.json()
        assert body['binary'] is True
        assert body['auth'] is True
        assert body['enabled'] is False  # not yet toggled


class TestEnableGate:
    async def test_enable_rejects_when_no_binary(
        self,
        admin_client,
        isolated_gws_dir,
        monkeypatch,
        override_async_session,
    ):
        monkeypatch.setenv('PATH', '')
        r = await admin_client.post('/api/settings/google-workspace/enable')
        assert r.status_code == 400
        assert 'binary' in r.json()['detail'].lower()

        # Flag didn't flip, filesystem clean
        async with override_async_session() as db:
            row = await db.get(AppSettings, 'google_workspace_enabled')
            assert row is None
        assert not (isolated_gws_dir / '.claude' / 'skills' / 'gws').exists()

    async def test_enable_rejects_when_not_authed(
        self,
        admin_client,
        isolated_gws_dir,
        with_fake_gws,
        tmp_path,
    ):
        with_fake_gws(tmp_path, auth='fail')
        r = await admin_client.post('/api/settings/google-workspace/enable')
        assert r.status_code == 400
        assert 'auth' in r.json()['detail'].lower()
        assert not (isolated_gws_dir / '.claude' / 'skills' / 'gws').exists()


class TestEnableDisableRoundTrip:
    async def test_enable_happy_path(
        self,
        admin_client,
        isolated_gws_dir,
        with_fake_gws,
        tmp_path,
        override_async_session,
    ):
        with_fake_gws(tmp_path, auth='ok')
        r = await admin_client.post('/api/settings/google-workspace/enable')
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['ok'] is True
        assert body['count'] == 19

        gws_dir = isolated_gws_dir / '.claude' / 'skills' / 'gws'
        assert (gws_dir / 'SKILL.md').is_file()

        # DB flag flipped
        async with override_async_session() as db:
            row = await db.get(AppSettings, 'google_workspace_enabled')
            assert row is not None and row.value == 'true'

        # /status reflects installed state
        s = await admin_client.get('/api/settings/google-workspace/status')
        assert s.json()['enabled'] is True
        assert s.json()['installed'] is True

    async def test_enable_then_disable(
        self,
        admin_client,
        isolated_gws_dir,
        with_fake_gws,
        tmp_path,
        override_async_session,
    ):
        with_fake_gws(tmp_path, auth='ok')
        await admin_client.post('/api/settings/google-workspace/enable')

        # Seed a sibling skill that must survive disable
        sibling = isolated_gws_dir / '.claude' / 'skills' / 'browser-use'
        sibling.mkdir(parents=True)
        (sibling / 'SKILL.md').write_text('# browser-use\n')

        r = await admin_client.post('/api/settings/google-workspace/disable')
        assert r.status_code == 200
        assert r.json()['removed'] is True

        gws_dir = isolated_gws_dir / '.claude' / 'skills' / 'gws'
        assert not gws_dir.exists()
        assert (sibling / 'SKILL.md').read_text() == '# browser-use\n'

        async with override_async_session() as db:
            row = await db.get(AppSettings, 'google_workspace_enabled')
            assert row is not None and row.value == 'false'

    async def test_re_enable_is_idempotent(
        self, admin_client, isolated_gws_dir, with_fake_gws, tmp_path
    ):
        with_fake_gws(tmp_path, auth='ok')
        r1 = await admin_client.post('/api/settings/google-workspace/enable')
        r2 = await admin_client.post('/api/settings/google-workspace/enable')
        assert r1.status_code == 200 and r2.status_code == 200

        # File tree still clean — no leftover staging/backup dirs
        skills = isolated_gws_dir / '.claude' / 'skills'
        leftovers = [
            p.name
            for p in skills.iterdir()
            if p.name.startswith(('.tmp_gws_', '.bak_gws'))
        ]
        assert leftovers == []

    async def test_disable_without_prior_install_is_noop(
        self,
        admin_client,
        isolated_gws_dir,
        override_async_session,
    ):
        r = await admin_client.post('/api/settings/google-workspace/disable')
        assert r.status_code == 200
        assert r.json()['removed'] is False
        async with override_async_session() as db:
            row = await db.get(AppSettings, 'google_workspace_enabled')
            assert row is not None and row.value == 'false'


class TestAuthz:
    async def test_non_admin_cannot_enable(
        self, member_client, isolated_gws_dir, with_fake_gws, tmp_path
    ):
        with_fake_gws(tmp_path, auth='ok')
        r = await member_client.post('/api/settings/google-workspace/enable')
        assert r.status_code == 403
        assert not (isolated_gws_dir / '.claude' / 'skills' / 'gws').exists()

    async def test_non_admin_cannot_disable(
        self, member_client, isolated_gws_dir
    ):
        r = await member_client.post('/api/settings/google-workspace/disable')
        assert r.status_code == 403


class TestWorkspaceIndexIntegration:
    async def test_only_one_top_level_gws_entry(
        self,
        admin_client,
        isolated_gws_dir,
        with_fake_gws,
        tmp_path,
    ):
        """After enable, the skills dir contains exactly one `gws`
        sibling (umbrella layout, not 19 flat `gws-*` siblings).

        This is what Claude Code's skill discovery sees: direct
        children of .claude/skills/. Verified at the filesystem level
        — the WorkspaceManager discovery semantics are covered by the
        unit tests on manager.get_structured() elsewhere.
        """
        with_fake_gws(tmp_path, auth='ok')
        await admin_client.post('/api/settings/google-workspace/enable')

        skills_root = isolated_gws_dir / '.claude' / 'skills'
        top_level = sorted(
            p.name
            for p in skills_root.iterdir()
            if p.is_dir() and not p.name.startswith('.')
        )
        # Exactly one gws-related entry, and it's the umbrella.
        gws_entries = [
            n for n in top_level if n == 'gws' or n.startswith('gws-')
        ]
        assert gws_entries == ['gws'], (
            f'expected a single `gws/` umbrella, got {gws_entries}'
        )

        # Umbrella frontmatter mentions Google Workspace → agent index
        # will show it with a useful description line.
        umbrella = (skills_root / 'gws' / 'SKILL.md').read_text()
        assert 'name: gws' in umbrella
        assert 'Google Workspace' in umbrella
