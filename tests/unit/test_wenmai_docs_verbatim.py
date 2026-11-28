"""The error table's 界面提示 column quotes the UI strings VERBATIM.

Every first cell of docs/wenmai.md's (and .en.md's) bottom error table
must occur verbatim in the locale's ``settings.wenmai*`` values or in
the shared ``browserVendor.*`` values with ``{{vendor}}`` filled as
稳卖 / Wenmai (the form and task sentences — wenmai defines no vendor
overrides in ``vendors.wenmai``, so the shared copy is what the app
renders). A paraphrased hint teaches the user to look for words the
app never shows.
"""

import json
from pathlib import Path
import re

import pytest

pytestmark = pytest.mark.unit

_DOCS = Path(__file__).resolve().parents[2] / 'docs'
_LOCALES = Path(__file__).resolve().parents[2] / 'frontend/src/i18n/locales'
_VENDOR_DISPLAY = {'zh': '稳卖', 'en': 'Wenmai'}


def _error_table_cells(page: Path, header_word: str) -> list[str]:
    """The first cell of every row of the bottom error table."""
    text = page.read_text(encoding='utf-8')
    # The error table sits under the Q&A heading, AFTER the
    # <details> entries; take the LAST table of the page.
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


def _ui_values(locale: str) -> list[str]:
    """Every string the app can render for wenmai in this locale.

    The vendor's own keys (``settings.wenmai*``) plus the shared
    ``browserVendor.*`` copy (form, task and unavailable sentences)
    with ``{{vendor}}`` interpolated as the display name.
    """
    data = json.loads(
        (_LOCALES / locale / 'translation.json').read_text(encoding='utf-8')
    )
    out: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, str):
            out.append(node)

    walk(data.get('settings', {}))
    walk(data.get('browserVendor', {}))
    vendor = _VENDOR_DISPLAY[locale]
    return [v.replace('{{vendor}}', vendor) for v in out]


@pytest.mark.parametrize(
    'locale,page', [('zh', 'wenmai.md'), ('en', 'wenmai.en.md')]
)
def test_error_table_quotes_the_ui_strings_verbatim(locale, page):
    header = '界面提示' if locale == 'zh' else 'Message'
    cells = _error_table_cells(_DOCS / page, header)
    assert cells, f'{page}: the error table is empty'
    sources = _ui_values(locale)
    for cell in cells:
        assert any(cell in source for source in sources), (
            f'{page}: the table cell {cell[:50]!r} is not a verbatim UI '
            f'string — copy it from settings.wenmai* or browserVendor.* '
            f'in the {locale} translation'
        )


def test_the_checker_has_teeth():
    """Negative control: a plausible-but-paraphrased cell is REJECTED.

    The old docs quoted the yuehulian-style wording for the
    one-account row — words the wenmai UI never shows. The checker
    must refuse exactly that class of drift.
    """
    sources = _ui_values('zh')
    paraphrase = '稳卖一台机器只支持一个账号，请先在设置页面删除所有稳卖店铺，再添加新账号。'
    assert not any(paraphrase in source for source in sources), (
        'the paraphrase unexpectedly matches a UI string — the negative '
        'control is invalid'
    )
    real = '这个浏览器一台机器只能保存一个账号。要先删除用它创建的所有店铺，才能更换账号。'
    assert any(real in source for source in sources)
