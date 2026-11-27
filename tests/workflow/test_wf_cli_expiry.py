"""The top bar warns only about an enabled platform whose sign-in expired.

A platform that was merely downloaded, or whose sign-in was never
finished, is silent. The WeCom-style online check has one answer:
expired or not; no individual capability is reported.
"""

import os
import sys
import time

import pytest

from app import config
from app.cli_integrations import attention, paths
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


class Expiry:
    def __init__(self, value: bool | None = False) -> None:
        self.value = value
        self.checks = 0

    async def __call__(self, run) -> bool | None:
        self.checks += 1
        return self.value


def checks_expiry(monkeypatch, fake: FakeIntegration, check: Expiry) -> None:
    monkeypatch.setattr(fake, 'checks_expiry', True, raising=False)
    monkeypatch.setattr(fake, 'check_expired', check, raising=False)


async def _items(client) -> set[str]:
    r = await client.get(f'{BASE}/attention')
    assert r.status_code == 200, r.text
    return {i['key'] for i in r.json()['items']}


async def _enable(client, fake: FakeIntegration) -> None:
    fake.sign_in()
    assert (await client.post(f'{BASE}/{fake.key}/install')).status_code == 200
    assert (await client.post(f'{BASE}/{fake.key}/enable')).status_code == 200


async def test_only_a_platform_once_signed_in_alerts(
    admin_client, member_client, platforms
):
    a, b = platforms['fake-a'], platforms['fake-b']
    await _enable(admin_client, a)
    b.sign_in()
    await admin_client.post(f'{BASE}/fake-b/install')  # signed in, not enabled
    await admin_client.post(f'{BASE}/fake-c/install')  # never signed in

    assert 'fake-a' not in await _items(admin_client)
    for fake in (a, b):
        (fake.state / 'token').unlink()

    assert await _items(admin_client) == {'fake-a'}
    assert await _items(member_client) == set()


async def test_expiry_check_has_one_answer_and_refreshes_the_bar(
    admin_client, platforms, monkeypatch
):
    fake = platforms['fake-a']
    check = Expiry(False)
    checks_expiry(monkeypatch, fake, check)
    await _enable(admin_client, fake)

    r = await admin_client.get(f'{BASE}/fake-a/expiry')
    assert r.json() == {'expired': False}
    assert 'fake-a' not in await _items(admin_client)

    check.value = True
    assert (await admin_client.get(f'{BASE}/fake-a/expiry')).json() == {
        'expired': True
    }
    assert await _items(admin_client) == {'fake-a'}


async def test_the_bar_rechecks_a_stale_answer_on_its_own(
    admin_client, platforms, monkeypatch
):
    fake = platforms['fake-a']
    check = Expiry(False)
    checks_expiry(monkeypatch, fake, check)
    await _enable(admin_client, fake)
    assert 'fake-a' not in await _items(admin_client)
    checks = check.checks

    check.value = True
    assert 'fake-a' not in await _items(admin_client)
    assert check.checks == checks

    monkeypatch.setattr(attention, 'RECHECK_S', 0)
    assert await _items(admin_client) == {'fake-a'}
    assert check.checks == checks + 1


async def test_signing_in_again_rechecks_at_once(
    admin_client, platforms, monkeypatch, home
):
    fake = platforms['fake-a']
    check = Expiry(True)
    checks_expiry(monkeypatch, fake, check)
    await _enable(admin_client, fake)
    assert await _items(admin_client) == {'fake-a'}

    check.value = False
    cred = paths.state_dir('fake-a', home) / 'credentials'
    cred.parent.mkdir(parents=True, exist_ok=True)
    cred.write_text('renewed')
    later = time.time() + 5
    os.utime(cred, (later, later))
    assert 'fake-a' not in await _items(admin_client)


async def test_nothing_to_check_for_a_platform_without_online_expiry(
    admin_client, platforms, monkeypatch
):
    await _enable(admin_client, platforms['fake-a'])
    r = await admin_client.get(f'{BASE}/fake-a/expiry')
    assert r.json() == {'expired': None}

    fake = platforms['fake-b']
    checks_expiry(monkeypatch, fake, Expiry(True))
    await admin_client.post(f'{BASE}/fake-b/install')  # never signed in
    r = await admin_client.get(f'{BASE}/fake-b/expiry')
    assert r.json() == {'expired': None}

    cards = {
        c['key']: c
        for c in (await admin_client.get(BASE)).json()['integrations']
    }
    assert cards['fake-b']['checks_expiry'] is True
    assert cards['fake-a']['checks_expiry'] is False
    assert cards['wecom']['checks_expiry'] is True
