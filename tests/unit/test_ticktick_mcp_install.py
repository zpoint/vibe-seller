"""``app.ticktick_mcp_install``: first-use install of ticktick-mcp.

Run against real subprocesses: fake ``git`` / ``uv`` scripts on PATH
stand in for the network, so a hang, a failure and a kill are the real
thing. The invariants: "installed" means the marker (never just "the
directory exists"); a failed or timed-out install leaves nothing
behind; a stalled clone ends with an error saying why; the clone gets
the system proxy.
"""

import asyncio
import os
import time

import pytest

from app import system_proxy, ticktick_mcp_install as inst
from app.platform import IS_WINDOWS

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(IS_WINDOWS, reason='fake git/uv are shell scripts'),
]

# `git clone --depth 1 URL DEST`: DEST is the last argument.
_FAKE_GIT = """#!/bin/sh
dest=""
for a in "$@"; do dest="$a"; done
echo "$HTTPS_PROXY" > "$FAKE_LOG/git-proxy"
case "$FAKE_GIT" in
  hang)
    sleep 300 &
    echo $! > "$FAKE_LOG/grandchild-pid"
    mkdir -p "$dest/.git"
    wait ;;
  fail)
    echo "fatal: unable to access: Could not resolve host" >&2
    exit 128 ;;
  *)
    mkdir -p "$dest/ticktick_mcp" "$dest/.git"
    touch "$dest/ticktick_mcp/__init__.py" ;;
esac
"""

# `uv venv PATH` and `uv pip install ...`.
_FAKE_UV = """#!/bin/sh
if [ "$1" = venv ]; then
  mkdir -p "$2/bin"
  touch "$2/bin/python3"
  exit 0
fi
[ "$FAKE_UV_PIP" = fail ] && { echo "no matching distribution" >&2; exit 1; }
exit 0
"""


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """Fake git/uv first on PATH; install dir and proxy isolated."""
    bin_dir = tmp_path / 'bin'
    log = tmp_path / 'log'
    bin_dir.mkdir()
    log.mkdir()
    for name, body in (('git', _FAKE_GIT), ('uv', _FAKE_UV)):
        script = bin_dir / name
        script.write_text(body)
        script.chmod(0o755)
    monkeypatch.setenv('PATH', f'{bin_dir}{os.pathsep}/bin{os.pathsep}/usr/bin')
    monkeypatch.setenv('FAKE_LOG', str(log))
    monkeypatch.setattr(inst, 'TICKTICK_MCP_DIR', tmp_path / 'ticktick-mcp')
    for var in system_proxy.PROXY_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(system_proxy.urllib.request, 'getproxies', lambda: {})
    return log


def _pid_gone(pid: int) -> bool:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


async def test_stalled_clone_fails_with_a_reason_and_leaves_nothing(
    fake, monkeypatch
):
    """The field bug: on a network that cannot reach GitHub the clone
    never returned and the UI sat on "installing…" forever."""
    monkeypatch.setenv('FAKE_GIT', 'hang')
    monkeypatch.setattr(inst, 'CLONE_TIMEOUT_S', 1.0)
    started = time.monotonic()
    with pytest.raises(inst.TickTickInstallError) as err:
        await inst.ensure_ticktick_mcp()
    assert time.monotonic() - started < 10
    message = str(err.value)
    assert 'timed out' in message
    assert 'proxy' in message
    # The half-made directory must not pass for an install next time.
    assert not inst.TICKTICK_MCP_DIR.exists()
    assert not inst.ticktick_mcp_installed()
    # git's own helper process dies with it, not just git.
    grandchild = int((fake / 'grandchild-pid').read_text())
    assert _pid_gone(grandchild)


async def test_failed_clone_reports_git_output(fake, monkeypatch):
    monkeypatch.setenv('FAKE_GIT', 'fail')
    with pytest.raises(inst.TickTickInstallError) as err:
        await inst.ensure_ticktick_mcp()
    assert 'Could not resolve host' in str(err.value)
    assert not inst.TICKTICK_MCP_DIR.exists()


async def test_dependency_failure_is_not_an_install(fake, monkeypatch):
    """Clone fine, deps fail: the next click must retry, not find a
    directory and call it done."""
    monkeypatch.setenv('FAKE_UV_PIP', 'fail')
    with pytest.raises(inst.TickTickInstallError):
        await inst.ensure_ticktick_mcp()
    assert not inst.TICKTICK_MCP_DIR.exists()
    assert not inst.ticktick_mcp_installed()
    monkeypatch.delenv('FAKE_UV_PIP')
    await inst.ensure_ticktick_mcp()
    assert inst.ticktick_mcp_installed()


async def test_leftover_directory_is_redone_not_trusted(fake):
    leftover = inst.TICKTICK_MCP_DIR
    (leftover / '.git').mkdir(parents=True)
    assert not inst.ticktick_mcp_installed()
    await inst.ensure_ticktick_mcp()
    assert inst.ticktick_mcp_installed()
    assert (leftover / inst.INSTALL_MARKER).is_file()
    assert (leftover / 'ticktick_mcp' / '__init__.py').is_file()


async def test_installed_checkout_is_left_alone(fake, monkeypatch):
    """An install from before the marker (package + venv) counts, and
    no network command runs for it."""
    root = inst.TICKTICK_MCP_DIR
    (root / 'ticktick_mcp').mkdir(parents=True)
    (root / 'ticktick_mcp' / '__init__.py').touch()
    (root / '.venv' / 'bin').mkdir(parents=True)
    (root / '.venv' / 'bin' / 'python3').touch()
    # A git that would fail the test if it were run.
    monkeypatch.setenv('FAKE_GIT', 'fail')
    assert await inst.ensure_ticktick_mcp() == str(root)
    assert not (fake / 'git-proxy').exists()


async def test_clone_goes_through_the_system_proxy(fake, monkeypatch):
    monkeypatch.setattr(
        system_proxy.urllib.request,
        'getproxies',
        lambda: {'https': '127.0.0.1:7890'},
    )
    await inst.ensure_ticktick_mcp()
    assert (fake / 'git-proxy').read_text().strip() == 'http://127.0.0.1:7890'


async def test_concurrent_clicks_install_once(fake):
    results = await asyncio.gather(
        inst.ensure_ticktick_mcp(), inst.ensure_ticktick_mcp()
    )
    assert results[0] == results[1] == str(inst.TICKTICK_MCP_DIR)
    assert inst.ticktick_mcp_installed()


async def test_missing_tool_names_it(fake, monkeypatch, tmp_path):
    empty = tmp_path / 'empty'
    empty.mkdir()
    monkeypatch.setenv('PATH', str(empty))
    with pytest.raises(inst.TickTickInstallError) as err:
        await inst.ensure_ticktick_mcp()
    assert '`git`' in str(err.value)
