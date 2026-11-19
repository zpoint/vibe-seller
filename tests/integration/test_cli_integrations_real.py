"""The REAL WeCom / Feishu / DingTalk CLIs, downloaded the way users get them.

For each platform, on whatever OS runs this (CI runs it on Linux,
macOS and Windows): resolve the current release, download it through
the proxy-aware path from the official host or its mirror, verify the
published checksum, prove the binary runs, ask it for its login state
in a fresh home (it must say "not signed in", offline), then fetch the
official skills of that exact version and build the umbrella — and
check that renaming the skill dirs broke no relative link the upstream
layout had working.

Network-bound (npm registry, GitHub / npmmirror / Gitee). No login is
attempted: signing in needs a person (see tests/e2e for that).
"""

from pathlib import Path
import re

import pytest

from app.cli_integrations import manager, paths, skills
from app.cli_integrations.base import host_platform
from app.cli_integrations.registry import INTEGRATIONS

pytestmark = pytest.mark.integration

_LINK = re.compile(r'\]\(([^)#\s]+)')
_PATH = re.compile(r'`((?:\.\./|\./)[^`\s]+)`')

# The official skills every platform must ship (frontmatter names).
EXPECTED = {
    'wecom': {'shared', 'message', 'doc', 'calendar', 'contact'},
    'feishu': {'shared', 'im', 'doc', 'sheets', 'calendar', 'base'},
    'dingtalk': {'shared', 'chat', 'doc', 'calendar', 'contact', 'aitable'},
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


@pytest.mark.parametrize('key', ['wecom', 'feishu', 'dingtalk'])
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
