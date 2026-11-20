"""Settings → Integrations → workplace CLIs, through the HTTP API.

The page's journey, end to end against a fake platform
(``tests/fixtures/fake_cli_integration.py``) whose "release" is served
from disk: Connect (install → login with link/code or QR → enabled on
success), the agent PATH / skills it yields, disable, the daily update
of every connected platform (sync job ``workplace_clis``), and the
gates — admin only, never enabled while signed out, a tampered download
never installed.
"""

import asyncio
import os
import sys

import pytest

from app import config
from app.ai.claude_backend_utils import apply_agent_venv_path
from app.cli_integrations import paths
from app.cli_integrations.login import login_manager
from app.cli_integrations.registry import INTEGRATIONS
from app.sync import jobs as sync_jobs
from app.sync.scheduler import sync_scheduler
from tests.fixtures import fake_cli_integration as fake_cli
from tests.fixtures.fake_cli_integration import FakeIntegration

pytestmark = [
    pytest.mark.workflow,
    pytest.mark.skipif(sys.platform == 'win32', reason='shebang script CLI'),
]

BASE = '/api/integrations/cli'


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Point the framework's runtime home at a throwaway dir."""
    h = tmp_path / 'home'
    (h / '.claude' / 'skills').mkdir(parents=True)
    monkeypatch.setattr(config, 'VIBE_SELLER_DIR', h)
    return h


@pytest.fixture
def platforms(tmp_path, monkeypatch, home):
    """Three fake platforms registered next to the real ones."""
    made = {}
    for key in ('fake-a', 'fake-b', 'fake-c'):
        integ = FakeIntegration(key, tmp_path)
        monkeypatch.setitem(INTEGRATIONS, key, integ)
        made[key] = integ
    yield made
    asyncio.get_event_loop  # noqa: B018 — keep the import used


@pytest.fixture(autouse=True)
async def _no_login_left():
    yield
    await login_manager.shutdown()
    login_manager._sessions.clear()


async def _card(client, key):
    r = await client.get(BASE)
    assert r.status_code == 200, r.text
    return {c['key']: c for c in r.json()['integrations']}[key]


async def _wait_login(client, key, state, timeout=15.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        view = (await client.get(f'{BASE}/{key}/login')).json()
        if view['state'] == state or (
            state == 'waiting' and view['state'] == 'waiting'
        ):
            return view
        if view['state'] in ('failed', 'cancelled') and view['state'] != state:
            raise AssertionError(view)
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f'{key} login never reached {state}: {view}')
        await asyncio.sleep(0.05)


async def _wait_enabled(client, key, timeout=15.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not (await _card(client, key))['enabled']:
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f'{key} never enabled')
        await asyncio.sleep(0.05)


async def test_list_shows_the_three_real_platforms(admin_client, home):
    r = await admin_client.get(BASE)
    cards = {c['key']: c for c in r.json()['integrations']}
    assert {'wecom', 'feishu', 'dingtalk'} <= set(cards)
    for key in ('wecom', 'feishu', 'dingtalk'):
        assert cards[key]['installed'] is False
        assert cards[key]['enabled'] is False
        assert cards[key]['auth'] is None


async def test_connect_journey_device_code(admin_client, platforms, home):
    """Connect = install, two-step login (create app, authorize), then
    enabled by the server itself — the page only watches."""
    fake = platforms['fake-a']
    r = await admin_client.post(f'{BASE}/fake-a/install')
    assert r.status_code == 200, r.text
    assert r.json()['version'] == '1.2.3'
    card = await _card(admin_client, 'fake-a')
    assert card['installed'] and card['version'] == '1.2.3'
    assert card['auth']['reason'] == 'not_configured'
    assert not card['enabled']

    # Signed out: enabling is refused — an agent must never be the one
    # to log in.
    r = await admin_client.post(f'{BASE}/fake-a/enable')
    assert r.status_code == 400
    assert 'not signed in' in r.json()['detail']

    r = await admin_client.post(
        f'{BASE}/fake-a/login', json={'enable_after': True}
    )
    assert r.status_code == 200, r.text
    view = await _wait_login(admin_client, 'fake-a', 'waiting')
    assert view['url'] == 'https://fake.example/setup?app=APP1'
    assert (view['step'], view['steps']) == (1, 2)
    # Everyone may list, but the link itself is admin-only and the list
    # does not carry it.
    listed = (await _card(admin_client, 'fake-a'))['login']
    assert set(listed) == {'state', 'step', 'steps'}

    fake.approve()
    view = await _wait_login(admin_client, 'fake-a', 'waiting')
    while view['step'] != 2 or not view['user_code']:
        view = await _wait_login(admin_client, 'fake-a', 'waiting')
    assert view['user_code'] == 'WXYZ-1234'
    fake.approve()
    await _wait_login(admin_client, 'fake-a', 'succeeded')
    await _wait_enabled(admin_client, 'fake-a')

    card = await _card(admin_client, 'fake-a')
    assert card['auth'] == {
        'usable': True,
        'reason': 'logged_in',
        'identity': 'Alice',
        'detail': {},
    }
    umbrella = paths.skill_dir('fake-a', home) / 'SKILL.md'
    assert umbrella.is_file()
    assert (paths.skill_dir('fake-a', home) / 'calendar' / 'SKILL.md').is_file()


async def test_qr_login_serves_the_image_to_admins_only(
    admin_client, member_client, platforms, home
):
    fake = platforms['fake-b']
    fake.qr = True
    fake.sign_in()
    (fake.state / 'token').unlink()  # configured, not logged in
    assert (
        await admin_client.post(f'{BASE}/fake-b/install')
    ).status_code == 200
    r = await admin_client.post(f'{BASE}/fake-b/login', json={})
    assert r.status_code == 200
    view = await _wait_login(admin_client, 'fake-b', 'waiting')
    while not view['qr']:
        view = await _wait_login(admin_client, 'fake-b', 'waiting')
    assert view['hint'] == 'scan'
    qr = await admin_client.get(f'{BASE}/fake-b/login/qr')
    assert qr.status_code == 200
    assert qr.headers['content-type'] == 'image/png'
    assert qr.content.startswith(b'\x89PNG')
    assert (
        await member_client.get(f'{BASE}/fake-b/login/qr')
    ).status_code == 403
    assert (await member_client.get(f'{BASE}/fake-b/login')).status_code == 403
    fake.approve()
    await _wait_login(admin_client, 'fake-b', 'succeeded')
    # No enable_after: signed in, but not given to agents.
    card = await _card(admin_client, 'fake-b')
    assert card['auth']['usable'] and not card['enabled']


async def test_members_can_look_but_not_touch(member_client, platforms, home):
    assert (await member_client.get(BASE)).status_code == 200
    for method, path in (
        ('post', 'install'),
        ('post', 'login'),
        ('delete', 'login'),
        ('post', 'enable'),
        ('post', 'disable'),
    ):
        r = await getattr(member_client, method)(f'{BASE}/fake-a/{path}')
        assert r.status_code == 403, (method, path)
    assert not paths.tool_dir('fake-a', home).exists()


async def test_only_enabled_platforms_reach_the_agent(
    admin_client, platforms, home, tmp_path
):
    """Enable one, then two, then disable one: the agent's PATH and its
    skills follow exactly — nothing loads for a platform not enabled."""
    for key, fake in platforms.items():
        fake.sign_in(user=f'user-{key}')
        assert (
            await admin_client.post(f'{BASE}/{key}/install')
        ).status_code == 200

    def agent_view():
        env = {'PATH': '/usr/bin'}
        apply_agent_venv_path(env, None, vibe_home=home, server_bin=tmp_path)
        on_path = {
            k
            for k in platforms
            if str(paths.bin_dir(k, home)) in env['PATH'].split(':')
        }
        skills = {
            p.name
            for p in paths.skills_root(home).iterdir()
            if (p / 'SKILL.md').is_file()
        }
        return on_path, skills & set(platforms), env.get('FAKE_CLI_SWITCH')

    assert agent_view() == (set(), set(), None)

    assert (await admin_client.post(f'{BASE}/fake-a/enable')).status_code == 200
    assert agent_view() == ({'fake-a'}, {'fake-a'}, 'fake-a')

    assert (await admin_client.post(f'{BASE}/fake-c/enable')).status_code == 200
    on_path, skills, _ = agent_view()
    assert on_path == skills == {'fake-a', 'fake-c'}

    r = await admin_client.post(f'{BASE}/fake-a/disable')
    assert r.status_code == 200 and r.json()['removed'] is True
    on_path, skills, switch = agent_view()
    assert on_path == skills == {'fake-c'}
    assert switch == 'fake-c'
    # Disabling keeps the binary and the login for a quick re-enable.
    card = await _card(admin_client, 'fake-a')
    assert card['installed'] and card['auth']['usable'] and not card['enabled']


async def test_update_refreshes_the_skills_of_an_enabled_platform(
    admin_client, platforms, home
):
    fake = platforms['fake-a']
    fake.sign_in()
    await admin_client.post(f'{BASE}/fake-a/install')
    await admin_client.post(f'{BASE}/fake-a/enable')
    fake.publish('2.0.0')
    r = await admin_client.post(f'{BASE}/fake-a/install')
    assert r.json()['version'] == '2.0.0'
    root = paths.skill_dir('fake-a', home)
    assert 'version 2.0.0' in (root / 'calendar' / 'SKILL.md').read_text(
        encoding='utf-8'
    )
    assert '`fakecli` 2.0.0' in (root / 'SKILL.md').read_text(encoding='utf-8')
    # A task that started on 1.2.3 still finds it: the previous version
    # stays; only older ones go.
    versions = paths.versions_dir('fake-a', home)
    assert sorted(p.name for p in versions.iterdir()) == ['1.2.3', '2.0.0']
    fake.publish('3.0.0')
    await admin_client.post(f'{BASE}/fake-a/install')
    assert sorted(p.name for p in versions.iterdir()) == ['2.0.0', '3.0.0']
    env = {'PATH': '/usr/bin'}
    apply_agent_venv_path(env, None, vibe_home=home, server_bin=home)
    assert str(versions / '3.0.0') in env['PATH'].split(':')
    assert str(versions / '2.0.0') not in env['PATH'].split(':')


async def test_tampered_download_is_never_installed(
    admin_client, platforms, home
):
    fake = platforms['fake-a']
    fake.bad_checksum = True
    r = await admin_client.post(f'{BASE}/fake-a/install')
    assert r.status_code == 502
    assert 'sha256' in r.json()['detail']
    assert paths.read_manifest('fake-a', home) is None
    assert not paths.versions_dir('fake-a', home).exists()
    assert not paths.is_installed('fake-a', 'fakecli', home)


async def test_install_never_moves_a_dir_the_cli_has_run_in(
    admin_client, platforms, home, monkeypatch
):
    """Windows refuses to rename a dir while a file in it is open, and
    the antivirus holds a CLI that just ran (and what it unpacked next
    to itself: ``dws``) open for seconds after. Here a dir holding
    such a file cannot be moved either — as on Windows — and the
    install, then an update, must still land."""
    real_rename, real_replace = os.rename, os.replace

    def windows_like(move):
        def refuse(src, dst, *a, **kw):
            if os.path.isfile(os.path.join(src, fake_cli.RUNTIME)):
                raise PermissionError(13, 'Access is denied', str(src))
            return move(src, dst, *a, **kw)

        return refuse

    monkeypatch.setattr(os, 'rename', windows_like(real_rename))
    monkeypatch.setattr(os, 'replace', windows_like(real_replace))
    fake = platforms['fake-a']
    r = await admin_client.post(f'{BASE}/fake-a/install')
    assert r.status_code == 200, r.text
    assert paths.is_installed('fake-a', 'fakecli', home)
    fake.publish('2.0.0')
    r = await admin_client.post(f'{BASE}/fake-a/install')
    assert r.status_code == 200, r.text
    assert paths.read_manifest('fake-a', home)['version'] == '2.0.0'
    versions = paths.versions_dir('fake-a', home)
    assert sorted(p.name for p in versions.iterdir()) == ['1.2.3', '2.0.0']


async def test_a_build_that_does_not_run_leaves_nothing_behind(
    admin_client, platforms, home
):
    fake = platforms['fake-a']
    await admin_client.post(f'{BASE}/fake-a/install')
    fake.publish_broken('2.0.0')
    r = await admin_client.post(f'{BASE}/fake-a/install')
    assert r.status_code == 400 and 'exited 9' in r.json()['detail']
    assert paths.read_manifest('fake-a', home)['version'] == '1.2.3'
    versions = paths.versions_dir('fake-a', home)
    assert sorted(p.name for p in versions.iterdir()) == ['1.2.3']


async def test_login_cancel_and_already_signed_in(
    admin_client, platforms, home
):
    fake = platforms['fake-a']
    await admin_client.post(f'{BASE}/fake-a/install')
    await admin_client.post(f'{BASE}/fake-a/login', json={})
    await _wait_login(admin_client, 'fake-a', 'waiting')
    r = await admin_client.delete(f'{BASE}/fake-a/login')
    assert r.json() == {'cancelled': True}
    assert (await admin_client.get(f'{BASE}/fake-a/login')).json()['state'] == (
        'cancelled'
    )
    # Already signed in: "login" has nothing to run, and enable_after
    # still finishes the job.
    fake.sign_in()
    r = await admin_client.post(
        f'{BASE}/fake-a/login', json={'enable_after': True}
    )
    assert r.status_code == 200
    await _wait_enabled(admin_client, 'fake-a')


async def test_unknown_platform_is_404(admin_client, home):
    assert (await admin_client.post(f'{BASE}/nope/install')).status_code == 404


async def test_sign_in_with_withheld_scopes_still_enables(
    admin_client, platforms, home
):
    """lark-cli signs in and exits non-zero when the org admin withheld a
    scope. Signed in is signed in: the platform gets enabled, and the
    view carries a readable line, not the CLI's JSON."""
    fake = platforms['fake-a']
    fake.login_exit = 3
    fake.long_line = True
    fake.sign_in()
    (fake.state / 'token').unlink()
    await admin_client.post(f'{BASE}/fake-a/install')
    r = await admin_client.post(
        f'{BASE}/fake-a/login', json={'enable_after': True}
    )
    assert r.status_code == 200
    await _wait_login(admin_client, 'fake-a', 'waiting')
    fake.approve()
    view = await _wait_login(admin_client, 'fake-a', 'succeeded')
    assert view['message'] == 'logged in'
    await _wait_enabled(admin_client, 'fake-a')


# ── the daily update (sync job ``workplace_clis``) ─────────────────


@pytest.fixture
async def update_job():
    """The real job on the process-wide scheduler, run the way the page
    and the daily tick reach it: ``POST /api/sync/workplace_clis/run``."""
    sync_scheduler.reset()
    job = next(
        j
        for j in sync_jobs.core_jobs()
        if j.name == sync_jobs.WORKPLACE_CLIS_JOB
    )
    await sync_scheduler.register(job)
    yield
    sync_scheduler.reset()


async def _daily_update(client) -> dict:
    r = await client.post(f'/api/sync/{sync_jobs.WORKPLACE_CLIS_JOB}/run')
    assert r.status_code == 200, r.text
    return r.json()


async def _connect(client, fake, *, enable: bool) -> None:
    fake.sign_in()
    assert (await client.post(f'{BASE}/{fake.key}/install')).status_code == 200
    if enable:
        assert (
            await client.post(f'{BASE}/{fake.key}/enable')
        ).status_code == 200


def _calendar_skill(home, key) -> str:
    return (paths.skill_dir(key, home) / 'calendar' / 'SKILL.md').read_text(
        encoding='utf-8'
    )


async def test_daily_update_moves_only_the_connected_platforms(
    admin_client, platforms, home, update_job
):
    a, b, c = platforms['fake-a'], platforms['fake-b'], platforms['fake-c']
    await _connect(admin_client, a, enable=True)
    await _connect(admin_client, b, enable=False)
    for fake in (a, b, c):
        fake.publish('2.0.0')

    run = await _daily_update(admin_client)
    assert run['status'] == 'updated' and run['error'] is None
    # Enabled: the new binary AND the skills of that same version.
    assert paths.read_manifest('fake-a', home)['version'] == '2.0.0'
    assert paths.skills_version('fake-a', home) == '2.0.0'
    assert 'version 2.0.0' in _calendar_skill(home, 'fake-a')
    assert (await _card(admin_client, 'fake-a'))['enabled']
    # Installed but not enabled: new binary, still no skills for agents.
    assert paths.read_manifest('fake-b', home)['version'] == '2.0.0'
    assert not paths.skill_dir('fake-b', home).exists()
    # Never connected: never downloaded.
    assert paths.read_manifest('fake-c', home) is None
    assert not paths.versions_dir('fake-c', home).exists()
    # The version a running task started on is still there.
    versions = sorted(
        p.name for p in paths.versions_dir('fake-a', home).iterdir()
    )
    assert versions == ['1.2.3', '2.0.0']

    # Current already: nothing is downloaded (the release is gone, so a
    # download would fail the run).
    a.release.unlink()
    b.release.unlink()
    run = await _daily_update(admin_client)
    assert run['status'] == 'unchanged' and run['error'] is None


async def test_daily_update_asks_no_vendor_when_nothing_is_connected(
    admin_client, platforms, home, update_job
):
    def _never():
        raise AssertionError('asked a vendor with nothing installed')

    for fake in platforms.values():
        fake.latest_version = _never
    run = await _daily_update(admin_client)
    assert run['status'] == 'skipped'
    assert run['skip_reason'] == 'precondition'


async def test_a_failed_update_keeps_the_running_version(
    admin_client, platforms, home, update_job
):
    a, b = platforms['fake-a'], platforms['fake-b']
    await _connect(admin_client, a, enable=True)
    await _connect(admin_client, b, enable=True)
    a.publish('2.0.0')
    a.bad_checksum = True  # a tampered (or broken) release
    b.publish('2.0.0')

    run = await _daily_update(admin_client)
    # One vendor's bad release holds back neither the other platform
    # nor the run; it is reported.
    assert run['status'] == 'updated'
    assert 'fake-a' in run['error'] and 'sha256' in run['error']
    assert paths.read_manifest('fake-b', home)['version'] == '2.0.0'
    # fake-a keeps running — and documenting — what it had.
    assert paths.read_manifest('fake-a', home)['version'] == '1.2.3'
    assert paths.is_enabled('fake-a', 'fakecli', home)
    assert paths.skills_version('fake-a', home) == '1.2.3'
    env = {'PATH': '/usr/bin'}
    apply_agent_venv_path(env, None, vibe_home=home, server_bin=home)
    assert str(paths.version_dir('fake-a', '1.2.3', home)) in env['PATH'].split(
        ':'
    )


async def test_skills_left_behind_by_a_failed_run_catch_up_next_run(
    admin_client, platforms, home, update_job
):
    """The binary moved but its skills download failed: the agent would
    read docs of the old version until the NEXT release. The next run
    sees the umbrella documents another version and brings it level."""
    a = platforms['fake-a']
    await _connect(admin_client, a, enable=True)
    a.publish('2.0.0')
    a.skills.unlink()  # the skills host is down this time

    run = await _daily_update(admin_client)
    assert run['status'] == 'failed' and 'fake-a' in run['error']
    assert paths.read_manifest('fake-a', home)['version'] == '2.0.0'
    assert paths.skills_version('fake-a', home) == '1.2.3'

    a.publish('2.0.0')  # the host is back
    run = await _daily_update(admin_client)
    assert run['status'] == 'updated' and run['error'] is None
    assert run['detail'] == {'fake-a': '2.0.0 -> 2.0.0 +skills'}
    assert paths.skills_version('fake-a', home) == '2.0.0'
    assert 'version 2.0.0' in _calendar_skill(home, 'fake-a')
