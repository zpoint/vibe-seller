"""First-use install of the TickTick MCP server (``ticktick-mcp``).

Settings → Dida365 → authorize clones the server and installs its
dependencies. Three things are held here, in code, so the click can
neither hang nor leave a half install that later passes for a finished
one:

- **Installed means the marker exists.** The marker is written last,
  after the clone and the dependency install both succeeded. A
  directory without it is a leftover of an interrupted run and is wiped
  and redone — never trusted because it merely exists.
- **The whole click has one deadline** (``INSTALL_BUDGET_S``), shorter
  than the browser's wait for the request, so the user always gets
  the reason, never a client-side "timed out". A clone that cannot
  reach GitHub (the usual state in mainland China without a proxy)
  used to sit forever on "installing…" with no error.
- **Downloads take a proxy that works** (``app.system_proxy``): git and
  uv read only ``*_proxy`` env vars, while the browser — and so the
  user's idea of "my network works" — follows the OS proxy setting.
"""

import asyncio
import contextlib
import logging
import os
import re
import shutil
import signal

from app.platform import IS_WINDOWS, venv_python
from app.system_proxy import (
    GITHUB,
    Decision,
    decide_download_env_async,
    pypi_target,
)
from app.workspace.manager import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)

TICKTICK_MCP_REPO = 'https://github.com/jacepark12/ticktick-mcp.git'
TICKTICK_MCP_DIR = VIBE_SELLER_DIR / 'ticktick-mcp'

# Written last. Its presence is the only thing "installed" means for a
# fresh install.
INSTALL_MARKER = '.install-complete'

# One click, start to finish — waiting for another click's install
# included. Below the frontend's LONG_TIMEOUT_MS (5 min; pinned by a
# test) so the answer always reaches the user.
INSTALL_BUDGET_S = 240.0
# No single step may take the whole budget.
CLONE_TIMEOUT_S = 120.0
DEPS_TIMEOUT_S = 180.0

# git gives up by itself on a transfer slower than 1 KB/s for 30 s,
# well before the deadline kills it.
_GIT_LOW_SPEED = {
    'GIT_HTTP_LOW_SPEED_LIMIT': '1000',
    'GIT_HTTP_LOW_SPEED_TIME': '30',
}

_ONE_AT_A_TIME = asyncio.Lock()

_USERINFO = re.compile(r'(\w+://)[^/\s@]+@')


class TickTickInstallError(RuntimeError):
    """The install failed; the message says why and what to try."""


def ticktick_mcp_installed() -> bool:
    """Whether a usable ticktick-mcp is in place."""
    root = TICKTICK_MCP_DIR
    if (root / INSTALL_MARKER).is_file():
        return True
    # An install made before the marker existed: a full checkout with
    # its venv. A clone cut short never has the package directory.
    return (root / 'ticktick_mcp' / '__init__.py').is_file() and venv_python(
        root / '.venv'
    ).is_file()


def _network_hint(decision: Decision) -> str:
    if decision.dropped:
        return (
            'Your proxy could not reach these sites and the direct '
            'network did not work either. Switch the proxy to a node '
            'that reaches github.com and pypi.org, then try again.'
        )
    if decision.env.get('HTTPS_PROXY') or decision.env.get('https_proxy'):
        return (
            'Check that the proxy can reach github.com and pypi.org, '
            'then try again.'
        )
    return (
        'GitHub may be unreachable from this network. Turn on the '
        'system proxy (or export HTTPS_PROXY before starting Vibe '
        'Seller), then try again.'
    )


async def _kill(proc: asyncio.subprocess.Process) -> None:
    """Kill the child and what it spawned (git runs a remote helper)."""
    if IS_WINDOWS:
        # /T takes the tree: git.exe's git-remote-https.exe would
        # otherwise live on, holding files in the clone directory.
        with contextlib.suppress(OSError):
            killer = await asyncio.create_subprocess_exec(
                'taskkill',
                '/F',
                '/T',
                '/PID',
                str(proc.pid),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)


async def _run_step(
    argv: list[str],
    *,
    decision: Decision,
    env: dict[str, str],
    timeout: float,
    what: str,
) -> None:
    if timeout <= 0:
        raise TickTickInstallError(
            f'{what}: no time left of the {int(INSTALL_BUDGET_S)}s the '
            f'install may take ({decision.describe()}). '
            f'{_network_hint(decision)}'
        )
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=not IS_WINDOWS,
        )
    except FileNotFoundError as e:
        raise TickTickInstallError(
            f'{what}: `{argv[0]}` is not installed or not on PATH.'
        ) from e
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError as e:
        await _kill(proc)
        await proc.wait()
        raise TickTickInstallError(
            f'{what} timed out after {int(timeout)}s '
            f'({decision.describe()}). {_network_hint(decision)}'
        ) from e
    except BaseException:
        # Cancelled (server shutdown, client gone): never orphan it.
        await _kill(proc)
        await proc.wait()
        raise
    if proc.returncode != 0:
        # Output can echo an index URL with a token in it.
        tail = _USERINFO.sub(
            r'\1***@', stderr.decode(errors='replace').strip()[-400:]
        )
        raise TickTickInstallError(
            f'{what} failed ({decision.describe()}): {tail}. '
            f'{_network_hint(decision)}'
        )


async def _remove_tree() -> None:
    await asyncio.to_thread(shutil.rmtree, TICKTICK_MCP_DIR, True)
    if TICKTICK_MCP_DIR.exists():
        logger.warning(
            'Could not fully remove %s; the next install redoes it',
            TICKTICK_MCP_DIR,
        )


async def ensure_ticktick_mcp() -> str:
    """Clone and install ticktick-mcp if not present; return its path.

    Raises ``TickTickInstallError`` (a ``RuntimeError``) when a step
    fails or the click runs out of time; nothing is left behind in that
    case.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + INSTALL_BUDGET_S

    def left(cap: float) -> float:
        return min(cap, deadline - loop.time())

    async with _ONE_AT_A_TIME:
        root = TICKTICK_MCP_DIR
        if ticktick_mcp_installed():
            return str(root)
        # A directory here that is not "installed" is an interrupted
        # run's leftover; cloning into it would fail on it.
        await _remove_tree()
        decision = await decide_download_env_async((GITHUB, pypi_target()))
        env = {
            **decision.env,
            **_GIT_LOW_SPEED,
            # A credential prompt would wait on a terminal nobody has.
            'GIT_TERMINAL_PROMPT': '0',
        }
        venv = root / '.venv'
        step = {'decision': decision, 'env': env}
        try:
            logger.info('Cloning ticktick-mcp to %s', root)
            await _run_step(
                ['git', 'clone', '--depth', '1', TICKTICK_MCP_REPO, str(root)],
                timeout=left(CLONE_TIMEOUT_S),
                what='Downloading ticktick-mcp (git clone)',
                **step,
            )
            await _run_step(
                ['uv', 'venv', str(venv)],
                timeout=left(DEPS_TIMEOUT_S),
                what='Creating the ticktick-mcp environment (uv venv)',
                **step,
            )
            await _run_step(
                [
                    'uv',
                    'pip',
                    'install',
                    '-e',
                    str(root),
                    '--python',
                    str(venv_python(venv)),
                ],
                timeout=left(DEPS_TIMEOUT_S),
                what='Installing ticktick-mcp dependencies (uv pip install)',
                **step,
            )
            await asyncio.to_thread((root / INSTALL_MARKER).write_text, 'ok\n')
        except BaseException:
            await _remove_tree()
            raise
        return str(root)
