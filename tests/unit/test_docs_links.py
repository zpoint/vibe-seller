"""Every docs link the app shows lands on a section that exists.

An error in the app links the docs section that says how to fix it
(``frontend/src/lib/docsHelp.ts``; AI presets and a few other places
link the docs too). The docs site addresses a section by its heading's
text, so renaming a heading — or moving an FAQ entry — silently turns
such a link into "the top of some page". This test reads every
``https://docs.vibe-sellers.com/...`` link in ``app/`` and
``frontend/src/`` and checks it against ``docs/``: the page must exist
in the link's language, and its ``#anchor`` must be a heading's anchor
or an explicit ``id`` on that page.

Anchors are computed the way the site computes them (mkdocs.yml: toc
with ``pymdownx.slugs.slugify(case='lower')``), so the link written in
the app is exactly the one a reader's browser follows.
"""

from pathlib import Path
import re
import unicodedata
from urllib.parse import unquote

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / 'docs'
SITE = 'https://docs.vibe-sellers.com/'

# A docs link as source code writes it: stops at a quote, backtick,
# whitespace or closing bracket.
_LINK = re.compile(r"https://docs\.vibe-sellers\.com/[^\s'\"`)<>\]]*")

_FENCE = re.compile(r'^\s*(```|~~~)')
_HEADING = re.compile(r'^\s*(#{1,6})\s+(.+?)\s*#*\s*$')
_HEADING_ID = re.compile(r'\s*\{:?\s*#([\w-]+)[^}]*\}\s*$')
_HTML_ID = re.compile(r'\bid="([^"]+)"')
_ATTR_ID = re.compile(r'\{:?\s*#([\w-]+)[^}]*\}')
_MD_LINK = re.compile(r'\[([^\]]*)\]\([^)]*\)')

# pymdownx.slugs._uslugify(case='lower'), which mkdocs.yml configures.
_TAGS = re.compile(r'</?[^>]*>')
_INVALID = re.compile(r'[^\w\- ]')


def _slug(text: str) -> str:
    """A heading's anchor, as the docs site makes it."""
    # toc slugifies the heading's rendered text: markup is gone.
    text = _MD_LINK.sub(r'\1', text).replace('`', '').replace('*', '')
    slug = _TAGS.sub('', unicodedata.normalize('NFC', text)).strip().lower()
    return _INVALID.sub('', slug).replace(' ', '-')


def _anchors(page: Path) -> set[str]:
    """Every anchor a link can land on in *page*."""
    anchors: set[str] = set()
    in_fence = False
    for line in page.read_text(encoding='utf-8').splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        anchors.update(_HTML_ID.findall(line))
        heading = _HEADING.match(line)
        if heading:
            text = heading.group(2)
            explicit = _HEADING_ID.search(text)
            anchors.add(explicit.group(1) if explicit else _slug(text))
        else:
            anchors.update(_ATTR_ID.findall(line))
    return anchors


def _source(url: str) -> tuple[Path, str]:
    """The docs source file a link renders from, and its anchor."""
    path, _, anchor = url[len(SITE) :].partition('#')
    path = path.strip('/')
    lang = 'zh'
    if path == 'en' or path.startswith('en/'):
        lang, path = 'en', path[3:]
    page = path or 'index'
    suffix = '.en.md' if lang == 'en' else '.md'
    return DOCS / f'{page}{suffix}', unquote(anchor)


def _app_links() -> dict[str, list[str]]:
    """Each docs link in the app, with the files that contain it."""
    found: dict[str, list[str]] = {}
    sources = [
        *ROOT.joinpath('frontend', 'src').rglob('*.ts'),
        *ROOT.joinpath('frontend', 'src').rglob('*.tsx'),
        *ROOT.joinpath('app').rglob('*.py'),
        *ROOT.joinpath('app').rglob('*.md'),
    ]
    for f in sources:
        if '__tests__' in f.parts:
            continue
        for url in _LINK.findall(f.read_text(encoding='utf-8')):
            found.setdefault(url, []).append(str(f.relative_to(ROOT)))
    return found


@pytest.mark.unit
def test_slug_matches_the_site_for_known_headings():
    # Anchors docs pages already link to each other with.
    assert _slug(
        'Windows（WSL）：让紫鸟进入开发者模式（ziniao_webdriver.bat）'
    ) == ('windowswsl让紫鸟进入开发者模式ziniao_webdriverbat')
    assert _slug('"无权限通过 webdriver 登录" when binding a store') == (
        '无权限通过-webdriver-登录-when-binding-a-store'
    )
    assert _slug('常见提示 / 报错') == '常见提示--报错'


@pytest.mark.unit
def test_the_app_has_docs_links():
    # The check below must not pass by finding nothing.
    links = _app_links()
    assert any('docsHelp.ts' in f for fs in links.values() for f in fs)
    assert len(links) > 20


@pytest.mark.unit
def test_every_docs_link_lands_on_a_section():
    broken = []
    for url, files in sorted(_app_links().items()):
        page, anchor = _source(url)
        if not page.exists():
            broken.append(
                f'{url} — no page {page.relative_to(ROOT)} ({files[0]})'
            )
            continue
        if anchor and anchor not in _anchors(page):
            broken.append(
                f'{url} — no "#{anchor}" in {page.relative_to(ROOT)} ({files[0]})'
            )
    assert not broken, 'Docs links that miss:\n' + '\n'.join(broken)
