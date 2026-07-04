#!/usr/bin/env python3
"""Render an ad-audit ``AD_AUDIT_<date>.md`` to a CJK-ready PDF next to it.

Pipeline: python-markdown (tables ext) -> styled HTML -> headless Chrome
``--print-to-pdf``. A throwaway profile in a temp dir is used, so this is
safe to run while the store browser is up (no session is touched).

Usage:
  python3 md_to_pdf.py AD_AUDIT_2026-06-05.md [-o OUT.pdf]

Chrome discovery order: $AUDIT_PDF_CHROME, google-chrome / chromium(-browser)
on PATH, then the playwright/puppeteer caches (mac + linux).
Exit 0 on success (prints the pdf path), 1 on failure.
"""

import argparse
import glob
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

try:
    import markdown
except ImportError:
    print('ERROR: python-markdown missing (pip install markdown)')
    sys.exit(1)

_LIST_START = re.compile(r'^ {0,3}([-*+]|\d+\.) ')
_INDENTED = re.compile(r'^ {2,}')


def normalize_md(text: str) -> str:
    """Insert the blank line python-markdown needs before a list.

    GFM renderers (the web UI, GitHub) tolerate a list that starts
    directly under a paragraph line; python-markdown does not — it folds
    the items into the paragraph and the PDF shows a wall of run-on text.
    Normalizing here makes the sanctioned renderer immune to that
    authoring slip instead of hoping every agent remembers the blank line.
    """
    out: list[str] = []
    prev = ''
    for line in text.splitlines():
        starts_list = bool(_LIST_START.match(line))
        prev_is_listish = bool(_LIST_START.match(prev) or _INDENTED.match(prev))
        if starts_list and prev.strip() and not prev_is_listish:
            out.append('')
        out.append(line)
        prev = line
    return '\n'.join(out)


_CSS = """
@page { size: A4; margin: 18mm 14mm; }
body { font-family: 'PingFang SC', 'Noto Sans SC', 'DengXian',
       'Microsoft JhengHei', sans-serif; font-size: 11px;
       line-height: 1.5; color: #222; }
h1 { font-size: 17px; border-bottom: 2px solid #444; padding-bottom: 4px; }
h2 { font-size: 14px; margin-top: 18px; border-bottom: 1px solid #999;
     padding-bottom: 2px; }
h3 { font-size: 12px; margin-top: 12px; }
table { border-collapse: collapse; width: 100%; margin: 6px 0; }
/* Long tables MUST break across pages; repeat the header row on each. */
thead { display: table-header-group; }
tr { page-break-inside: avoid; }
th, td { border: 1px solid #bbb; padding: 3px 5px; text-align: left;
         word-break: break-all; }
th { background: #f0f0f0; }
code { background: #f5f5f5; padding: 0 3px; font-size: 10px; }
pre { background: #f5f5f5; padding: 6px; overflow-x: hidden;
      white-space: pre-wrap; font-size: 10px; }
blockquote { border-left: 3px solid #ccc; margin-left: 0;
             padding-left: 10px; color: #555; }
"""


def find_chrome():
    """Locate a Chrome/Chromium binary across Linux and macOS."""
    if os.environ.get('AUDIT_PDF_CHROME'):
        return os.environ['AUDIT_PDF_CHROME']
    for name in ('google-chrome', 'chromium', 'chromium-browser'):
        path = shutil.which(name)
        if path:
            return path
    home = os.path.expanduser('~')
    mac_app = '/Contents/MacOS/Google Chrome for Testing'
    for pat in (
        # Linux caches
        f'{home}/.cache/ms-playwright/chromium-*/chrome-linux64/chrome',
        f'{home}/.cache/puppeteer/chrome/*/chrome-linux64/chrome',
        f'{home}/.cache/puppeteer/chrome-headless-shell/*/'
        'chrome-headless-shell-linux64/chrome-headless-shell',
        # macOS — headless-shell first (purpose-built for printing, never
        # conflicts with a running store browser).
        f'{home}/Library/Caches/ms-playwright/chromium_headless_shell-*/'
        'chrome-headless-shell-mac-arm64/chrome-headless-shell',
        f'{home}/Library/Caches/ms-playwright/chromium_headless_shell-*/'
        'chrome-headless-shell-mac-x64/chrome-headless-shell',
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        '/Applications/Chromium.app/Contents/MacOS/Chromium',
        # Chrome for Testing LAST: hangs in headless print while the same
        # binary runs the store browser.
        f'{home}/Library/Caches/ms-playwright/chromium-*/'
        f'chrome-mac-arm64/Google Chrome for Testing.app{mac_app}',
        f'{home}/Library/Caches/ms-playwright/chromium-*/'
        f'chrome-mac/Google Chrome for Testing.app{mac_app}',
    ):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None


def headless_flag():
    """``--headless=new`` hangs >120 s on macOS Chrome; legacy
    ``--headless`` prints fine there. Linux keeps ``=new``."""
    return '--headless' if sys.platform == 'darwin' else '--headless=new'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('audit_md')
    ap.add_argument('-o', '--output')
    args = ap.parse_args(argv)

    src = pathlib.Path(args.audit_md)
    if not src.is_file():
        print(f'ERROR: {src} not found')
        return 1
    out = pathlib.Path(args.output) if args.output else src.with_suffix('.pdf')

    chrome = find_chrome()
    if not chrome:
        print('ERROR: no chrome/chromium binary found (set AUDIT_PDF_CHROME)')
        return 1

    body = markdown.markdown(
        normalize_md(src.read_text(encoding='utf-8')),
        extensions=['tables', 'fenced_code'],
    )
    html = (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        f'<style>{_CSS}</style></head><body>{body}</body></html>'
    )

    with tempfile.TemporaryDirectory() as tmp:
        html_path = pathlib.Path(tmp) / 'audit.html'
        html_path.write_text(html, encoding='utf-8')
        cmd = [
            chrome,
            headless_flag(),
            '--disable-gpu',
            '--no-sandbox',
            f'--user-data-dir={tmp}/profile',
            f'--print-to-pdf={out.resolve()}',
            '--no-pdf-header-footer',
            html_path.resolve().as_uri(),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0 or not out.is_file():
        print(f'ERROR: chrome print failed rc={proc.returncode}')
        print(proc.stderr[-500:])
        return 1
    # Sanity gate: a real multi-section audit never prints to a single
    # page. A 1-page PDF means the HTML failed to render (e.g. raw
    # markdown) — fail loudly instead of shipping a broken file.
    raw = out.read_bytes()
    pages = raw.count(b'/Type /Page') - raw.count(b'/Type /Pages')
    if pages < 1:
        print('ERROR: output PDF has no pages — rendering failed')
        return 1
    print(f'wrote {out} ({out.stat().st_size} bytes, ~{pages} pages)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
