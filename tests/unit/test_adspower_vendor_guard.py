"""AdsPower's C3 guard declaration (R20): the private paths + port.

The task-agent guard (app/ai/vendor_guard.py) denies tool calls that
reach a vendor's private on-disk data. AdsPower's declaration — the
data dir name (every OS spelling) and the Windows profile cache — is
pinned here end-to-end: a Read/Bash into them is denied, and the
NEGATIVE CONTROL (the same spec with the declaration emptied) shows
the block exists ONLY because of the declaration.
"""

from dataclasses import replace

import pytest

import app.ai.vendor_guard as guard_mod
from app.ai.vendor_guard import (
    under_declared_private_path,
    vendor_guard_deny,
)
from app.browser.adapters.adspower_backend import ADSPOWER_VENDOR
from app.browser.vendors import BrowserVendorSpec

pytestmark = pytest.mark.unit

_TASK = '00000000-0000-0000-0000-000000000001'

# The recorded shapes (FACTS §k, §m6): the %APPDATA% data dir (the
# logs carry the API key in plain text), its macOS spelling, the
# Linux one, and the Windows profile cache with the cookie DBs —
# in the spellings the guard's matcher covers (every WSL /mnt/c and
# mid-path Windows form). NOTE (routed to the C3 owner, R20): the
# NATIVE drive-root-direct spelling `C:\.ADSPOWER_GLOBAL\...` is a
# shared-matcher gap (the separator before the name is consumed and
# the walk-back token `C:` is not an absolute start) — no vendor
# declaration can cover it; fix belongs in vendor_guard.py.
_PRIVATE_READS = [
    r'C:\Users\auser\AppData\Roaming\adspower_global\logs\main.log',
    '/Users/auser/Library/Application Support/adspower_global/'
    'cwd_global/source/local_api',
    '/home/auser/.config/adspower_global/cwd_global/lib/main.min.js',
    '/mnt/c/.ADSPOWER_GLOBAL/cache/kaaaaaa1_i7test/Default/Cookies',
    r'C:\Users\auser\.ADSPOWER_GLOBAL\cache\cookies.db',
]
_PRIVATE_CMDS = [
    'grep -rl apiKey ~/AppData/Roaming/adspower_global',
    'ls /mnt/c/Users/*/.ADSPOWER_GLOBAL/cache',
    'ls /mnt/c/.ADSPOWER_GLOBAL/cache',
]


def _with_spec(spec: BrowserVendorSpec):
    """Point the guard's matcher at ONE spec (rebuilt by id change)."""

    def fake_registry():
        return {'adspower': spec}

    return fake_registry


@pytest.mark.parametrize('path', _PRIVATE_READS)
def test_adspower_private_reads_are_under_a_declared_path(path):
    assert under_declared_private_path(path) is True


@pytest.mark.parametrize('path', _PRIVATE_READS)
def test_adspower_private_reads_are_denied(path):
    reason = vendor_guard_deny('Read', {'file_path': path}, task_id=_TASK)
    assert reason is not None
    assert 'adspower' in reason.lower()


@pytest.mark.parametrize('cmd', _PRIVATE_CMDS)
def test_adspower_private_commands_are_denied(cmd):
    reason = vendor_guard_deny('Bash', {'command': cmd}, task_id=_TASK)
    assert reason is not None
    assert 'adspower' in reason.lower()


@pytest.mark.parametrize('path', _PRIVATE_READS)
def test_negative_control_without_the_declaration(path, monkeypatch):
    """The same paths, the same spec with private_paths EMPTIED: the
    vendor-data block is gone (whatever else denies, it is not the
    AdsPower private-data rule) — the block exists only because of
    the declaration."""
    empty = replace(ADSPOWER_VENDOR, private_paths=(), local_ports=())
    monkeypatch.setattr(guard_mod, 'registered_vendor_specs', _with_spec(empty))
    monkeypatch.setattr(guard_mod, '_MATCHER', None)
    assert under_declared_private_path(path) is False


def test_the_declared_port_is_the_documented_default():
    # The static tuple can only carry the documented default; the
    # real port is random per launch (see the spec's comment).
    assert ADSPOWER_VENDOR.local_ports == (50325,)


def test_the_declaration_names_only_distinctive_dirs():
    # No generic names: every entry is AdsPower's own spelling.
    for entry in ADSPOWER_VENDOR.private_paths:
        assert 'adspower' in entry.lower()
