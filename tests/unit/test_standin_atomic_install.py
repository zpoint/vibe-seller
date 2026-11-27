"""Stand-in reinstalls must not open a running executable for writing.

CI runs the next UI test while the previous test's server can still be
executing its stand-in CLI. ``shutil.copyfile(source, running_exe)``
then fails with ETXTBSY. The contract is an atomic replacement: the new
file has a new inode, while the old process keeps its old inode.
"""

import os
import sys

import pytest

from app.cli_integrations import paths
from app.cli_integrations.registry import INTEGRATIONS
from tests.e2e import wecom_standin, workplace_expiry_standin

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(
        sys.platform == 'win32', reason='POSIX executable inode'
    ),
]


def _install(key: str, home) -> None:
    if key == 'wecom':
        wecom_standin.install(home)
    else:
        workplace_expiry_standin.install(key, home)


@pytest.mark.parametrize('key', ['wecom', 'feishu', 'dingtalk'])
def test_reinstall_replaces_the_executable_atomically(key, tmp_path):
    home = tmp_path / 'home'
    _install(key, home)
    version = (
        wecom_standin.VERSION
        if key == 'wecom'
        else workplace_expiry_standin.VERSION
    )
    exe = paths.version_dir(key, version, home) / paths.exe_name(
        INTEGRATIONS[key].binary
    )
    before = exe.stat().st_ino

    _install(key, home)
    after = exe.stat().st_ino

    assert after != before, (
        'reinstall truncated the running executable in place'
    )
    assert os.access(exe, os.X_OK)
