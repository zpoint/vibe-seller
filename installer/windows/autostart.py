"""The tray's start-at-login switch: re-exports app/windows_autostart.py.

The logic lives in the app package so the tray and
``vibe-seller autostart`` share one implementation (the tray already
imports ``app`` from the install's venv). This file stays because the
installer stages it next to tray.py, which imports it by this name.
"""

from app.windows_autostart import (
    _FOLDERID_STARTUP,  # noqa: PLC2701 — the real-Windows tests use it
    APPROVED_KEY,
    LNK_NAME,
    AutoStart,
    AutoStartError,
    _sh_get_known_folder,  # noqa: PLC2701 — the real-Windows tests use it
    encode_approval,
    is_approved,
    startup_dir,
)

__all__ = [
    'APPROVED_KEY',
    'LNK_NAME',
    '_FOLDERID_STARTUP',
    'AutoStart',
    'AutoStartError',
    '_sh_get_known_folder',
    'encode_approval',
    'is_approved',
    'startup_dir',
]
