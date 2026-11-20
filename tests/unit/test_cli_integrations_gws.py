"""Google Workspace (``gws``) as a managed platform.

Fed the output gws 0.22.x really prints (``auth status`` JSON, the
sign-in link of both its login paths), and the key file Google Cloud
really hands out (ids replaced). Pins what the page and the agent rely
on: which state the row shows, which link and local address the sign-in
waits on, which key files are accepted, which build is downloaded, which
skills the agent gets — and that nothing reaches a personal gws install
or sign-in on the same machine.
"""

import hashlib
import io
import json
import os
import stat
import sys
import tarfile

import pytest

from app.cli_integrations import download, manager, paths, skills
from app.cli_integrations.base import AuthStatus, LoginView, SetupError
from app.cli_integrations.gws import GoogleWorkspace
from app.cli_integrations.registry import apply_enabled_env

pytestmark = pytest.mark.unit

gws = GoogleWorkspace()
parse = gws.login_steps(AuthStatus(False, 'not_logged_in'))[0].parse

# The link as each of gws's two login paths prints it (yup-oauth2 on
# stderr without a proxy; its own flow on stdout with one).
YUP_LINK = (
    '  https://accounts.google.com/o/oauth2/auth?scope=https://www.'
    'googleapis.com/auth/drive%20openid&access_type=offline&redirect_uri='
    'http://localhost:53969&response_type=code&client_id=1-x.apps.'
    'googleusercontent.com&prompt=select_account+consent'
)
PROXY_LINK = (
    '  https://accounts.google.com/o/oauth2/auth?scope=https%3A%2F%2Fwww%2E'
    'googleapis%2Ecom%2Fauth%2Fdrive&access_type=offline&redirect_uri='
    'http%3A%2F%2Flocalhost%3A61234&response_type=code&client_id=1%2Dx&'
    'prompt=select_account+consent'
)

CLIENT = {
    'installed': {
        'client_id': '123-example.apps.googleusercontent.com',
        'project_id': 'example-project',
        'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
        'token_uri': 'https://oauth2.googleapis.com/token',
        'auth_provider_x509_cert_url': (
            'https://www.googleapis.com/oauth2/v1/certs'
        ),
        'client_secret': 'placeholder-secret',
        'redirect_uris': ['http://localhost'],
    }
}


def _feed(lines: list[str]) -> LoginView:
    view = LoginView()
    for line in lines:
        parse(line, view)
    return view


# ── the sign-in link ───────────────────────────────────


@pytest.mark.parametrize(
    'link, callback',
    [
        (YUP_LINK, 'http://localhost:53969'),
        (PROXY_LINK, 'http://localhost:61234'),
    ],
)
def test_the_sign_in_link_and_its_local_address(link, callback):
    view = _feed(['Open this URL in your browser to authenticate:', '', link])
    assert view.url == link.strip()
    assert view.callback == callback
    assert view.message is None


def test_a_failure_line_becomes_the_message_not_the_json_dump():
    view = _feed([
        '{',
        '  "error": {',
        '    "code": 401,',
        '    "message": "OAuth flow failed: invalid_client",',
        '  }',
        '}',
        'error[auth]: OAuth flow failed: invalid_client',
    ])
    assert view.message == 'OAuth flow failed: invalid_client'
    assert view.url is None


def test_the_sign_in_asks_only_for_the_services_the_agent_gets():
    (step,) = gws.login_steps(AuthStatus(False, 'not_logged_in'))
    assert step.argv == (
        'auth',
        'login',
        '-s',
        'drive,gmail,sheets,docs,calendar',
    )
    assert step.paste_back is True
    assert step.hint == 'gws_auth'


# ── auth status ────────────────────────────────────────

_NONE = {
    'auth_method': 'none',
    'client_config_exists': False,
    'encrypted_credentials_exists': False,
    'plain_credentials_exists': False,
    'keyring_backend': 'file',
}
_CONFIGURED = {**_NONE, 'client_config_exists': True, 'project_id': 'p1'}
_SIGNED_IN = {
    **_CONFIGURED,
    'auth_method': 'oauth2',
    'encrypted_credentials_exists': True,
    'encryption_valid': True,
    'has_refresh_token': True,
}


async def _status(out: str, rc: int = 0, err: str = '') -> AuthStatus:
    async def run(args):
        assert args == ['auth', 'status']
        return rc, out, err

    return await gws.auth_status(run)


@pytest.mark.parametrize(
    'data, usable, reason, identity',
    [
        (_NONE, False, 'not_configured', None),
        (_CONFIGURED, False, 'not_logged_in', None),
        (
            {**_SIGNED_IN, 'token_valid': True, 'user': 'owner@example.com'},
            True,
            'logged_in',
            'owner@example.com',
        ),
        # Revoked, or a "testing" Google project's 7-day expiry.
        (
            {
                **_SIGNED_IN,
                'token_valid': False,
                'token_error': 'Token has been expired or revoked.',
            },
            False,
            'expired',
            None,
        ),
        # Saved with a key this machine does not have.
        ({**_SIGNED_IN, 'encryption_valid': False}, False, 'expired', None),
        # Google did not answer: the saved sign-in counts, unchecked.
        (_SIGNED_IN, True, 'logged_in', None),
    ],
)
async def test_status_states(data, usable, reason, identity):
    status = await _status(json.dumps(data, indent=2))
    assert (status.usable, status.reason, status.identity) == (
        usable,
        reason,
        identity,
    )
    assert gws.needs_setup(status) is (reason == 'not_configured')


async def test_status_after_a_preamble_line():
    out = 'Using keyring backend: file\n' + json.dumps(_CONFIGURED)
    assert (await _status(out)).reason == 'not_logged_in'


@pytest.mark.parametrize('out, rc', [('', 127), ('garbage', 0), ('[]', 0)])
async def test_status_that_cannot_be_read_is_an_error(out, rc):
    status = await _status(out, rc, 'not installed')
    assert (status.usable, status.reason) == (False, 'error')


# ── the sign-in key file ───────────────────────────────


def _client_file(home):
    return paths.state_dir('gws', home) / 'config' / 'client_secret.json'


def test_a_desktop_key_file_is_stored_where_gws_reads_it(tmp_path):
    payload = json.dumps({**CLIENT, 'extra': 'dropped'})
    gws.apply_setup(payload, tmp_path)
    stored = _client_file(tmp_path)
    assert json.loads(stored.read_text()) == CLIENT
    assert gws.env(tmp_path)['GOOGLE_WORKSPACE_CLI_CONFIG_DIR'] == str(
        stored.parent
    )
    if sys.platform != 'win32':
        assert stat.S_IMODE(stored.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    'payload, code',
    [
        (json.dumps({'web': CLIENT['installed']}), 'google_client_web'),
        ('{not json', 'google_client_invalid'),
        ('[1, 2]', 'google_client_invalid'),
        (json.dumps({'other': {}}), 'google_client_invalid'),
        (
            json.dumps({
                'installed': {**CLIENT['installed'], 'client_secret': ''}
            }),
            'google_client_invalid',
        ),
        (
            json.dumps({
                'installed': {
                    k: v
                    for k, v in CLIENT['installed'].items()
                    if k != 'project_id'
                }
            }),
            'google_client_invalid',
        ),
        ('x' * (65 * 1024), 'google_client_invalid'),
    ],
)
def test_an_unusable_key_file_is_refused_and_nothing_stored(
    tmp_path, payload, code
):
    with pytest.raises(SetupError) as e:
        gws.apply_setup(payload, tmp_path)
    assert e.value.code == code
    assert not _client_file(tmp_path).exists()


# ── the build ──────────────────────────────────────────


@pytest.mark.parametrize(
    'plat, asset',
    [
        ('darwin-arm64', 'google-workspace-cli-aarch64-apple-darwin.tar.gz'),
        ('darwin-x64', 'google-workspace-cli-x86_64-apple-darwin.tar.gz'),
        ('linux-x64', 'google-workspace-cli-x86_64-unknown-linux-musl.tar.gz'),
        (
            'linux-arm64',
            'google-workspace-cli-aarch64-unknown-linux-musl.tar.gz',
        ),
        ('win32-x64', 'google-workspace-cli-x86_64-pc-windows-msvc.zip'),
    ],
)
def test_the_release_asset_and_its_published_checksum(monkeypatch, plat, asset):
    body = b'release bytes'
    digest = hashlib.sha256(body).hexdigest()
    asked = []

    def fake_fetch(urls, **_):
        asked.append(list(urls))
        return f'{digest}  {asset}\n'.encode(), urls[0]

    monkeypatch.setattr(download, 'fetch', fake_fetch)
    got = gws.binary_asset('0.22.5', plat)
    base = 'https://github.com/googleworkspace/cli/releases/download/v0.22.5'
    assert asked == [[f'{base}/{asset}.sha256']]
    assert got.urls == (f'{base}/{asset}',)
    got.verify(body)
    with pytest.raises(download.IntegrityError):
        got.verify(b'tampered')
    exe = 'gws.exe' if plat.startswith('win32') else 'gws'
    assert got.member(f'./{exe}') and got.member(exe)
    assert not got.member('./README.md')


def test_no_build_for_this_platform_is_unsupported():
    with pytest.raises(ValueError):
        gws.binary_asset('0.22.5', 'win32-arm64')


def test_a_checksum_file_without_the_asset_is_a_download_error(monkeypatch):
    monkeypatch.setattr(
        download, 'fetch', lambda urls, **_: (b'0' * 64 + b'  other\n', urls[0])
    )
    with pytest.raises(download.DownloadError):
        gws.binary_asset('0.22.5', 'linux-x64')


# ── skills: the services signed in for ─────────────────


def test_only_the_signed_in_services_skills_reach_the_agent():
    """``recipe-*`` / ``persona-*`` and the services the sign-in does
    not cover (Chat, Classroom, …) would only fail for the agent."""
    names = [
        'gws-shared',
        'gws-drive',
        'gws-drive-upload',
        'gws-gmail',
        'gws-gmail-send',
        'gws-sheets-read',
        'gws-docs-write',
        'gws-calendar-agenda',
        'gws-chat',
        'gws-chat-send',
        'gws-classroom',
        'gws-workflow-email-to-task',
        'gws-modelarmor',
        'gws-people',
        'recipe-save-email-to-doc',
        'persona-exec-assistant',
    ]
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tf:
        for n in names:
            body = f'---\nname: {n}\ndescription: d\n---\n'.encode()
            info = tarfile.TarInfo(f'cli-0.22.5/skills/{n}/SKILL.md')
            info.size = len(body)
            tf.addfile(info, io.BytesIO(body))
    subs = skills.select(download.iter_archive(buf.getvalue()), gws.layout)
    assert sorted(subs) == [
        'calendar-agenda',
        'docs-write',
        'drive',
        'drive-upload',
        'gmail',
        'gmail-send',
        'shared',
        'sheets-read',
    ]


def test_the_agent_is_told_never_to_sign_in_or_export():
    umbrella = skills.umbrella_md(
        gws, '0.22.5', {'shared': {'SKILL.md': b'---\nname: gws-shared\n---\n'}}
    )
    for cmd in ('auth login', 'auth setup', 'auth logout', 'auth export'):
        assert f'`gws {cmd}`' in umbrella
    assert 'Google Workspace row' in umbrella


# ── isolation from a personal gws ──────────────────────

PERSONAL = {
    'GOOGLE_WORKSPACE_CLI_CLIENT_ID': 'personal-id',
    'GOOGLE_WORKSPACE_CLI_CLIENT_SECRET': 'personal-secret',
    'GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE': '/elsewhere/creds.json',
    'GOOGLE_WORKSPACE_PROJECT_ID': 'personal-project',
    'GOOGLE_WORKSPACE_CLI_TOKEN': 'personal-token',
    'GOOGLE_WORKSPACE_CLI_CONFIG_DIR': '/elsewhere/.config/gws',
    'GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND': 'keyring',
    'CLOUDSDK_CONFIG': '/elsewhere/.config/gcloud',
}


def _assert_isolated(env: dict, home) -> None:
    for var in gws.env_unset:
        assert var not in env, var
    assert env['GOOGLE_WORKSPACE_CLI_TOKEN'] == ''
    assert env['GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND'] == 'file'
    assert env['GOOGLE_WORKSPACE_CLI_CONFIG_DIR'] == str(
        paths.state_dir('gws', home) / 'config'
    )
    # gws runs ``gcloud`` itself: never with the person's gcloud config.
    assert env['CLOUDSDK_CONFIG'] == str(
        paths.state_dir('gws', home) / 'gcloud'
    )


def test_the_servers_runs_never_see_a_personal_gws_setup(tmp_path, monkeypatch):
    """A personal gws configured through the environment — or its OS
    keyring entry, which gws shares per OS user — must never become the
    identity of the managed copy."""
    for k, v in PERSONAL.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(
        manager.system_proxy,
        'checked_download_env',
        lambda targets: {**os.environ, 'HTTPS_PROXY': 'http://127.0.0.1:9'},
    )
    env = manager.run_env(gws, tmp_path)
    _assert_isolated(env, tmp_path)
    # The server's own runs reach Google over the path that works.
    assert env['HTTPS_PROXY'] == 'http://127.0.0.1:9'


def test_the_agents_env_never_sees_a_personal_gws_setup(tmp_path):
    home = tmp_path
    paths.tool_dir('gws', home).mkdir(parents=True)
    paths.write_manifest('gws', {'version': '0.22.5'}, home)
    exe = paths.binary_path('gws', 'gws', home)
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b'')
    umbrella = paths.skill_dir('gws', home)
    umbrella.mkdir(parents=True)
    (umbrella / 'SKILL.md').write_text('x')

    env = dict(PERSONAL)
    apply_enabled_env(env, home)
    _assert_isolated(env, home)


def test_a_disabled_gws_leaves_the_agents_env_alone(tmp_path):
    env = dict(PERSONAL)
    apply_enabled_env(env, tmp_path)
    assert env == PERSONAL
