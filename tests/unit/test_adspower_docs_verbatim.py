"""The error table's 界面提示 column quotes the UI strings VERBATIM (R29).

Every first cell of docs/adspower.md's (and .en.md's) error table must
occur verbatim in the locale's ``vendors.adspower.*`` values, in
``adspower_common``'s message constants, or — for rows the SHARED
generic copy answers (the platform row) — in the shared
``browserVendor.*`` values. A paraphrased hint teaches the user to
look for words the app never shows.
"""

import json
from pathlib import Path
import re

import pytest

from app.browser.adapters.adspower_backend import ADSPOWER_VENDOR
import app.browser.adapters.adspower_common as common

pytestmark = pytest.mark.unit

_DOCS = Path(__file__).resolve().parents[2] / 'docs'
_LOCALES = Path(__file__).resolve().parents[2] / ('frontend/src/i18n/locales')


def _error_table_cells(page: Path, header_word: str) -> list[str]:
    """The first cell of every row of the bottom error table."""
    text = page.read_text(encoding='utf-8')
    # The table sits under the Q&A heading; take the LAST table of the
    # page (the error table follows the <details> entries).
    tables = re.findall(r'(?:^\|.*\n)+', text, re.MULTILINE)
    assert tables, f'{page.name}: no table found'
    table = tables[-1]
    rows = [r for r in table.strip().split('\n') if r.startswith('|')]
    assert header_word in rows[0], f'{page.name}: unexpected header {rows[0]}'
    cells = []
    for row in rows[2:]:  # skip the header and the |---| separator
        cell = row.split('|')[1].strip()
        if cell:
            cells.append(cell)
    return cells


def _vendor_values(locale: str) -> list[str]:
    data = json.loads(
        (_LOCALES / locale / 'translation.json').read_text(encoding='utf-8')
    )
    out = []

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, str):
            out.append(node)

    walk(data.get('vendors', {}).get(ADSPOWER_VENDOR.key, {}))
    # The shared copy answers rows the vendor does not override (the
    # platform row): allow those values too, with {{vendor}} filled.
    shared = data.get('browserVendor', {})
    walk(shared)
    return [v.replace('{{vendor}}', 'AdsPower') for v in out]


def _constant_values() -> list[str]:
    return [
        v for k, v in vars(common).items() if isinstance(v, str) and k.isupper()
    ]


@pytest.mark.parametrize(
    'locale,page', [('zh', 'adspower.md'), ('en', 'adspower.en.md')]
)
def test_error_table_quotes_the_ui_strings_verbatim(locale, page):
    header = '界面提示' if locale == 'zh' else 'Message'
    cells = _error_table_cells(_DOCS / page, header)
    assert cells, f'{page}: the error table is empty'
    sources = (
        _vendor_values(locale)
        + _constant_values()
        # the shared locale values (platform row etc.)
        + _vendor_values(locale)
    )
    for cell in cells:
        assert any(cell in source for source in sources), (
            f'{page}: the table cell {cell[:50]!r} is not a verbatim UI '
            'string — copy it from vendors.adspower.* in the '
            f'{locale} translation, adspower_common, or the shared '
            'browserVendor copy'
        )
