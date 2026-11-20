"""E2E: workplace CLIs (WeCom / Feishu / DingTalk) on a real server.

Three legs, from cheapest to the one that needs a person:

* ``test_connect_installs_the_real_cli`` — the API the page calls
  downloads the REAL official CLI for this OS into the server's home and
  reports it installed but not signed in. No model.
* ``test_login_starts_and_cancels_cleanly`` — through the API, each real
  sign-in starts and a cancel ends it promptly with no CLI process left
  (on Windows too: the installer job runs this module). No model.
* ``test_ui_connect_starts_each_sign_in`` — Settings → Integrations →
  Connect, in a real browser: each platform's real sign-in starts and
  the page shows what the person must do (WeCom: QR + scan link; Feishu:
  the app-setup link, step 1/2; DingTalk: a link + code), then Cancel.
  Nothing is approved. No model.
* ``test_agent_sees_exactly_the_enabled_clis`` — one, two, then all
  three platforms enabled: a real agent finds exactly those CLIs on its
  PATH (each answers ``--version``) and exactly those umbrella skills.
  Enabling needs a signed-in CLI, which CI cannot have, so the
  precondition is laid down the way ``manager.enable`` lays it (the
  real skills of the installed version, ``skills.install``) — the
  agent side is what is under test. Spends model credits.

Signed-in runs (a person scans / approves) live in
``test_workplace_cli_live.py``.

**This module owns its server**: enabling a platform puts a skill into
every task workspace and a CLI on every agent's PATH.
"""

import json
import os
from pathlib import Path
import re
import tempfile
import time

from playwright.sync_api import expect
import psutil
import pytest

from app.cli_integrations import manager, paths, skills
from app.cli_integrations.registry import INTEGRATIONS
from tests.e2e import e2e_helpers
from tests.e2e.own_server import owned_agent_stack

pytestmark = [pytest.mark.e2e]

KEYS = ('wecom', 'feishu', 'dingtalk')
SHOT_DIR = Path(
    os.environ.get('E2E_CLI_SHOT_DIR')
    or Path(tempfile.gettempdir()) / 'workplace-cli-e2e'
)

HOME: Path = Path()


@pytest.fixture(scope='module', autouse=True)
def own_stack(tmp_path_factory):
    """A server of our own — or, with ``E2E_DEDICATED_SERVER=1``, the
    target server itself, for a job where nothing else uses it (the
    Windows installer job tests the installed daemon; ``start.sh``, which
    ``owned_agent_stack`` runs, does not exist on Windows)."""
    global HOME
    if os.environ.get('E2E_DEDICATED_SERVER') == '1':
        HOME = Path(os.environ['VIBE_HOME'])
        yield None
        return
    with owned_agent_stack(tmp_path_factory, 'workplace-cli') as server:
        HOME = server.home
        yield server


def _base() -> str:
    return f'{e2e_helpers.BASE_URL}/api/integrations/cli'


def _cards(client) -> dict:
    r = client.get(_base())
    r.raise_for_status()
    return {c['key']: c for c in r.json()['integrations']}


def _ensure_installed(client, key: str) -> dict:
    card = _cards(client)[key]
    if card['installed']:
        return card
    r = client.post(f'{_base()}/{key}/install', timeout=600)
    assert r.status_code == 200, r.text
    return _cards(client)[key]


@pytest.mark.parametrize('key', KEYS)
def test_connect_installs_the_real_cli(api_client, key):
    r = api_client.post(f'{_base()}/{key}/install', timeout=600)
    assert r.status_code == 200, r.text
    version = r.json()['version']
    card = _cards(api_client)[key]
    assert card['installed'] and card['version'] == version
    assert card['auth'] is not None and card['auth']['usable'] is False
    assert card['enabled'] is False
    exe = paths.binary_path(key, INTEGRATIONS[key].binary, HOME)
    assert exe is not None and exe.is_file()
    assert exe.parent.name == version


def _cli_processes(binary: str) -> list[psutil.Process]:
    """Running processes of the managed *binary* under this HOME."""
    root = os.path.normcase(str(HOME / 'tools'))
    found = []
    for p in psutil.process_iter(['exe']):
        exe = os.path.normcase(p.info.get('exe') or '')
        if exe.startswith(root) and Path(exe).stem == binary:
            found.append(p)
    return found


@pytest.mark.parametrize('key', KEYS)
def test_login_starts_and_cancels_cleanly(api_client, key):
    """The real sign-in starts (link / code / QR — nothing approved),
    and Cancel ends it with no CLI process left behind, on this OS."""
    _ensure_installed(api_client, key)
    binary = INTEGRATIONS[key].binary
    r = api_client.post(f'{_base()}/{key}/login', json={})
    assert r.status_code == 200, r.text
    deadline = time.time() + 90
    view = r.json()
    while not (view['url'] or view['user_code'] or view['qr']):
        assert view['state'] in ('starting', 'waiting'), view
        assert time.time() < deadline, f'{key}: no sign-in shown: {view}'
        time.sleep(1)
        view = api_client.get(f'{_base()}/{key}/login').json()
    assert view['url'] and view['url'].startswith('https://'), view
    if key == 'wecom':
        while not view['qr'] and time.time() < deadline:
            time.sleep(0.5)
            view = api_client.get(f'{_base()}/{key}/login').json()
        png = api_client.get(f'{_base()}/{key}/login/qr')
        assert png.status_code == 200 and png.content[:4] == b'\x89PNG'
    if key == 'dingtalk':
        assert view['user_code'], view
    assert _cli_processes(binary), f'{key}: no {binary} running mid-login'
    started = time.time()
    r = api_client.delete(f'{_base()}/{key}/login', timeout=30)
    assert r.json() == {'cancelled': True}
    assert time.time() - started < 15, 'cancel did not return promptly'
    view = api_client.get(f'{_base()}/{key}/login').json()
    assert view['state'] == 'cancelled', view
    deadline = time.time() + 15
    while _cli_processes(binary) and time.time() < deadline:
        time.sleep(0.5)
    assert not _cli_processes(binary), f'{binary} outlived its cancelled login'


def _open_integrations(page):
    page.reload()
    page.wait_for_selector('h1', timeout=10000)
    page.locator('[data-testid="nav-settings"]').click()
    page.locator('[data-testid="settings-tab-integrations"]').click()
    page.locator('[data-testid="cli-card"]').wait_for(timeout=10000)


def test_ui_connect_starts_each_sign_in(api_client, authenticated_page):
    for key in KEYS:
        _ensure_installed(api_client, key)
    page = authenticated_page
    _open_integrations(page)
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    for key in KEYS:
        row = page.get_by_test_id(f'cli-row-{key}')
        expect(row.get_by_test_id(f'cli-status-{key}')).to_contain_text(
            'Not signed in'
        )
        row.get_by_test_id(f'cli-connect-{key}').click()
        panel = page.get_by_test_id(f'cli-login-{key}')
        expect(panel).to_be_visible(timeout=30000)
        link = page.get_by_test_id(f'cli-login-url-{key}')
        expect(link).to_be_visible(timeout=60000)
        href = link.get_attribute('href') or ''
        if key == 'wecom':
            assert 'work.weixin.qq.com' in href, href
            qr = page.get_by_test_id('cli-login-qr-wecom')
            expect(qr).to_be_visible(timeout=30000)
            # The image the page shows is a real PNG, served to admins.
            r = api_client.get(f'{_base()}/wecom/login/qr')
            assert r.status_code == 200
            assert r.content.startswith(b'\x89PNG')
        elif key == 'feishu':
            assert re.match(r'https://open\.(feishu\.cn|larksuite\.com)/', href)
            expect(panel).to_contain_text('Step 1/2')
        else:
            assert href.startswith('https://'), href
            code = page.get_by_test_id('cli-login-code-dingtalk')
            expect(code).to_have_text(re.compile(r'\S{4,}'))
        page.screenshot(path=str(SHOT_DIR / f'connect-{key}.png'))
        page.get_by_test_id(f'cli-cancel-{key}').click()
        expect(panel).to_be_hidden(timeout=15000)
        view = api_client.get(f'{_base()}/{key}/login').json()
        assert view['state'] == 'cancelled', view
    # Nothing was approved: nothing is enabled.
    assert not any(c['enabled'] for c in _cards(api_client).values())


# ── the agent side ─────────────────────────────────────

_SKILLS_CACHE: dict[str, tuple[str, dict]] = {}


def _set_enabled(enabled: set[str]) -> None:
    """Lay down exactly *enabled* umbrellas (what manager.enable does
    after its sign-in check) in the owned server's home."""
    for key in KEYS:
        integ = INTEGRATIONS[key]
        if key in enabled:
            version = paths.read_manifest(key, HOME)['version']
            if key not in _SKILLS_CACHE:
                subs = manager._fetch_skills_blocking(integ, version)
                _SKILLS_CACHE[key] = (version, subs)
            version, subs = _SKILLS_CACHE[key]
            skills.install(integ, version, subs, HOME)
        else:
            skills.uninstall(integ, HOME)


_PROMPT = (
    'Check which of these commands exist on your PATH: wecom-cli, '
    'lark-cli, dws. Use the Bash tool: run `command -v <name>` for each, '
    'and for each one that exists run `<name> --version`; a command that '
    'is missing, or whose --version fails, counts as MISSING. Do not install '
    'anything and do not run any other subcommand. Also list the names of '
    'the skills available to you that are exactly "wecom", "feishu" or '
    '"dingtalk". Then call vibe_seller_set_task_result with ONLY a JSON '
    'object like {"wecom-cli": "<first line of --version or MISSING>", '
    '"lark-cli": "...", "dws": "...", "skills": ["..."]}.'
)


def _result_json(task: dict) -> dict:
    text = task.get('result') or ''
    m = re.search(r'\{.*\}', text, re.S)
    assert m, f'no JSON in the result: {text!r}'
    return json.loads(m.group(0))


@pytest.mark.parametrize(
    'enabled',
    [('wecom',), ('wecom', 'feishu'), ('wecom', 'feishu', 'dingtalk')],
    ids=['one', 'two', 'three'],
)
def test_agent_sees_exactly_the_enabled_clis(api_client, enabled):
    for key in KEYS:
        _ensure_installed(api_client, key)
    _set_enabled(set(enabled))
    cards = _cards(api_client)
    assert {k for k, c in cards.items() if c['enabled']} == set(enabled)

    store = e2e_helpers.create_store(
        api_client,
        f'cli-e2e-{len(enabled)}-{int(time.time())}',
        browser_backend='chrome',
    )
    task = e2e_helpers.create_task(
        api_client,
        title=f'Which workplace CLIs can you run? ({len(enabled)})',
        store_id=store['id'],
        description=_PROMPT + ' No need to open a browser.',
        plan_mode=False,
    )
    done = e2e_helpers.poll_task_status(
        api_client, task['id'], {'completed'}, fail_statuses={'failed'}
    )
    assert done['status'] == 'completed', done.get('error')
    got = _result_json(done)
    for key in KEYS:
        binary = INTEGRATIONS[key].binary
        version = cards[key]['version']
        if key in enabled:
            assert version in str(got.get(binary)), (key, got)
        else:
            assert 'MISSING' in str(got.get(binary)).upper(), (key, got)
    assert set(got.get('skills') or []) == set(enabled), got
