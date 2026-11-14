"""Shared fixtures for the unit tier."""

from __future__ import annotations

import pytest

from app import bundle
import app.pip_update as pu


@pytest.fixture(name='bundle_home')
def _bundle_home(tmp_path, monkeypatch):
    """A Windows-shaped installer bundle: {app} with .venv, python, uv."""
    app = tmp_path / 'app'
    venv = app / '.venv'
    (venv / 'Scripts').mkdir(parents=True)
    (app / 'python').mkdir()
    (app / 'python' / 'python.exe').write_text('')
    # The wheel set the installer laid down: a second LOCAL source both
    # the apply and its rollback may resolve from.
    (app / 'wheels').mkdir()
    (app / 'uv.exe').write_text('')
    (app / 'unins000.exe').write_text('')
    monkeypatch.setattr(pu.sys, 'platform', 'win32')
    monkeypatch.setattr(pu.sys, 'prefix', str(venv))
    monkeypatch.setattr(
        pu.sys, 'executable', str(venv / 'Scripts' / 'python.exe')
    )
    monkeypatch.setattr(
        pu.sys, '_base_executable', str(app / 'python' / 'python.exe')
    )
    monkeypatch.setattr(bundle, 'app_root', lambda: app)
    return app
