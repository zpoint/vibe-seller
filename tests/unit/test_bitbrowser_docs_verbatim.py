"""The error table's first column quotes the UI strings VERBATIM.

Every first cell of docs/bitbrowser.md's (and .en.md's) bottom error
table must occur verbatim in the locale's ``vendors.bitbrowser.*`` or
shared ``browserVendor.*`` values, or in the adapter's message
constants. A paraphrased hint teaches the user to look for words the app
never shows.
"""

import json
from pathlib import Path
import re

import pytest

from app.browser.adapters import bitbrowser as bb

pytestmark = pytest.mark.unit

_DOCS = Path(__file__).resolve().parents[2] / 'docs'
_LOCALES = Path(__file__).resolve().parents[2] / 'frontend/src/i18n/locales'


def _error_table_cells(page: Path, header_word: str) -> list[str]:
    """The first cell of every row of the bottom error table."""
    text = page.read_text(encoding='utf-8')
    tables = re.findall(r'(?:^\|.*\n)+', text, re.MULTILINE)
    assert tables, f'{page.name}: no table found'
    rows = [r for r in tables[-1].strip().split('\n') if r.startswith('|')]
    assert header_word in rows[0], f'{page.name}: unexpected header {rows[0]}'
    cells = []
    for row in rows[2:]:
        cell = row.split('|')[1].strip()
        if cell:
            cells.append(cell)
    return cells


def _locale_values(locale: str) -> list[str]:
    data = json.loads(
        (_LOCALES / locale / 'translation.json').read_text(encoding='utf-8')
    )
    out = []

    def walk(node):
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, str):
            out.append(node)

    for key, value in data.get('vendors', {}).items():
        if key.startswith(bb.BITBROWSER_VENDOR.key):
            walk(value)
    walk(data.get('browserVendor', {}))
    display = (
        bb.BITBROWSER_VENDOR.display_name_zh
        if locale == 'zh'
        else bb.BITBROWSER_VENDOR.display_name_en
    )
    return [value.replace('{{vendor}}', display) for value in out]


def _constant_values() -> list[str]:
    return [
        value
        for key, value in vars(bb).items()
        if isinstance(value, str) and key.isupper()
    ]


def _sources(locale: str) -> list[str]:
    return _locale_values(locale) + _constant_values()


def _assert_verbatim(cells: list[str], sources: list[str], page: str) -> None:
    for cell in cells:
        assert any(cell in source for source in sources), (
            f'{page}: {cell[:60]!r} is not a verbatim UI string — copy it '
            'from the locale or the adapter message constants'
        )


@pytest.mark.parametrize(
    'locale,page', [('zh', 'bitbrowser.md'), ('en', 'bitbrowser.en.md')]
)
def test_error_table_quotes_the_ui_strings_verbatim(locale, page):
    header = '界面提示' if locale == 'zh' else 'Message'
    cells = _error_table_cells(_DOCS / page, header)
    assert cells, f'{page}: the error table is empty'
    _assert_verbatim(cells, _sources(locale), page)


def test_verbatim_check_negative_control_catches_a_paraphrase():
    """A plausible but non-UI sentence must fail the same check."""
    with pytest.raises(AssertionError, match='not a verbatim UI string'):
        _assert_verbatim(
            ['Please sign in to your browser and try again later'],
            _sources('en'),
            'negative-control',
        )
