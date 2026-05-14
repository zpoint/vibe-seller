"""Tests for WorkspaceManager._ensure_venv bootstrapping.

Verifies that after venv creation, pip and uv are actually
usable inside the venv (not just that binaries exist).
"""

import asyncio
import os
import shutil

import pytest

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

        pip3 = tmp_path / '.venv' / 'bin' / 'pip3'
        assert pip3.exists(), 'pip3 binary missing'

        proc = await asyncio.create_subprocess_exec(
            str(pip3),
            '--version',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()
        assert proc.returncode == 0, 'pip3 --version failed'

    async def test_venv_has_working_uv(self, ws, tmp_path):
        """uv must be installed and runnable in the venv."""
        if not shutil.which('uv'):
            pytest.skip('uv not installed')
        await ws._ensure_venv()

        uv = tmp_path / '.venv' / 'bin' / 'uv'
        assert uv.exists(), 'uv binary missing'

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

        assert (tmp_path / '.venv' / 'bin' / 'pip3').exists()

    async def test_rebootstraps_existing_venv_missing_tools(self, ws, tmp_path):
        """If venv exists but pip/uv are missing, re-bootstrap."""
        if not shutil.which('uv'):
            pytest.skip('uv not installed')
        await ws._ensure_venv()

        # Simulate a pre-existing broken venv by removing tools
        venv_bin = tmp_path / '.venv' / 'bin'
        (venv_bin / 'pip3').unlink()
        (venv_bin / 'uv').unlink()

        # Should detect missing tools and re-bootstrap
        await ws._ensure_venv()

        assert (venv_bin / 'pip3').exists()
        assert (venv_bin / 'uv').exists()

    async def test_venv_has_working_python3(self, ws, tmp_path):
        """python3 must be runnable in the venv."""
        if not shutil.which('uv'):
            pytest.skip('uv not installed')
        await ws._ensure_venv()

        python3 = tmp_path / '.venv' / 'bin' / 'python3'
        assert python3.exists(), 'python3 binary missing'

        proc = await asyncio.create_subprocess_exec(
            str(python3),
            '--version',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        assert proc.returncode == 0, (
            f'python3 --version failed: {stderr.decode()}'
        )

    async def test_broken_venv_python_recreated(self, ws, tmp_path):
        """Broken python3 binary triggers venv recreation."""
        if not shutil.which('uv'):
            pytest.skip('uv not installed')
        await ws._ensure_venv()

        # Corrupt python3: replace with a 0-byte file
        python3 = tmp_path / '.venv' / 'bin' / 'python3'
        python_link = tmp_path / '.venv' / 'bin' / 'python'
        for p in (python3, python_link):
            if p.is_symlink() or p.exists():
                p.unlink()
            p.write_bytes(b'')
            p.chmod(0o755)

        # _ensure_venv should detect broken python and recreate
        await ws._ensure_venv()

        # python3 must now work
        proc = await asyncio.create_subprocess_exec(
            str(python3),
            '--version',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        assert proc.returncode == 0, (
            f'python3 still broken after recreation: {stderr.decode()}'
        )

    async def test_recreated_venv_uses_symlinks(self, ws, tmp_path):
        """Recreated venv should use symlinks, not copies."""
        if not shutil.which('uv'):
            pytest.skip('uv not installed')
        await ws._ensure_venv()

        # Corrupt python3
        python3 = tmp_path / '.venv' / 'bin' / 'python3'
        python_link = tmp_path / '.venv' / 'bin' / 'python'
        for p in (python3, python_link):
            if p.is_symlink() or p.exists():
                p.unlink()
            p.write_bytes(b'')
            p.chmod(0o755)

        # Recreate
        await ws._ensure_venv()

        # python and python3 should be symlinks (uv venv default)
        python_bin = tmp_path / '.venv' / 'bin' / 'python'
        python3_bin = tmp_path / '.venv' / 'bin' / 'python3'
        assert os.path.islink(str(python_bin)), (
            f'python is not a symlink: {python_bin}'
        )
        assert os.path.islink(str(python3_bin)), (
            f'python3 is not a symlink: {python3_bin}'
        )
