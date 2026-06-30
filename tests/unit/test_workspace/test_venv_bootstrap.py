"""Tests for WorkspaceManager._ensure_venv bootstrapping.

Verifies that after venv creation, pip and uv are actually
usable inside the venv (not just that binaries exist).
"""

import asyncio
import os
import shutil

import pytest

from app.platform import venv_bin_dir, venv_executable, venv_python
from app.workspace.manager import WorkspaceManager


@pytest.fixture
def ws(tmp_path):
    """WorkspaceManager rooted at a temp directory."""
    return WorkspaceManager(root=tmp_path)


@pytest.mark.unit
class TestEnsureVenv:
    async def test_venv_has_working_pip(self, ws, tmp_path):
        """pip must be importable and pip3 must run."""
        if not shutil.which('uv'):
            pytest.skip('uv not installed')
        await ws._ensure_venv()

        pip = venv_executable(tmp_path / '.venv', 'pip')
        assert pip.exists(), f'pip binary missing at {pip}'

        proc = await asyncio.create_subprocess_exec(
            str(pip),
            '--version',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()
        assert proc.returncode == 0, 'pip --version failed'

    async def test_venv_has_working_uv(self, ws, tmp_path):
        """uv must be installed and runnable in the venv."""
        if not shutil.which('uv'):
            pytest.skip('uv not installed')
        await ws._ensure_venv()

        uv = venv_executable(tmp_path / '.venv', 'uv')
        assert uv.exists(), f'uv binary missing at {uv}'

        proc = await asyncio.create_subprocess_exec(
            str(uv),
            '--version',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()
        assert proc.returncode == 0, 'uv --version failed'

    async def test_venv_idempotent(self, ws, tmp_path):
        """Calling _ensure_venv twice must not error."""
        if not shutil.which('uv'):
            pytest.skip('uv not installed')
        await ws._ensure_venv()
        await ws._ensure_venv()  # second call is a no-op

        pip = venv_executable(tmp_path / '.venv', 'pip')
        assert pip.exists()

    async def test_rebootstraps_existing_venv_missing_tools(self, ws, tmp_path):
        """If venv exists but pip/uv are missing, re-bootstrap."""
        if not shutil.which('uv'):
            pytest.skip('uv not installed')
        await ws._ensure_venv()

        # Simulate a pre-existing broken venv by removing tools
        pip = venv_executable(tmp_path / '.venv', 'pip')
        uv = venv_executable(tmp_path / '.venv', 'uv')
        pip.unlink()
        uv.unlink()

        # Should detect missing tools and re-bootstrap
        await ws._ensure_venv()

        assert pip.exists()
        assert uv.exists()

    async def test_venv_has_working_python(self, ws, tmp_path):
        """python must be runnable in the venv."""
        if not shutil.which('uv'):
            pytest.skip('uv not installed')
        await ws._ensure_venv()

        py = venv_python(tmp_path / '.venv')
        assert py.exists(), f'python binary missing at {py}'

        proc = await asyncio.create_subprocess_exec(
            str(py),
            '--version',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        assert proc.returncode == 0, (
            f'python --version failed: {stderr.decode()}'
        )

    async def test_broken_venv_python_recreated(self, ws, tmp_path):
        """Broken python binary triggers venv recreation."""
        if not shutil.which('uv'):
            pytest.skip('uv not installed')
        await ws._ensure_venv()

        # Corrupt python: replace with a 0-byte file
        py = venv_python(tmp_path / '.venv')
        venv_bin = venv_bin_dir(tmp_path / '.venv')
        python_link = venv_bin / 'python'
        for p in (py, python_link):
            if p.is_symlink() or p.exists():
                p.unlink()
            p.write_bytes(b'')
            p.chmod(0o755)

        # _ensure_venv should detect broken python and recreate
        await ws._ensure_venv()

        # python must now work
        proc = await asyncio.create_subprocess_exec(
            str(py),
            '--version',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        assert proc.returncode == 0, (
            f'python still broken after recreation: {stderr.decode()}'
        )

    async def test_recreated_venv_uses_symlinks(self, ws, tmp_path):
        """Recreated venv should use symlinks, not copies."""
        if not shutil.which('uv'):
            pytest.skip('uv not installed')
        if os.name == 'nt':
            pytest.skip('Windows venvs use copies, not symlinks')
        await ws._ensure_venv()

        # Corrupt python
        venv_bin = venv_bin_dir(tmp_path / '.venv')
        py = venv_python(tmp_path / '.venv')
        python_link = venv_bin / 'python'
        for p in (py, python_link):
            if p.is_symlink() or p.exists():
                p.unlink()
            p.write_bytes(b'')
            p.chmod(0o755)

        # Recreate
        await ws._ensure_venv()

        # python and python3 should be symlinks (uv venv default)
        python_bin = venv_bin / 'python'
        python3_bin = venv_bin / 'python3'
        assert os.path.islink(str(python_bin)), (
            f'python is not a symlink: {python_bin}'
        )
        assert os.path.islink(str(python3_bin)), (
            f'python3 is not a symlink: {python3_bin}'
        )
