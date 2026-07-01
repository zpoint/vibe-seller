"""Tk dialogs + clipboard for the Windows tray.

Kept free of pystray/app imports so it's unit-testable with only
tkinter (see tests/unit/test_tray_dialog.py). The old tray used a
ctypes ``MessageBox`` invoked on pystray's callback thread, which was
unresponsive (OK/close did nothing) and couldn't host Copy buttons.
This builds a real, responsive tkinter window with a Copy button per
value, run on its own thread so it never blocks the tray loop.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import subprocess
import sys
import threading

logger = logging.getLogger('vibe-seller-tray')


def copy_to_clipboard_fallback(text: str) -> None:
    """Clipboard copy without tkinter (Windows ``clip``); no-op else."""
    if sys.platform != 'win32':
        return
    try:
        subprocess.run(['clip'], input=text, text=True, check=False, timeout=5)
    except (OSError, subprocess.SubprocessError):
        logger.warning('clipboard copy failed', exc_info=True)


def msgbox(title: str, text: str) -> None:
    """Fallback message box (only if tkinter is unavailable). On its own
    thread so it never blocks the caller's event loop."""
    if sys.platform == 'win32':
        threading.Thread(
            target=lambda: ctypes.windll.user32.MessageBoxW(
                0, text, title, 0x40
            ),
            daemon=True,
        ).start()
    else:
        logger.info('%s: %s', title, text)


def build_dialog(
    tk,
    title: str,
    message: str,
    copy_rows: list[tuple[str, str]] | None = None,
    auto_copy: str | None = None,
    copy_label: str = 'Copy',
    ok_label: str = 'OK',
):
    """Build (but do NOT run) the dialog; return ``(root, handles)``.

    Split from the thread/mainloop so a test can drive it headlessly.
    ``handles`` = ``{'copy_buttons': [...], 'ok_button': ...,
    'copy': fn}``. Each value row gets its own Copy button; ``auto_copy``
    is placed on the clipboard immediately.
    """
    root = tk.Tk()
    root.title(title)
    # Build hidden: clipboard/update work on a withdrawn window, and a
    # mapped top-most window can block on headless/virtual displays
    # (CI, some sandboxes). show_dialog() deiconifies before mainloop.
    root.withdraw()
    root.resizable(False, False)
    frm = tk.Frame(root, padx=18, pady=16)
    frm.pack(fill='both', expand=True)
    tk.Label(frm, text=message, justify='left').pack(anchor='w')

    def _copy(val: str) -> None:
        root.clipboard_clear()
        root.clipboard_append(val)
        root.update()

    copy_buttons = []
    for _label, val in copy_rows or []:
        row = tk.Frame(frm)
        row.pack(anchor='w', fill='x', pady=(8, 0))
        tk.Label(row, text=val, font=('Consolas', 10)).pack(side='left')
        btn = tk.Button(
            row, text=copy_label, width=8, command=lambda v=val: _copy(v)
        )
        btn.pack(side='right', padx=(12, 0))
        copy_buttons.append(btn)

    if auto_copy:
        _copy(auto_copy)
    ok = tk.Button(frm, text=ok_label, width=10, command=root.destroy)
    ok.pack(pady=(16, 0))
    root.update_idletasks()
    with contextlib.suppress(Exception):
        root.eval('tk::PlaceWindow . center')
    root.attributes('-topmost', True)
    return root, {'copy_buttons': copy_buttons, 'ok_button': ok, 'copy': _copy}


def show_dialog(
    tk,
    title: str,
    message: str,
    copy_rows: list[tuple[str, str]] | None = None,
    auto_copy: str | None = None,
    copy_label: str = 'Copy',
    ok_label: str = 'OK',
) -> None:
    """Show the dialog on its OWN thread (never blocks the tray loop).

    Falls back to a message box when ``tk`` is None (tkinter absent).
    """
    if tk is None:
        if auto_copy:
            copy_to_clipboard_fallback(auto_copy)
        msgbox(title, message)
        return

    def _run() -> None:
        try:
            root, _ = build_dialog(
                tk, title, message, copy_rows, auto_copy, copy_label, ok_label
            )
            root.deiconify()  # show it (build_dialog leaves it hidden)
            root.mainloop()
        except Exception:  # noqa: BLE001 — a dialog must never crash the tray
            logger.exception('dialog failed')
            if auto_copy:
                copy_to_clipboard_fallback(auto_copy)

    threading.Thread(target=_run, daemon=True).start()
