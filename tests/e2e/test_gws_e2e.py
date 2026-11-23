"""E2E: Google Workspace on a real server, the official ``gws``.

* ``test_connect_installs_the_real_gws`` — the API the page calls
  downloads the REAL gws for this OS; the row then asks for the
  one-time sign-in key before any sign-in. No model.
* ``test_sign_in_starts_relays_and_cancels`` — with a placeholder key
  (Google never sees an approval for it): the real sign-in prints
  Google's link and waits on a local port; a refusal pasted back ends
  it as failed, a cancel ends the next one — no gws left running. No
  model.
* ``test_ui_setup_and_sign_in`` — Settings → Integrations → Google
  Workspace in a real browser: Connect → the setup steps with their
  Google Cloud links → choose the key file → the sign-in link and the
  paste box → Cancel. Screenshots go to ``E2E_GWS_SHOT_DIR``. No model.
* ``test_agent_runs_the_managed_gws_only`` — Google Workspace enabled:
  a real agent finds the managed gws, and it reads ITS sign-in and key
  file from this server's home — even though the server's environment
  carries a personal gws setup (planted below). Spends model credits.

A completed Google sign-in needs a person and a Google account; that
run is manual (see docs/google-workspace.en.md).

**This module owns its server**: enabling Google Workspace puts a skill
into every task workspace and gws on every agent's PATH.
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

KEY = 'gws'
SHOT_DIR = Path(
    os.environ.get('E2E_GWS_SHOT_DIR')
    or Path(tempfile.gettempdir()) / 'gws-e2e'
)
# A personal gws configured through the environment of whoever started
# the server. The managed copy must never act as it.
PERSONAL_ENV = {
    'GOOGLE_WORKSPACE_CLI_CLIENT_ID': 'personal.apps.googleusercontent.com',
    'GOOGLE_WORKSPACE_CLI_CLIENT_SECRET': 'personal-secret',
    'GOOGLE_WORKSPACE_CLI_CONFIG_DIR': '/nonexistent/personal/gws',
    'GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND': 'keyring',
}
PLACEHOLDER_KEY = {
    'installed': {
        'client_id': '000000000000-placeholder.apps.googleusercontent.com',
        'project_id': 'placeholder-project',
        'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
        'token_uri': 'https://oauth2.googleapis.com/token',
        'auth_provider_x509_cert_url': (
            'https://www.googleapis.com/oauth2/v1/certs'
        ),
        'client_secret': 'placeholder-secret',
        'redirect_uris': ['http://localhost'],
    }
}

HOME: Path = Path()


@pytest.fixture(scope='module', autouse=True)
def own_stack(tmp_path_factory):
    global HOME
    if os.environ.get('E2E_DEDICATED_SERVER') == '1':
        HOME = Path(os.environ['VIBE_HOME'])
        yield None
        return
    with owned_agent_stack(tmp_path_factory, 'gws', env=PERSONAL_ENV) as s:
        HOME = s.home
        yield s


def _base() -> str:
    return f'{e2e_helpers.BASE_URL}/api/integrations/cli'


def _card(client) -> dict:
    r = client.get(_base())
    r.raise_for_status()
    return {c['key']: c for c in r.json()['integrations']}[KEY]


def _ensure_installed(client) -> dict:
    if not _card(client)['installed']:
        r = client.post(f'{_base()}/{KEY}/install', timeout=600)
        assert r.status_code == 200, r.text
    return _card(client)


def _gws_processes() -> list[psutil.Process]:
    root = os.path.normcase(str(HOME / 'tools'))
    found = []
    for p in psutil.process_iter(['exe']):
        exe = os.path.normcase(p.info.get('exe') or '')
        if exe.startswith(root) and Path(exe).stem == 'gws':
            found.append(p)
    return found


def _describe(procs: list[psutil.Process]) -> str:
    """Which gws, started by whom and when — so a failure names the
    process (a sign-in, a status probe, ...) instead of only a pid."""
    out = []
    for p in procs:
        try:
            with p.oneshot():
                args = ' '.join(p.cmdline()[1:]) or '?'
                age = time.time() - p.create_time()
                out.append(
                    f'pid {p.pid} ({p.status()}, parent {p.ppid()}, '
                    f'{age:.0f}s old): gws {args}'
                )
        except psutil.Error as e:
            out.append(f'pid {p.pid}: {type(e).__name__}')
    return '; '.join(out)


def _ident(p: psutil.Process) -> tuple[int, float] | None:
    try:
        return p.pid, p.create_time()
    except psutil.Error:
        return None


def _no_gws_left(running: list[psutil.Process], timeout: float = 15) -> None:
    """Every gws in *running* — taken just before the sign-in was ended —
    is gone within *timeout*.

    Only those: ending a sign-in refreshes the card, and the refresh runs
    a fresh ``gws auth status`` (observed in CI, 1 s old at the check).
    That probe did not outlive anything. Identity is pid + start time, so
    a reused pid is not mistaken for a survivor, and the loop and the
    assertion read the same sample.
    """
    before = {i for p in running if (i := _ident(p))}
    deadline = time.time() + timeout
    while True:
        left = [p for p in _gws_processes() if _ident(p) in before]
        if not left or time.time() >= deadline:
            break
        time.sleep(0.5)
    assert not left, f'gws outlived its sign-in: {_describe(left)}'


def _key_file(tmp_dir: Path) -> Path:
    f = tmp_dir / 'client_secret_000000000000-placeholder.json'
    f.write_text(json.dumps(PLACEHOLDER_KEY))
    return f


def _wait_view(client, check, what: str, timeout: float = 90) -> dict:
    deadline = time.time() + timeout
    while True:
        view = client.get(f'{_base()}/{KEY}/login').json()
        if check(view):
            return view
        assert time.time() < deadline, f'{what}: {view}'
        time.sleep(0.5)


def test_connect_installs_the_real_gws(api_client):
    r = api_client.post(f'{_base()}/{KEY}/install', timeout=600)
    assert r.status_code == 200, r.text
    version = r.json()['version']
    card = _card(api_client)
    assert card['installed'] and card['version'] == version
    assert card['auth']['reason'] == 'not_configured'
    assert card['needs_setup'] is True and card['setup'] == 'google_client'
    exe = paths.binary_path(KEY, 'gws', HOME)
    assert exe is not None and exe.is_file()
    assert exe.parent.name == version
    r = api_client.post(f'{_base()}/{KEY}/login', json={})
    assert r.status_code == 400
    assert r.json()['detail']['code'] == 'needs_setup'


def test_sign_in_starts_relays_and_cancels(api_client):
    _ensure_installed(api_client)
    r = api_client.post(
        f'{_base()}/{KEY}/setup',
        json={'payload': json.dumps(PLACEHOLDER_KEY)},
    )
    assert r.status_code == 200, r.text
    assert _card(api_client)['auth']['reason'] == 'not_logged_in'

    r = api_client.post(f'{_base()}/{KEY}/login', json={})
    assert r.status_code == 200, r.text
    view = _wait_view(api_client, lambda v: v['callback'], 'no sign-in link')
    assert view['url'].startswith('https://accounts.google.com/'), view
    assert re.fullmatch(r'http://localhost:\d+', view['callback']), view
    assert view['paste_back'] is True
    running = _gws_processes()
    assert running

    r = api_client.post(
        f'{_base()}/{KEY}/login/paste',
        json={'address': f'{view["callback"]}/?error=access_denied'},
        timeout=90,
    )
    assert r.status_code == 200, r.text
    view = _wait_view(
        api_client, lambda v: v['state'] == 'failed', 'refusal not final'
    )
    _no_gws_left(running)

    r = api_client.post(f'{_base()}/{KEY}/login', json={})
    assert r.status_code == 200, r.text
    _wait_view(api_client, lambda v: v['callback'], 'no second link')
    running = _gws_processes()
    started = time.time()
    r = api_client.delete(f'{_base()}/{KEY}/login', timeout=30)
    assert r.json() == {'cancelled': True}
    assert time.time() - started < 15
    _no_gws_left(running)
    assert _card(api_client)['enabled'] is False


def _open_integrations(page):
    page.reload()
    page.wait_for_selector('h1', timeout=10000)
    page.locator('[data-testid="nav-settings"]').click()
    page.locator('[data-testid="settings-tab-integrations"]').click()
    page.locator('[data-testid="gws-card"]').wait_for(timeout=10000)


def _forget_setup() -> None:
    """Back to "downloaded, no key yet" — the state a first Connect
    finds after the download."""
    config = paths.state_dir(KEY, HOME) / 'config'
    (config / 'client_secret.json').unlink(missing_ok=True)


def test_ui_setup_and_sign_in(api_client, authenticated_page, tmp_path):
    _ensure_installed(api_client)
    _forget_setup()
    page = authenticated_page
    _open_integrations(page)
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    card = page.get_by_test_id('gws-card')
    card.scroll_into_view_if_needed()
    page.screenshot(path=str(SHOT_DIR / 'gws-row.png'))

    card.get_by_test_id(f'cli-connect-{KEY}').click()
    form = page.get_by_test_id(f'cli-setup-{KEY}')
    expect(form).to_be_visible(timeout=30000)
    steps = page.get_by_test_id(f'cli-setup-steps-{KEY}').locator('li')
    expect(steps).to_have_count(6)
    links = form.locator('a[href^="https://console.cloud.google.com/"]')
    expect(links).to_have_count(5)
    page.screenshot(path=str(SHOT_DIR / 'gws-setup.png'))

    page.get_by_test_id(f'cli-setup-file-{KEY}').set_input_files(
        str(_key_file(tmp_path))
    )
    panel = page.get_by_test_id(f'cli-login-{KEY}')
    expect(panel).to_be_visible(timeout=60000)
    link = page.get_by_test_id(f'cli-login-url-{KEY}')
    expect(link).to_be_visible(timeout=60000)
    assert (link.get_attribute('href') or '').startswith(
        'https://accounts.google.com/'
    )
    paste = page.get_by_test_id(f'cli-paste-{KEY}')
    expect(paste).to_contain_text('http://localhost:')
    page.screenshot(path=str(SHOT_DIR / 'gws-sign-in.png'))

    # A wrong address is refused with what to do; the sign-in waits on.
    page.get_by_test_id(f'cli-paste-input-{KEY}').fill(
        'https://example.com/?code=x'
    )
    page.get_by_test_id(f'cli-paste-submit-{KEY}').click()
    expect(page.get_by_test_id('cli-error')).to_be_visible(timeout=15000)
    page.screenshot(path=str(SHOT_DIR / 'gws-paste-wrong.png'))
    assert api_client.get(f'{_base()}/{KEY}/login').json()['state'] == (
        'waiting'
    )

    running = _gws_processes()
    assert running
    page.get_by_test_id(f'cli-cancel-{KEY}').click()
    expect(panel).to_be_hidden(timeout=15000)
    _no_gws_left(running)
    assert _card(api_client)['enabled'] is False


_PROMPT = (
    'Check the Google Workspace command on your PATH. Use the Bash tool: '
    'run `command -v gws`, `gws --version` and `gws auth status`. Do not '
    'sign in, install or change anything. Also list the names of the '
    'skills available to you that are exactly "gws". Then call '
    'vibe_seller_set_task_result with ONLY a JSON object like '
    '{"path": "<command -v output or MISSING>", "version": "<first line '
    'of --version>", "status": <the JSON auth status printed, as an '
    'object>, "skills": ["..."]}.'
)


def test_agent_runs_the_managed_gws_only(api_client):
    card = _ensure_installed(api_client)
    integ = INTEGRATIONS[KEY]
    # What manager.enable lays down after its sign-in check (a person's
    # Google sign-in cannot happen in CI): the real skills of the
    # installed version.
    subs = manager._fetch_skills_blocking(integ, card['version'])
    skills.install(integ, card['version'], subs, HOME)
    api_client.post(
        f'{_base()}/{KEY}/setup',
        json={'payload': json.dumps(PLACEHOLDER_KEY)},
    ).raise_for_status()
    assert _card(api_client)['enabled'] is True

    store = e2e_helpers.create_store(
        api_client, f'gws-e2e-{int(time.time())}', browser_backend='chrome'
    )
    task = e2e_helpers.create_task(
        api_client,
        title='Which Google Workspace tool can you run?',
        store_id=store['id'],
        description=_PROMPT + ' No need to open a browser.',
        plan_mode=False,
    )
    done = e2e_helpers.poll_task_status(
        api_client, task['id'], {'completed'}, fail_statuses={'failed'}
    )
    assert done['status'] == 'completed', done.get('error')
    text = done.get('result') or ''
    got = json.loads(re.search(r'\{.*\}', text, re.S).group(0))

    exe = paths.binary_path(KEY, 'gws', HOME)
    assert Path(got['path']).resolve() == exe.resolve(), got
    assert card['version'] in got['version'], got
    status = got['status']
    config = paths.state_dir(KEY, HOME) / 'config'
    # Its key file and its keyring are this server's, not the personal
    # setup planted in the server's environment.
    assert (
        Path(status['client_config']).resolve()
        == (config / 'client_secret.json').resolve()
    ), status
    assert status['client_config_exists'] is True
    assert status['keyring_backend'] == 'file'
    assert got['skills'] == ['gws'], got
