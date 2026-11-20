"""Settings → Integrations → Google Workspace, through the HTTP API.

Google Workspace connects like WeCom / Feishu / DingTalk, with two
things of its own: a one-time setup (the sign-in key the person creates
in Google Cloud, uploaded on the page) and a sign-in that comes back as
a redirect to a port on THIS computer — relayed from a pasted address
when the browser is elsewhere. The journey runs against a fake platform
that signs in the same way (``FakeIntegration(listen=True,
setup=True)``), so nothing needs Google.

Also the upgrade from the earlier switch, which used the person's own
``gws``: it is retired once at boot, and the row says why until Google
Workspace is connected again.
"""

import asyncio
import sys

import pytest
from sqlalchemy import select

from app import config
from app.cli_integrations import gws_legacy, paths
from app.cli_integrations.login import login_manager
from app.cli_integrations.registry import INTEGRATIONS
from app.models.app_settings import AppSettings
from tests.fixtures.fake_cli_integration import FakeIntegration

pytestmark = [
    pytest.mark.workflow,
    pytest.mark.skipif(sys.platform == 'win32', reason='shebang script CLI'),
]

BASE = '/api/integrations/cli'
KEY = 'fake-g'


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / 'home'
    (h / '.claude' / 'skills').mkdir(parents=True)
    monkeypatch.setattr(config, 'VIBE_SELLER_DIR', h)
    return h


@pytest.fixture
def google_like(tmp_path, monkeypatch, home) -> FakeIntegration:
    integ = FakeIntegration(KEY, tmp_path, listen=True, setup=True)
    monkeypatch.setitem(INTEGRATIONS, KEY, integ)
    return integ


@pytest.fixture(autouse=True)
async def _no_login_left():
    yield
    await login_manager.shutdown()
    login_manager._sessions.clear()


async def _card(client, key=KEY):
    r = await client.get(BASE)
    assert r.status_code == 200, r.text
    return {c['key']: c for c in r.json()['integrations']}[key]


async def _until(fetch, check, what, timeout=15.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        value = await fetch()
        if check(value):
            return value
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f'{what}: {value}')
        await asyncio.sleep(0.05)


async def _view(client):
    return (await client.get(f'{BASE}/{KEY}/login')).json()


async def test_google_workspace_is_listed_with_its_setup(admin_client, home):
    card = await _card(admin_client, 'gws')
    assert card['title'] == 'Google Workspace'
    assert card['binary'] == 'gws'
    assert card['setup'] == 'google_client'
    assert card['installed'] is False and card['enabled'] is False
    # Nothing to set up before the download; nothing said either.
    assert card['needs_setup'] is False
    assert card['notice'] is None


async def test_connect_with_setup_and_a_sign_in_from_another_computer(
    admin_client, google_like, home
):
    r = await admin_client.post(f'{BASE}/{KEY}/install')
    assert r.status_code == 200, r.text
    card = await _card(admin_client)
    assert card['needs_setup'] is True
    assert card['auth']['reason'] == 'not_configured'

    # The sign-in cannot start before the key is there — the page says
    # which step comes first instead of a CLI error.
    r = await admin_client.post(
        f'{BASE}/{KEY}/login', json={'enable_after': True}
    )
    assert r.status_code == 400
    assert r.json()['detail']['code'] == 'needs_setup'

    r = await admin_client.post(f'{BASE}/{KEY}/setup', json={'payload': 'nope'})
    assert r.status_code == 400
    assert r.json()['detail']['code'] == 'fake_key_invalid'
    assert (await _card(admin_client))['needs_setup'] is True

    r = await admin_client.post(
        f'{BASE}/{KEY}/setup', json={'payload': 'good-key'}
    )
    assert r.status_code == 200, r.text
    card = await _card(admin_client)
    assert card['needs_setup'] is False
    assert card['auth']['reason'] == 'not_logged_in'

    r = await admin_client.post(
        f'{BASE}/{KEY}/login', json={'enable_after': True}
    )
    assert r.status_code == 200, r.text
    view = await _until(
        lambda: _view(admin_client),
        lambda v: v['callback'],
        'sign-in link never shown',
    )
    assert view['state'] == 'waiting'
    assert view['paste_back'] is True
    assert view['url'].startswith('https://accounts.fake.example/')

    # An address that is not this sign-in's redirect is refused, and
    # the sign-in keeps waiting for the right one.
    r = await admin_client.post(
        f'{BASE}/{KEY}/login/paste',
        json={'address': 'http://evil.example/?code=x'},
    )
    assert r.status_code == 400
    assert r.json()['detail']['code'] == 'bad_address'
    assert (await _view(admin_client))['state'] == 'waiting'

    r = await admin_client.post(
        f'{BASE}/{KEY}/login/paste',
        json={'address': f'{view["callback"]}/?code=4/abc&scope=x'},
    )
    assert r.status_code == 200, r.text
    await _until(
        lambda: _view(admin_client),
        lambda v: v['state'] == 'succeeded' and not v['finishing'],
        'sign-in never finished',
    )
    card = await _card(admin_client)
    assert card['enabled'] is True
    assert card['auth']['identity'] == 'Alice'
    assert (paths.skill_dir(KEY, home) / 'SKILL.md').is_file()


async def test_paste_without_a_waiting_sign_in(admin_client, google_like):
    r = await admin_client.post(
        f'{BASE}/{KEY}/login/paste',
        json={'address': 'http://localhost:1/?code=x'},
    )
    assert r.status_code == 400
    assert r.json()['detail']['code'] == 'no_login'


async def test_setup_of_a_platform_without_one_is_refused(
    admin_client, home, monkeypatch, tmp_path
):
    plain = FakeIntegration('fake-plain', tmp_path)
    monkeypatch.setitem(INTEGRATIONS, 'fake-plain', plain)
    await admin_client.post(f'{BASE}/fake-plain/install')
    r = await admin_client.post(
        f'{BASE}/fake-plain/setup', json={'payload': 'good-key'}
    )
    assert r.status_code == 400
    assert r.json()['detail']['code'] == 'unsupported'


async def test_setup_before_the_download_says_connect_first(
    admin_client, google_like, home
):
    r = await admin_client.post(
        f'{BASE}/{KEY}/setup', json={'payload': 'good-key'}
    )
    assert r.status_code == 400
    assert r.json()['detail']['code'] == 'not_installed'


async def test_members_cannot_set_up_or_relay(member_client, google_like):
    for path, body in (
        ('setup', {'payload': 'good-key'}),
        ('login/paste', {'address': 'http://localhost:1/?code=x'}),
    ):
        r = await member_client.post(f'{BASE}/{KEY}/{path}', json=body)
        assert r.status_code == 403, path
    assert not (google_like.state / 'config').exists()


# ── the earlier switch ─────────────────────────────────


def _legacy_umbrella(home):
    umbrella = paths.skill_dir('gws', home)
    (umbrella / 'sheets').mkdir(parents=True)
    (umbrella / 'SKILL.md').write_text('---\nname: gws\n---\nold switch\n')
    (umbrella / 'sheets' / 'SKILL.md').write_text('old\n')
    return umbrella


async def _legacy_row(db_maker):
    async with db_maker() as db:
        return (
            await db.execute(
                select(AppSettings).where(
                    AppSettings.key == gws_legacy.LEGACY_SETTING
                )
            )
        ).scalar_one_or_none()


async def test_the_earlier_switch_is_retired_and_the_row_says_why(
    admin_client, override_async_session, home
):
    """An install that had the old switch on: its umbrella documented
    the person's own gws, which the agent no longer reaches. It goes,
    the setting goes, and the row says to connect once more."""
    umbrella = _legacy_umbrella(home)
    async with override_async_session() as db:
        db.add(AppSettings(key=gws_legacy.LEGACY_SETTING, value='true'))
        await db.commit()

    async with override_async_session() as db:
        assert await gws_legacy.migrate(db, home) is True

    assert not umbrella.exists()
    assert await _legacy_row(override_async_session) is None
    card = await _card(admin_client, 'gws')
    assert card['notice'] == 'gws_moved'

    # Once: a second boot finds nothing to retire and keeps the notice.
    async with override_async_session() as db:
        assert await gws_legacy.migrate(db, home) is False
    assert (await _card(admin_client, 'gws'))['notice'] == 'gws_moved'


async def test_a_switch_that_was_off_leaves_no_notice(
    admin_client, override_async_session, home
):
    async with override_async_session() as db:
        db.add(AppSettings(key=gws_legacy.LEGACY_SETTING, value='false'))
        await db.commit()
    async with override_async_session() as db:
        assert await gws_legacy.migrate(db, home) is False
    assert await _legacy_row(override_async_session) is None
    assert (await _card(admin_client, 'gws'))['notice'] is None


async def test_the_managed_umbrella_is_never_taken_for_the_old_one(
    override_async_session, home
):
    umbrella = _legacy_umbrella(home)
    (umbrella / paths.SKILLS_VERSION_FILE).write_text('0.22.5')
    async with override_async_session() as db:
        assert await gws_legacy.migrate(db, home) is False
    assert (umbrella / 'SKILL.md').is_file()


async def test_the_notice_goes_once_the_platform_is_enabled(
    admin_client, google_like, home
):
    paths.write_notice(KEY, 'gws_moved', home)
    await admin_client.post(f'{BASE}/{KEY}/install')
    assert (await _card(admin_client))['notice'] == 'gws_moved'
    google_like.sign_in()
    r = await admin_client.post(f'{BASE}/{KEY}/enable')
    assert r.status_code == 200, r.text
    await admin_client.post(f'{BASE}/{KEY}/disable')
    assert (await _card(admin_client))['notice'] is None
