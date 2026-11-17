"""Settings → Dida365 → install the TickTick MCP: the real route.

HTTP → the route → the real installer, with fake ``git`` / ``uv`` on
PATH standing in for the network. What the user sees in each case: a
finished install, a second click that does nothing, and a failure that
says why and leaves nothing behind for the next click to mistake for an
install.
"""

import os

import pytest

from app import ticktick_mcp_install as inst
from app.platform import IS_WINDOWS

pytestmark = [
    pytest.mark.workflow,
    pytest.mark.skipif(IS_WINDOWS, reason='fake git/uv are shell scripts'),
]

_GIT = """#!/bin/sh
dest=""
for a in "$@"; do dest="$a"; done
if [ -n "$FAKE_GIT_FAIL" ]; then
  echo "fatal: unable to access github.com: Failed to connect" >&2
  exit 128
fi
mkdir -p "$dest/ticktick_mcp" "$dest/.git"
touch "$dest/ticktick_mcp/__init__.py"
"""

_UV = """#!/bin/sh
if [ "$1" = venv ]; then
  mkdir -p "$2/bin"
  touch "$2/bin/python3"
fi
exit 0
"""


@pytest.fixture
def fake_tools(monkeypatch, tmp_path):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    for name, body in (('git', _GIT), ('uv', _UV)):
        script = bin_dir / name
        script.write_text(body)
        script.chmod(0o755)
    monkeypatch.setenv('PATH', f'{bin_dir}{os.pathsep}/bin{os.pathsep}/usr/bin')
    monkeypatch.setattr(inst, 'TICKTICK_MCP_DIR', tmp_path / 'ticktick-mcp')
    return tmp_path / 'ticktick-mcp'


async def test_install_then_second_click_is_a_no_op(admin_client, fake_tools):
    first = await admin_client.post('/api/dida365/setup-mcp')
    assert first.status_code == 200
    assert first.json() == {
        'ok': True,
        'already_installed': False,
        'path': str(fake_tools),
    }
    assert (fake_tools / inst.INSTALL_MARKER).is_file()

    second = await admin_client.post('/api/dida365/setup-mcp')
    assert second.status_code == 200
    assert second.json()['already_installed'] is True


async def test_failed_install_says_why_and_the_next_click_retries(
    admin_client, fake_tools, monkeypatch
):
    monkeypatch.setenv('FAKE_GIT_FAIL', '1')
    failed = await admin_client.post('/api/dida365/setup-mcp')
    assert failed.status_code == 500
    assert 'Failed to connect' in failed.json()['detail']
    assert not fake_tools.exists()

    monkeypatch.delenv('FAKE_GIT_FAIL')
    retry = await admin_client.post('/api/dida365/setup-mcp')
    assert retry.status_code == 200
    assert retry.json()['already_installed'] is False


async def test_leftover_directory_is_not_reported_as_installed(
    admin_client, fake_tools
):
    (fake_tools / '.git').mkdir(parents=True)
    r = await admin_client.post('/api/dida365/setup-mcp')
    assert r.status_code == 200
    assert r.json()['already_installed'] is False
    assert (fake_tools / 'ticktick_mcp' / '__init__.py').is_file()
