"""First-use install of the TickTick MCP server (``ticktick-mcp``).

Settings → Dida365 → authorize clones the server and installs its
dependencies. Three things are held here, in code, so the click can
neither hang nor leave a half install that later passes for a finished
one:

- **Installed means the marker exists.** The marker is written last,
  after the clone and the dependency install both succeeded. A
  directory without it is a leftover of an interrupted run and is wiped
  and redone — never trusted because it merely exists.
- **Every step has a deadline.** A clone that cannot reach GitHub (the
  usual state in mainland China without a proxy) used to sit forever on
  "installing…" with no error. It now fails with the reason and what to
  do.
- **Downloads take the system proxy** (``app.system_proxy``): git and
  uv read only ``*_proxy`` env vars, while the browser — and so the
  user's idea of "my network works" — follows the OS proxy setting.
"""

import asyncio
import contextlib
import logging
import os
import shutil
import signal

from app.platform import IS_WINDOWS, venv_python
from app.system_proxy import (
    GITHUB,
    checked_download_env_async,
    pypi_target,
    redact_proxy_url,
)
from app.workspace.manager import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)

TICKTICK_MCP_REPO = 'https://github.com/jacepark12/ticktick-mcp.git'
TICKTICK_MCP_DIR = VIBE_SELLER_DIR / 'ticktick-mcp'

# Written last. Its presence is the only thing "installed" means for a
# fresh install.
INSTALL_MARKER = '.install-complete'

CLONE_TIMEOUT_S = 120.0
DEPS_TIMEOUT_S = 300.0

_ONE_AT_A_TIME = asyncio.Lock()


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


def _proxy_note(env: dict[str, str]) -> str:
    proxy = env.get('HTTPS_PROXY') or env.get('https_proxy')
    if proxy:
        return f' (through proxy {redact_proxy_url(proxy)})'
    return ' (no proxy configured)'


def _network_hint(env: dict[str, str]) -> str:
    if env.get('HTTPS_PROXY') or env.get('https_proxy'):
        return (
            'Check that the proxy can reach github.com and pypi.org, '
            'then try again.'
        )
    return (
        'GitHub may be unreachable from this network. Turn on the '
        'system proxy (or export HTTPS_PROXY before starting Vibe '
        'Seller), then try again.'
    )


def _kill(proc: asyncio.subprocess.Process) -> None:
    """Kill the child and what it spawned (git runs a remote helper)."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        if IS_WINDOWS:
            proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGKILL)


async def _run_step(
    argv: list[str], *, env: dict[str, str], timeout: float, what: str
) -> None:
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
        _kill(proc)
        await proc.wait()
        raise TickTickInstallError(
            f'{what} timed out after {int(timeout)}s{_proxy_note(env)}. '
            f'{_network_hint(env)}'
        ) from e
    except BaseException:
        # Cancelled (server shutdown, client gone): never orphan it.
        _kill(proc)
        await proc.wait()
        raise
    if proc.returncode != 0:
        tail = stderr.decode(errors='replace').strip()[-400:]
        raise TickTickInstallError(
            f'{what} failed{_proxy_note(env)}: {tail}. {_network_hint(env)}'
        )


async def _remove_tree() -> None:
    await asyncio.to_thread(shutil.rmtree, TICKTICK_MCP_DIR, True)


async def ensure_ticktick_mcp() -> str:
    """Clone and install ticktick-mcp if not present; return its path.

    Raises ``TickTickInstallError`` (a ``RuntimeError``) when a step
    fails or runs out of time; nothing is left behind in that case.
    """
    async with _ONE_AT_A_TIME:
        root = TICKTICK_MCP_DIR
        if ticktick_mcp_installed():
            return str(root)
        # A directory here that is not "installed" is an interrupted
        # run's leftover; cloning into it would fail on it.
        await _remove_tree()
        env = await checked_download_env_async((GITHUB, pypi_target()))
        # A credential prompt would wait on a terminal nobody has.
        env['GIT_TERMINAL_PROMPT'] = '0'
        venv = root / '.venv'
        try:
            logger.info('Cloning ticktick-mcp to %s', root)
            await _run_step(
                ['git', 'clone', '--depth', '1', TICKTICK_MCP_REPO, str(root)],
                env=env,
                timeout=CLONE_TIMEOUT_S,
                what='Downloading ticktick-mcp (git clone)',
            )
            await _run_step(
                ['uv', 'venv', str(venv)],
                env=env,
                timeout=DEPS_TIMEOUT_S,
                what='Creating the ticktick-mcp environment (uv venv)',
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
                env=env,
                timeout=DEPS_TIMEOUT_S,
                what='Installing ticktick-mcp dependencies (uv pip install)',
            )
            await asyncio.to_thread((root / INSTALL_MARKER).write_text, 'ok\n')
        except BaseException:
            await _remove_tree()
            raise
        return str(root)
