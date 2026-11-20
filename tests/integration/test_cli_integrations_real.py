"""The REAL WeCom / Feishu / DingTalk / Google Workspace CLIs, downloaded the
way users get them.

For each platform, on whatever OS runs this (CI runs it on Linux,
macOS and Windows): resolve the current release, download it through
the proxy-aware path from the official host or its mirror, verify the
published checksum, prove the binary runs, ask it for its login state
in a fresh home (it must say "not signed in", offline), then fetch the
official skills of that exact version and build the umbrella — and
check that renaming the skill dirs broke no relative link the upstream
layout had working.

Network-bound (npm registry, GitHub / npmmirror / Gitee). No login is
completed: signing in needs a person (see tests/e2e for that). Google
Workspace's sign-in is STARTED for real with a placeholder key: gws must
print Google's link and wait on a local port, a refusal relayed to that
port must end it cleanly, and so must a cancel.
"""

import asyncio
import json
import os
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

import psutil
import pytest

from app.cli_integrations import manager, paths, skills
from app.cli_integrations.base import host_platform
from app.cli_integrations.login import login_manager
from app.cli_integrations.registry import INTEGRATIONS

pytestmark = pytest.mark.integration

_LINK = re.compile(r'\]\(([^)#\s]+)')
_PATH = re.compile(r'`((?:\.\./|\./)[^`\s]+)`')

# The official skills every platform must ship (frontmatter names).
EXPECTED = {
    'wecom': {'shared', 'message', 'doc', 'calendar', 'contact'},
    'feishu': {'shared', 'im', 'doc', 'sheets', 'calendar', 'base'},
    'dingtalk': {'shared', 'chat', 'doc', 'calendar', 'contact', 'aitable'},
    'gws': {'shared', 'gmail', 'drive', 'sheets', 'docs', 'calendar'},
}


def _broken_links(root: Path) -> set[tuple[str, str]]:
    """(file, target) of relative links under *root* that do not resolve."""
    broken = set()
    for md in root.rglob('*.md'):
        text = md.read_text(encoding='utf-8', errors='replace')
        for target in {*_LINK.findall(text), *_PATH.findall(text)}:
            if '://' in target or target.startswith(('/', 'mailto:')):
                continue
            if not (md.parent / target).exists():
                broken.add((str(md.relative_to(root)), target))
    return broken


def _write_upstream(subs, prefix: str, dest: Path) -> None:
    for name, files in subs.items():
        for rel, body in files.items():
            f = dest / f'{prefix}{name}' / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(body)


@pytest.mark.parametrize('key', ['wecom', 'feishu', 'dingtalk', 'gws'])
async def test_real_cli_installs_runs_and_brings_its_skills(key, tmp_path):
    integ = INTEGRATIONS[key]
    home = tmp_path / 'home'

    manifest = await manager.install(integ, home)
    assert manifest['platform'] == host_platform()
    assert re.match(r'^\d+\.\d+\.\d+$', manifest['version']), manifest
    exe = paths.binary_path(key, integ.binary, home)
    assert exe is not None and exe.is_file()
    assert exe.parent == paths.version_dir(key, manifest['version'], home)

    # Fresh home: a real, offline "not signed in" — never "usable".
    status = await manager.auth_status(integ, home)
    assert not status.usable
    assert status.reason in ('not_logged_in', 'not_configured'), status

    # The login state lives in OUR state dir, never the user's home.
    state = paths.state_dir(key, home)
    assert state.is_dir()

    # Signed out → enabling is refused; build the umbrella directly.
    with pytest.raises(manager.IntegrationError):
        await manager.enable(integ, home)
    subs = manager._fetch_skills_blocking(integ, manifest['version'])
    assert EXPECTED[key] <= set(subs), sorted(subs)
    names = skills.install(integ, manifest['version'], subs, home)
    root = paths.skill_dir(key, home)
    umbrella = (root / 'SKILL.md').read_text(encoding='utf-8')
    meta = skills.frontmatter(umbrella)
    assert meta['name'] == key
    for name in names:
        assert f'`./{name}/SKILL.md`' in umbrella
        assert (root / name / 'SKILL.md').is_file()
    for cmd in integ.forbidden_commands:
        assert f'`{integ.binary} {cmd}`' in umbrella

    # Renaming <prefix>x → x broke nothing that resolved upstream.
    upstream = tmp_path / 'upstream'
    _write_upstream(subs, integ.layout.prefix, upstream)
    before = len(_broken_links(upstream))
    # The generated umbrella is checked above (its rules quote a
    # `./xxx/SKILL.md` placeholder on purpose).
    after = {b for b in _broken_links(root) if b[0] != 'SKILL.md'}
    assert len(after) <= before, sorted(after)[:20]


# ── Google Workspace: the real sign-in, started and ended ─────────────

# A Desktop-app key in the shape Google Cloud hands out (placeholder ids:
# Google never sees an approval for it).
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


async def _waiting(key: str, timeout: float = 60.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        session = login_manager.get(key)
        if session is not None and session.view.callback:
            return session
        if session is not None and not session.active:
            raise AssertionError(session.view)
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError('gws never printed its sign-in link')
        await asyncio.sleep(0.1)


def _cli_procs(exe: Path) -> list:
    out = []
    for p in psutil.process_iter(['exe', 'cmdline']):
        try:
            if p.info['exe'] and Path(p.info['exe']).resolve() == exe.resolve():
                out.append(p)
        except (psutil.Error, OSError):
            continue
    return out


@pytest.fixture
async def gws_home(tmp_path, monkeypatch):
    integ = INTEGRATIONS['gws']
    home = tmp_path / 'home'
    # The OS user's home, as gws would see it: a personal gws keeps its
    # config under it (``~/.config/gws``), and so does the gcloud gws
    # runs by itself (``~/.config/gcloud``, its sign-in). The managed
    # copy never touches either — with gcloud on PATH, as on every CI
    # runner.
    user = tmp_path / 'user'
    user.mkdir()
    monkeypatch.setenv('HOME', str(user))
    monkeypatch.setenv('USERPROFILE', str(user))
    await manager.install(integ, home)
    yield integ, home
    await login_manager.shutdown()
    login_manager._sessions.pop('gws', None)


async def test_real_gws_sign_in_starts_waits_locally_and_ends_cleanly(
    gws_home,
):
    integ, home = gws_home
    assert (await manager.auth_status(integ, home)).reason == 'not_configured'
    with pytest.raises(manager.IntegrationError) as e:
        await manager.start_login(integ, home)
    assert e.value.code == 'needs_setup'

    await manager.setup(integ, json.dumps(PLACEHOLDER_KEY), home)
    status = await manager.auth_status(integ, home)
    assert (status.usable, status.reason) == (False, 'not_logged_in')

    view = await manager.start_login(integ, home)
    session = await _waiting('gws')
    view = session.view
    assert view.state == 'waiting' and view.paste_back
    assert view.url.startswith('https://accounts.google.com/o/oauth2/')
    query = parse_qs(urlsplit(view.url).query)
    assert query['client_id'] == [PLACEHOLDER_KEY['installed']['client_id']]
    assert re.fullmatch(r'http://localhost:\d+', view.callback), view.callback

    # The person said no in a browser on another computer: the refusal
    # pasted back reaches gws, and the sign-in ends — no hang.
    await manager.paste_back(integ, f'{view.callback}/?error=access_denied')
    assert session.task.done()
    assert (session.view.state, session.view.error) == ('failed', 'denied')
    assert not (await manager.auth_status(integ, home)).usable
    exe = paths.binary_path('gws', 'gws', home)
    assert _cli_procs(exe) == []
    assert list(Path(os.environ['HOME']).iterdir()) == []


async def test_real_gws_sign_in_cancel_leaves_nothing_running(gws_home):
    integ, home = gws_home
    await manager.setup(integ, json.dumps(PLACEHOLDER_KEY), home)
    await manager.start_login(integ, home)
    session = await _waiting('gws')
    exe = paths.binary_path('gws', 'gws', home)
    assert _cli_procs(exe)
    assert await login_manager.cancel('gws')
    assert session.view.state == 'cancelled'
    assert _cli_procs(exe) == []
    # Everything gws wrote is ours; the OS user's home is untouched.
    config = paths.state_dir('gws', home) / 'config'
    assert (config / 'client_secret.json').is_file()
    assert list(Path(os.environ['HOME']).iterdir()) == []
