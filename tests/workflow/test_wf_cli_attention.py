"""Which signed-in platforms need signing in again — the bar at the top.

Through the HTTP API against fake platforms
(``tests/fixtures/fake_cli_integration.py``): a platform counts only
once it has been signed in (enabled); its whole sign-in or — on a
platform that grants permissions one by one, like WeCom — one of its
permissions expired. A permission alerts only once it has worked here;
signing in again re-checks at once.
"""

import os
import sys
import time

import pytest

from app import config
from app.cli_integrations import attention, paths
from app.cli_integrations.base import Permission
from app.cli_integrations.login import login_manager
from app.cli_integrations.registry import INTEGRATIONS
from tests.fixtures.fake_cli_integration import FakeIntegration

pytestmark = [
    pytest.mark.workflow,
    pytest.mark.skipif(sys.platform == 'win32', reason='shebang script CLI'),
]

BASE = '/api/integrations/cli'


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / 'home'
    (h / '.claude' / 'skills').mkdir(parents=True)
    monkeypatch.setattr(config, 'VIBE_SELLER_DIR', h)
    return h


@pytest.fixture
def platforms(tmp_path, monkeypatch, home):
    made = {}
    for key in ('fake-a', 'fake-b', 'fake-c'):
        integ = FakeIntegration(key, tmp_path)
        monkeypatch.setitem(INTEGRATIONS, key, integ)
        made[key] = integ
    return made


@pytest.fixture(autouse=True)
async def _no_login_left():
    yield
    await login_manager.shutdown()
    login_manager._sessions.clear()


class Grants:
    """What the fake platform's permissions answer, settable per test."""

    def __init__(self, **states: str) -> None:
        self.states = dict(states)
        self.checks = 0

    async def __call__(self, run) -> list[Permission]:
        self.checks += 1
        return [
            Permission(
                k, s, f'https://renew.example/{k}' if s == 'expired' else None
            )
            for k, s in self.states.items()
        ]


def per_permission(monkeypatch, fake: FakeIntegration, grants: Grants) -> None:
    monkeypatch.setattr(fake, 'per_permission', True, raising=False)
    monkeypatch.setattr(fake, 'permissions', grants, raising=False)


async def _items(client) -> dict[str, list[str]]:
    r = await client.get(f'{BASE}/attention')
    assert r.status_code == 200, r.text
    return {i['key']: i['expired'] for i in r.json()['items']}


async def _enable(client, fake: FakeIntegration) -> None:
    fake.sign_in()
    assert (await client.post(f'{BASE}/{fake.key}/install')).status_code == 200
    assert (await client.post(f'{BASE}/{fake.key}/enable')).status_code == 200


async def test_only_a_platform_once_signed_in_alerts(
    admin_client, member_client, platforms
):
    """Enabled and its sign-in gone: alert. Downloaded and signed in but
    never enabled, or downloaded and never signed in: no alert."""
    a, b = platforms['fake-a'], platforms['fake-b']
    await _enable(admin_client, a)
    b.sign_in()
    await admin_client.post(f'{BASE}/fake-b/install')
    await admin_client.post(f'{BASE}/fake-c/install')  # never signed in

    assert 'fake-a' not in await _items(admin_client)

    for f in (a, b):
        (f.state / 'token').unlink()  # the sign-in expired
    items = await _items(admin_client)
    assert items.get('fake-a') == []  # the whole sign-in, no permission list
    assert 'fake-b' not in items and 'fake-c' not in items

    # Only admins can sign a platform in again: nobody else is told to.
    assert await _items(member_client) == {}


async def test_permission_alerts_once_it_worked_here_and_then_expired(
    admin_client, platforms, monkeypatch
):
    fake = platforms['fake-a']
    grants = Grants(doc='ok', mail='expired')
    per_permission(monkeypatch, fake, grants)
    await _enable(admin_client, fake)

    r = await admin_client.get(f'{BASE}/fake-a/permissions')
    assert r.json()['permissions'] == [
        {'key': 'doc', 'state': 'ok', 'renew_url': None},
        {
            'key': 'mail',
            'state': 'expired',
            'renew_url': 'https://renew.example/mail',
        },
    ]
    # Mail never worked here: the admin never granted it — no alert.
    assert 'fake-a' not in await _items(admin_client)

    grants.states['doc'] = 'expired'
    # The Settings card checks afresh; the bar reads what it found.
    await admin_client.get(f'{BASE}/fake-a/permissions')
    assert (await _items(admin_client))['fake-a'] == ['doc']


async def test_the_bar_rechecks_a_stale_answer_on_its_own(
    admin_client, platforms, monkeypatch
):
    fake = platforms['fake-a']
    grants = Grants(doc='ok')
    per_permission(monkeypatch, fake, grants)
    await _enable(admin_client, fake)
    assert 'fake-a' not in await _items(admin_client)  # first check: works
    checks = grants.checks

    grants.states['doc'] = 'expired'
    # Within RECHECK_S the bar trusts its last check: no new calls.
    assert 'fake-a' not in await _items(admin_client)
    assert grants.checks == checks

    monkeypatch.setattr(attention, 'RECHECK_S', 0)
    assert (await _items(admin_client))['fake-a'] == ['doc']
    assert grants.checks == checks + 1


async def test_signing_in_again_rechecks_at_once(
    admin_client, platforms, monkeypatch, home
):
    """A new scan renews the grants: the bar must not keep saying
    "expired" for the rest of RECHECK_S."""
    fake = platforms['fake-a']
    grants = Grants(doc='ok')
    per_permission(monkeypatch, fake, grants)
    await _enable(admin_client, fake)
    await _items(admin_client)
    grants.states['doc'] = 'expired'
    await admin_client.get(f'{BASE}/fake-a/permissions')
    assert (await _items(admin_client))['fake-a'] == ['doc']

    # Renewed by signing in again: the stored sign-in changes.
    grants.states['doc'] = 'ok'
    cred = paths.state_dir('fake-a', home) / 'credentials'
    cred.parent.mkdir(parents=True, exist_ok=True)
    cred.write_text('renewed')
    later = time.time() + 5
    os.utime(cred, (later, later))
    assert 'fake-a' not in await _items(admin_client)


async def test_nothing_to_check_for_a_platform_without_permissions(
    admin_client, platforms, monkeypatch
):
    await _enable(admin_client, platforms['fake-a'])
    r = await admin_client.get(f'{BASE}/fake-a/permissions')
    assert r.json() == {'permissions': None}

    fake = platforms['fake-b']
    per_permission(monkeypatch, fake, Grants(doc='ok'))
    await admin_client.post(f'{BASE}/fake-b/install')  # never signed in
    r = await admin_client.get(f'{BASE}/fake-b/permissions')
    assert r.json() == {'permissions': None}

    card = {
        c['key']: c
        for c in (await admin_client.get(BASE)).json()['integrations']
    }
    assert card['fake-b']['per_permission'] is True
    assert card['fake-a']['per_permission'] is False
    assert card['wecom']['per_permission'] is True
