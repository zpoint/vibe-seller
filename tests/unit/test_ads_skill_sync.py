"""The ads skill is updated on the same schedule as every other skill.

The skill ships from the bound ads service, which holds its version. The
skills sync that runs before tasks asks that version too, and pulls the
bundle only when it moved.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from app import ads_client, ads_skill
from app.models.app_settings import AppSettings
from app.workspace.skills_sync import SkillsSyncManager

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class FakeSession:
    def __init__(self, version: str | None = None):
        self.rows = {}
        if version is not None:
            self.rows[ads_skill.VERSION_KEY] = AppSettings(
                key=ads_skill.VERSION_KEY, value=version
            )

    async def get(self, _model, key):
        return self.rows.get(key)

    def add(self, row):
        self.rows[row.key] = row

    async def commit(self):
        pass


@pytest.fixture
def service(tmp_path, monkeypatch):
    """A bound service at version ``v2``, every call recorded."""
    root = tmp_path / 'skills' / ads_skill.SKILL_NAME
    monkeypatch.setattr(ads_skill, 'skill_dir', lambda: root)
    state = {
        'session': FakeSession(),
        'calls': [],
        'bound': True,
        'authorized': True,
    }

    @asynccontextmanager
    async def fake_async_session():
        yield state['session']

    async def fake_config(_session):
        return {'configured': state['bound']}

    async def fake_call(_session, path, **_kw):
        state['calls'].append(path)
        if path == '/skill/version':
            return {'version': 'v2'}
        return {'version': 'v2', 'files': {'SKILL.md': '# v2'}}

    monkeypatch.setattr(ads_skill, 'async_session', fake_async_session)
    monkeypatch.setattr(ads_client, 'get_config', fake_config)

    async def fake_any_authorized(_db):
        return state['authorized']

    monkeypatch.setattr(ads_skill, '_any_store_authorized', fake_any_authorized)
    monkeypatch.setattr(ads_client, 'call', fake_call)
    state['root'] = root
    return state


class TestRefreshIfBound:
    async def test_a_newer_version_on_the_service_is_installed(self, service):
        service['root'].mkdir(parents=True)
        (service['root'] / 'SKILL.md').write_text('# v1')
        service['session'] = FakeSession('v1')

        result = await ads_skill.refresh_if_bound()

        assert result['updated'] is True
        assert (service['root'] / 'SKILL.md').read_text() == '# v2'
        assert service['session'].rows[ads_skill.VERSION_KEY].value == 'v2'

    async def test_the_same_version_downloads_nothing(self, service):
        service['root'].mkdir(parents=True)
        (service['root'] / 'SKILL.md').write_text('# v2')
        service['session'] = FakeSession('v2')

        result = await ads_skill.refresh_if_bound()

        assert result == {'updated': False, 'version': 'v2'}
        assert service['calls'] == ['/skill/version']

    async def test_no_service_bound_asks_nothing(self, service):
        service['bound'] = False

        assert await ads_skill.refresh_if_bound() is None
        assert service['calls'] == []

    async def test_no_authorized_store_asks_nothing(self, service):
        """Registered, but no store authorized — or the last one revoked:
        the skill must not come back at the next boot or skills sync."""
        service['authorized'] = False

        assert await ads_skill.refresh_if_bound() is None
        assert service['calls'] == []
        assert not (service['root'] / 'SKILL.md').exists()

    async def test_an_unreachable_service_is_reported_not_raised(
        self, service, monkeypatch
    ):
        async def down(*_a, **_kw):
            raise ConnectionError('down')

        monkeypatch.setattr(ads_client, 'call', down)

        result = await ads_skill.refresh_if_bound()

        assert result['updated'] is False and 'down' in result['error']


@pytest.fixture
def sync_mgr(tmp_path):
    mgr = SkillsSyncManager()
    mgr._dest_dir = tmp_path / '.claude' / 'skills'
    mgr._dest_dir.mkdir(parents=True)
    return mgr


def _patched(sync_mgr, *, enabled=True):
    gate = patch(
        'app.workspace.skills_sync._auto_sync_enabled',
        new_callable=AsyncMock,
        return_value=enabled,
    )
    commit = patch.object(
        sync_mgr,
        '_fetch_remote_commit',
        new_callable=AsyncMock,
        return_value=None,
    )
    ads = patch.object(ads_skill, 'refresh_if_bound', new_callable=AsyncMock)
    return gate, commit, ads


class TestTheSkillsSyncChecksTheAdsSkill:
    async def test_before_a_task_even_when_github_is_unreachable(
        self, sync_mgr
    ):
        gate, commit, ads = _patched(sync_mgr)
        with gate, commit, ads as refresh:
            await sync_mgr.check_and_sync_remote()
        refresh.assert_awaited_once()

    async def test_not_when_auto_sync_is_off(self, sync_mgr):
        gate, commit, ads = _patched(sync_mgr, enabled=False)
        with gate, commit, ads as refresh:
            await sync_mgr.check_and_sync_remote()
        refresh.assert_not_called()

    async def test_not_inside_the_cooldown(self, sync_mgr):
        sync_mgr._write_sync_meta({'last_sync_at': '2999-01-01T00:00:00+00:00'})
        gate, commit, ads = _patched(sync_mgr)
        with gate, commit, ads as refresh:
            await sync_mgr.check_and_sync_remote()
        refresh.assert_not_called()

    async def test_the_sync_button_checks_it_and_says_what_happened(
        self, sync_mgr
    ):
        gate, commit, ads = _patched(sync_mgr)
        with (
            gate,
            commit,
            ads as refresh,
            patch.object(
                sync_mgr,
                '_do_remote_sync',
                new_callable=AsyncMock,
                return_value={'status': 'success', 'copied': 0},
            ),
        ):
            refresh.return_value = {'updated': True, 'version': 'v2'}
            result = await sync_mgr.fetch_remote()
        assert result['ads_skill'] == {'updated': True, 'version': 'v2'}
