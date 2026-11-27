"""Install an enabled stand-in WeCom on an owned e2e server.

``set_expired`` switches its one authorization state while the server
runs; ``renew`` also changes the stored sign-in, as a fresh scan does.
"""

import json
import os
from pathlib import Path
import shutil
import sys
import time

from app.cli_integrations import paths, skills
from app.cli_integrations.registry import INTEGRATIONS

STANDIN = Path(__file__).with_name('fake_wecom_cli.py')
VERSION = '1.3.4'
# The Windows stand-in needs a real wecom-cli.exe (the integration
# runner spawns it, and the agent's shell resolves it by name); the
# console-script fixture builds one through pip.
EXE_FIXTURE = Path(__file__).with_name('fixtures') / 'fake-wecom-cli'
_MINIMAL_SUBS = {
    'shared': {
        'SKILL.md': b'---\nname: wecomcli-shared\ndescription: Shared.\n---\n'
    },
}


def _install_windows_exe(dest: Path) -> None:
    """Build a genuine ``wecom-cli.exe`` for this venv, at *dest*.

    ``pip install -e`` of the fixture creates the console-script shim
    in this venv's ``Scripts/`` — a real PE that launches this venv's
    python and runs the stand-in's ``main``. Copying it into the
    integration's version dir satisfies both the runner (it executes
    the exe) and the agent (its shell resolves ``wecom-cli`` to it).
    """
    import subprocess

    built = subprocess.run(
        [
            sys.executable,
            '-m',
            'pip',
            'install',
            '-q',
            '-e',
            str(EXE_FIXTURE),
        ],
        capture_output=True,
        text=True,
    )
    if built.returncode != 0:
        raise RuntimeError(
            f'building the wecom-cli shim failed:\n{built.stderr[-800:]}'
        )
    shim = Path(sys.executable).parent / 'wecom-cli.exe'
    if not shim.is_file():
        raise RuntimeError(f'pip built no wecom-cli.exe at {shim}')
    shutil.copyfile(shim, dest)


def install(home: Path, *, subs: dict | None = None) -> None:
    integ = INTEGRATIONS['wecom']
    vdir = paths.version_dir('wecom', VERSION, home)
    vdir.mkdir(parents=True, exist_ok=True)
    exe = vdir / paths.exe_name(integ.binary)
    if sys.platform == 'win32':
        _install_windows_exe(exe)
    else:
        shutil.copyfile(STANDIN, exe)
        exe.chmod(0o755)
    paths.write_manifest(
        'wecom', {'version': VERSION, 'source': 'e2e stand-in'}, home
    )
    state = paths.state_dir('wecom', home)
    state.mkdir(parents=True, exist_ok=True)
    (state / 'credentials.enc').write_text('stand-in', encoding='utf-8')
    set_expired(home, False)
    skills.install(integ, VERSION, subs or _MINIMAL_SUBS, home)


def set_expired(home: Path, expired: bool) -> None:
    state = paths.state_dir('wecom', home)
    state.mkdir(parents=True, exist_ok=True)
    (state / 'standin.json').write_text(
        json.dumps({'expired': expired}), encoding='utf-8'
    )


def renew(home: Path) -> None:
    set_expired(home, False)
    cred = paths.state_dir('wecom', home) / 'credentials.enc'
    cred.write_text(f'renewed {time.time()}', encoding='utf-8')
    later = time.time() + 5
    os.utime(cred, (later, later))


def calls(home: Path) -> list[list[str]]:
    log = paths.state_dir('wecom', home) / 'calls.log'
    if not log.is_file():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line]
