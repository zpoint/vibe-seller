"""Install enabled Feishu / DingTalk stand-ins with an expired sign-in."""

import json
from pathlib import Path
import shutil
import sys

from app.cli_integrations import paths, skills
from app.cli_integrations.registry import INTEGRATIONS

STANDIN = Path(__file__).with_name('fake_expiring_workplace_cli.py')
VERSION = '1.0.0'


def install(key: str, home: Path, *, expired: bool = True) -> None:
    if key not in ('feishu', 'dingtalk'):
        raise ValueError(key)
    if sys.platform == 'win32':
        raise RuntimeError('the stand-in is a shebang script')
    integ = INTEGRATIONS[key]
    vdir = paths.version_dir(key, VERSION, home)
    vdir.mkdir(parents=True, exist_ok=True)
    exe = vdir / paths.exe_name(integ.binary)
    shutil.copyfile(STANDIN, exe)
    exe.chmod(0o755)
    paths.write_manifest(
        key, {'version': VERSION, 'source': 'e2e stand-in'}, home
    )
    set_expired(key, home, expired)
    subs = {'shared': {'SKILL.md': b'---\nname: example-shared\n---\n'}}
    skills.install(integ, VERSION, subs, home)


def set_expired(key: str, home: Path, expired: bool) -> None:
    state = paths.state_dir(key, home)
    state.mkdir(parents=True, exist_ok=True)
    (state / 'standin.json').write_text(
        json.dumps({'expired': expired}), encoding='utf-8'
    )


def reset(home: Path) -> None:
    for key in ('feishu', 'dingtalk'):
        skills.uninstall(INTEGRATIONS[key], home)
