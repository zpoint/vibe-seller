"""Regression test for the Windows tray dialog.

Reproduces the bugs found in manual testing:
- the dialog was an unresponsive ctypes MessageBox (OK/close did
  nothing) — here we assert OK actually destroys the window;
- it lacked a Copy button per URL — we assert invoking each copies that
  value;
- it copied the IP URL by default — we assert the hostname URL is the
  default clipboard value.

Drives ``installer/windows/dialogs.build_dialog`` headlessly via
tkinter. Runs anywhere tkinter has a display (the Windows CI runner,
dev machines); skipped when headless.
"""

import contextlib
from pathlib import Path
import sys

import pytest

pytestmark = pytest.mark.unit

_WIN_DIR = Path(__file__).resolve().parents[2] / 'installer' / 'windows'


def _load_dialogs():
    tk = pytest.importorskip('tkinter')
    if str(_WIN_DIR) not in sys.path:
        sys.path.insert(0, str(_WIN_DIR))
    import dialogs  # noqa: PLC0415

    return tk, dialogs


def test_build_dialog_copy_and_close():
    tk, dialogs = _load_dialogs()
    # Create exactly ONE Tk root (build_dialog's) — a separate display
    # probe would be a second Tk instance, which hangs. Skip headless
    # by catching the TclError build_dialog raises with no display.
    try:
        root, h = dialogs.build_dialog(
            tk,
            'Test',
            'msg',
            copy_rows=[('ip', 'http://1.2.3.4:7777')],
            auto_copy='http://host.local:7777',
            copy_label='Copy',
            ok_label='OK',
        )
    except tk.TclError:
        pytest.skip('no display for tkinter')
    try:
        # Default clipboard value is the hostname URL (not the IP).
        assert root.clipboard_get() == 'http://host.local:7777'
        # The per-value Copy button copies that value.
        h['copy_buttons'][0].invoke()
        assert root.clipboard_get() == 'http://1.2.3.4:7777'
        # OK actually closes the window (the old MessageBox was
        # unresponsive — clicking OK did nothing). After destroy(), the
        # interpreter is gone, so any Tk call raises TclError.
        assert root.winfo_exists()
        h['ok_button'].invoke()
        with pytest.raises(tk.TclError):
            root.winfo_exists()
    finally:
        with contextlib.suppress(Exception):
            root.destroy()
