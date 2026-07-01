"""Agent-venv bootstrap helpers.

Extracted from ``WorkspaceManager`` so the venv-creation cluster (tool
checks + pip/uv bootstrap) lives in its own focused module rather than
inflating ``manager.py``. Behaviour is unchanged — these were
``@staticmethod``s taking ``venv_dir`` and are now plain module
functions. Tests exercise them through ``WorkspaceManager._ensure_venv``
(see ``tests/unit/test_workspace/test_venv_bootstrap.py``).
"""

import asyncio
import contextlib
import logging
from pathlib import Path

from app.platform import venv_executable, venv_python

logger = logging.getLogger(__name__)


async def venv_tools_ok(venv_dir: Path) -> bool:
    """Check python runs and pip/uv exist in the venv."""
    pip = venv_executable(venv_dir, 'pip')
    uv = venv_executable(venv_dir, 'uv')
    if not (pip.exists() and uv.exists()):
        return False
    return await python_runnable(venv_dir)


async def python_runnable(venv_dir: Path) -> bool:
    """Check if python in the venv is executable."""
    py = venv_python(venv_dir)
    if not py.exists():
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            str(py),
            '--version',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=2)
        return proc.returncode == 0
    except TimeoutError:
        # Kill the hung process to avoid leaking it.
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        return False
    except OSError:
        return False


async def bootstrap_venv_tools(venv_dir: Path) -> None:
    """Install pip and uv into the venv."""
    python = str(venv_python(venv_dir))
    proc = await asyncio.create_subprocess_exec(
        'uv',
        'pip',
        'install',
        'pip',
        'uv',
        '--reinstall',
        '--python',
        python,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr_bytes = await proc.communicate()
    if proc.returncode != 0:
        stderr = stderr_bytes.decode() if stderr_bytes else ''
        logger.warning(
            'Failed to bootstrap pip/uv in venv: %s',
            stderr,
        )
    else:
        logger.info('Bootstrapped pip and uv into agent venv')
