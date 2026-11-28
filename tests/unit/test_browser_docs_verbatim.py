"""The error tables and error Q&As quote the UI strings VERBATIM.

Every first cell of the browsers / ziniao / yuehulian error tables, and
every ``error-*`` quick-reference summary on the browsers pages, must
be a string the app actually shows — either a value from the page's
locale section (``settings.ziniao*``, ``settings.yuehulian*``, the
shared ``browserVendor`` copy) or a prefix of a ``tasks.error_*``
banner string. A paraphrased hint teaches the user to look for words
the app never shows.

Model: ``tests/unit/test_adspower_docs_verbatim.py``.
"""

import json
from pathlib import Path
import re

import pytest

pytestmark = pytest.mark.unit

_DOCS = Path(__file__).resolve().parents[2] / 'docs'
_ROOT = Path(__file__).resolve().parents[2]
_LOCALES = _ROOT / 'frontend/src/i18n/locales'


def _locale_values(locale: str, *sections: str) -> list[str]:
    """Every string under the given top-level locale sections."""
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

    for section in sections:
        walk(data.get(section, {}))
    return out


def _error_table_cells(page: Path, header_word: str) -> list[str]:
    """The first cell of every row of the bottom error table."""
    text = page.read_text(encoding='utf-8')
    # The error table follows the <details> entries; take the LAST
    # table of the page.
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


def _browsers_error_summaries(page: Path) -> list[str]:
    """The bolded summary of every error-* quick-reference entry."""
    text = page.read_text(encoding='utf-8')
    entries = re.findall(
        r'<details[^>]*id="(error-[^"]+)"[^>]*>\s*<summary><b>(.*?)</b>',
        text,
    )
    assert len(entries) >= 7, f'{page.name}: expected 7 error-* entries'
    return [summary.strip('「」""') for _, summary in entries]


@pytest.mark.parametrize(
    'locale,page',
    [('zh', 'ziniao.md'), ('en', 'ziniao.en.md')],
)
def test_ziniao_error_table_quotes_the_ui_strings_verbatim(locale, page):
    header = '界面提示' if locale == 'zh' else 'Message'
    cells = _error_table_cells(_DOCS / page, header)
    assert cells, f'{page}: the error table is empty'
    sources = _locale_values(locale, 'settings', 'tasks') + _locale_values(
        locale, 'tasks'
    )
    # The settings section also holds the ziniao strings; the shared
    # platform copy lives under browserVendor.
    sources += _locale_values(locale, 'browserVendor')
    for cell in cells:
        assert any(cell in source for source in sources), (
            f'{page}: the table cell {cell[:50]!r} is not a verbatim UI '
            f'string — copy it from the {locale} translation '
            '(settings.ziniao*, tasks.error_ziniao_* or browserVendor)'
        )


@pytest.mark.parametrize(
    'locale,page',
    [('zh', 'yuehulian.md'), ('en', 'yuehulian.en.md')],
)
def test_yuehulian_error_table_quotes_the_ui_strings_verbatim(locale, page):
    header = '界面提示' if locale == 'zh' else 'Message'
    cells = _error_table_cells(_DOCS / page, header)
    assert cells, f'{page}: the error table is empty'
    sources = _locale_values(locale, 'settings') + _locale_values(
        locale, 'browserVendor'
    )
    for cell in cells:
        assert any(cell in source for source in sources), (
            f'{page}: the table cell {cell[:50]!r} is not a verbatim UI '
            f'string — copy it from the {locale} translation '
            '(settings.yuehulian* or the shared browserVendor copy)'
        )


@pytest.mark.parametrize(
    'locale,page',
    [('zh', 'browsers.md'), ('en', 'browsers.en.md')],
)
def test_browsers_error_summaries_quote_the_banner_strings(locale, page):
    """Each quick-reference summary is the first clause of a
    ``tasks.error_*`` banner string — the part the user matches on."""
    summaries = _browsers_error_summaries(_DOCS / page)
    banner_values = [
        v for k, v in _flat_tasks(locale).items() if k.startswith('error_')
    ]
    for summary in summaries:
        assert any(v.startswith(summary) for v in banner_values), (
            f'{page}: the summary {summary[:50]!r} is not the start of '
            'any tasks.error_* banner string in the '
            f'{locale} translation'
        )


def _flat_tasks(locale: str) -> dict[str, str]:
    data = json.loads(
        (_LOCALES / locale / 'translation.json').read_text(encoding='utf-8')
    )
    tasks = data.get('tasks', {})
    return {k: v for k, v in tasks.items() if isinstance(v, str)}


def test_negative_control_a_paraphrased_cell_is_rejected():
    """The verbatim checker actually rejects a paraphrase: a cell the
    locale never says must fail the same containment the tests above
    assert (proves they fail, not silently pass, on a paraphrase)."""
    sources = _locale_values('zh', 'settings') + _locale_values(
        'zh', 'browserVendor'
    )
    paraphrase = '浏览器客户端没有启动，请打开后重试'
    assert not any(paraphrase in source for source in sources), (
        'the paraphrase unexpectedly IS a locale string — pick another'
    )
    # The real string it paraphrases:
    assert any(
        '浏览器客户端没有在运行' in source
        for source in _locale_values('zh', 'tasks')
    )
