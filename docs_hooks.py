"""MkDocs build hooks for the vibe-seller documentation site.

``docs/`` is the single source of truth: ``docs/index.md`` (zh) and
``docs/index.en.md`` (en) are committed sources, and the GitHub READMEs
are generated from them by ``scripts/gen_readme.py`` (see that script
for the full list of docs -> README rewrite rules). Nothing here reads
or writes the READMEs; these hooks only adapt content at site build
time:

- ``on_page_markdown``: Chinese sources are wrapped by hand and Markdown
  renders each of those newlines as a space inside a Chinese sentence.
  ``docs_cjk.join_cjk_lines`` rejoins them at build time, so the sources
  keep their line breaks and the GitHub READMEs (generated from the
  sources) are unchanged. See that module for the rules.
- ``on_page_markdown``: ``docs_ui_chips.mark_ui_labels`` turns the
  corner-bracketed UI names the Chinese pages quote (「新建任务」,
  「设置 → 集成」) into ``<span class="ui">`` chips, and the English
  pages' ``"**Label**"`` convention with them, so a button reads as a
  button. Prose in the same brackets, headings, code and HTML are left
  alone; the sources keep their plain text.
- ``on_page_markdown``: docs pages that link ``../README.md`` (correct
  on GitHub, outside the docs root on the site) are pointed at the
  homepage of whichever language is being built instead. Links to
  ``index.en.md`` (a repo file, but not part of the zh build's page set)
  are pointed at the ``/en/`` homepage.
- ``on_page_markdown``: raw ``<img>`` tags keep a GitHub-relative media
  path, which 404s once the page is served from its own directory on the
  site; the site build pins them at the shared ``/images/`` root.
- ``on_page_markdown``: on the Chinese build, every English-only page is
  prefixed with a short note that it has no Chinese version yet. A page
  has a real Chinese version exactly when it has an ``.en.md`` sibling —
  ``docs_structure: suffix`` pairs ``foo.md`` (the Chinese source) with
  ``foo.en.md``. Pages without a sibling are English text served as the
  zh fallback by mkdocs-static-i18n.
- Homepage only (``index.md`` / ``index.en.md``): the site presents the
  page as a docs landing page without the sources gaining any
  site-only markup the READMEs would have to carry. The empty-header
  document-directory table is wrapped so ``docs/stylesheets/extra.css``
  can lay it out as cards, and a quick-start card grid is inserted under
  the demo video link, built from the links that table already contains
  (so labels and targets stay single-sourced). ``on_post_page`` tags the
  rendered ``<body>`` with ``mdx-home``, which is what scopes all of the
  homepage CSS rules.

Everything here is presentation: no page's wording is edited.
"""

from __future__ import annotations

from pathlib import Path
import re

from mkdocs.config.defaults import MkDocsConfig
from mkdocs.plugins import get_plugin_logger
from mkdocs.structure.files import Files
from mkdocs.structure.pages import Page

from docs_cjk import join_cjk_lines
from docs_ui_chips import mark_ui_labels

log = get_plugin_logger(__name__)

# Prefix added to English-only pages on the Chinese build.
ZH_ONLY_NOTE = '!!! note "本页暂时只有英文版"'

# Sources that render as the site homepage, one per locale.
HOME_SRCS = frozenset({'index.md', 'index.en.md'})

# The "watch the full video" line under the demo GIF — the landing's
# quick-start cards go right after it (same line scripts/gen_readme.py
# treats as site-only).
_VIDEO_LINE = re.compile(r'^\[.*\]\(videos/[^)]*\.(?:mp4|webm)\)\s*$')

# Raw ``<img>`` tags keep the GitHub-relative media path (``images/…`` on
# the Chinese pages, ``../images/…`` on the English ones, where the page
# is served under ``/en/``). A site page is served from its own directory
# — ``/quickstart/``, ``/en/quickstart/`` — so the same path would resolve
# next to the page and 404. Images are shared by both locales at the site
# root, so the site build pins them there. Markdown images (``![](…)``)
# need none of this: MkDocs rewrites those per page.
_MEDIA_SRC = re.compile(r'src="\.\./images/|src="images/')

# Horizontal rule, the fallback anchor for the card grid.
_RULE = re.compile(r'^-{3,}\s*$')

# One markdown inline link, e.g. ``[安装](install.md)``.
_INLINE_LINK = re.compile(r'\[([^\]]*)\]\(([^)]+)\)')

# Landing card order, icons and one-line blurbs, keyed by page stem
# (``install.md`` and ``install.en.md`` both stem to ``install``). Titles
# and link targets come from the homepage's own directory table, never
# from here; the blurbs name what the page actually covers — the UI terms
# (分发 / 单任务 / 店铺逐个运行, Fan out / Single) are the ones the pages
# and the app itself use.
_LANDING_CARDS = (
    (
        'install',
        'material-download',
        {
            'zh': 'Windows 安装包 / macOS / Linux 一行命令',
            'en': 'Windows installer, or one line on macOS / Linux',
        },
    ),
    (
        'quickstart',
        'material-rocket-launch',
        {
            'zh': '配置模型、绑定店铺、完成第一个任务',
            'en': 'Set up a model, connect a store, run your first task',
        },
    ),
    (
        'llm-keys',
        'material-key-variant',
        {
            'zh': 'DeepSeek、Claude、Kimi 等去哪拿 Key、选哪个预设',
            'en': 'Where to get a key for DeepSeek, Claude, Kimi…',
        },
    ),
    (
        'concepts',
        'material-lightbulb-on',
        {
            'zh': '店铺、任务、定时、集成之间的关系',
            'en': 'How stores, tasks, schedules and integrations fit',
        },
    ),
    (
        'integrations',
        'material-puzzle',
        {
            'zh': '邮件、群机器人、MCP、广告 API……',
            'en': 'Email, chat bots, MCP, Ads API…',
        },
    ),
    (
        'examples',
        'material-play-box-multiple',
        {
            'zh': '邮件发企业微信、广告 ACOS 巡检等分步教程',
            'en': 'Step-by-step: email digest to WeCom, ACOS review…',
        },
    ),
)


def _has_chinese_version(page: Page, config: MkDocsConfig) -> bool:
    """Whether this page has a real Chinese source.

    The suffix docs structure pairs ``foo.md`` (Chinese) with
    ``foo.en.md`` (English); a page without an ``.en.md`` sibling is
    English text used as the zh fallback and does not count.
    """
    src = Path(page.file.src_path).with_suffix('.en.md')
    return (Path(config.docs_dir) / src).exists()


def _current_language(config: MkDocsConfig) -> str | None:
    """Locale of the build pass currently running (set by i18n)."""
    i18n = config.plugins.get('i18n')
    return getattr(i18n, 'current_language', None) if i18n else None


def _is_home(page: Page) -> bool:
    """Whether this page is the homepage of the locale being built."""
    return page.file.src_uri in HOME_SRCS


def _table_span(lines: list[str]) -> tuple[int, int] | None:
    """Start/stop of the directory table (empty header row), if any.

    The homepage's document directory is the one table in the source
    with no header text — that shape, not the heading above it, is what
    the landing transform anchors on, so rewording a section title
    cannot break it. Other tables on the page (the scenario table) are
    skipped.
    """
    i = 0
    while i < len(lines):
        if not lines[i].lstrip().startswith('|'):
            i += 1
            continue
        start = i
        while i < len(lines) and lines[i].lstrip().startswith('|'):
            i += 1
        rows = [_cells(row) for row in lines[start:i]]
        body = rows[2:]
        if len(rows) > 2 and not any(rows[0]) and any(any(r) for r in body):
            return start, i
    return None


def _cells(row: str) -> list[str]:
    """The cells of one markdown table row."""
    return [cell.strip() for cell in row.strip().strip('|').split('|')]


def _table_links(
    lines: list[str], start: int, end: int
) -> dict[str, tuple[str, str]]:
    """``stem -> (label, target)`` for every link in the table body."""
    links: dict[str, tuple[str, str]] = {}
    for row in lines[start + 2 : end]:
        for label, target in _INLINE_LINK.findall(row):
            if not label or not target:
                continue
            stem = Path(target).stem
            if stem.endswith('.en'):
                stem = stem[: -len('.en')]
            links.setdefault(stem, (label, target))
    return links


def _cards(links: dict[str, tuple[str, str]], locale: str | None) -> list[str]:
    """The quick-start card grid, in ``_LANDING_CARDS`` order."""
    items = []
    for stem, icon, blurbs in _LANDING_CARDS:
        found = links.get(stem)
        if found is None:
            log.warning(
                f'homepage directory has no {stem} link — landing card skipped'
            )
            continue
        label, target = found
        blurb = blurbs.get(locale or 'zh', blurbs['zh'])
        items.append(f'-   [:{icon}: __{label}__]({target})\n\n    {blurb}')
    if not items:
        return []
    return [
        '<div class="mdx-quick grid cards" markdown="1">',
        '',
        '\n\n'.join(items),
        '',
        '</div>',
    ]


def _insert_after(
    lines: list[str], block: list[str], *patterns: re.Pattern[str]
) -> bool:
    """Insert ``block`` after the first line matching any pattern."""
    for pattern in patterns:
        for i, line in enumerate(lines):
            if pattern.match(line):
                lines[i + 1 : i + 1] = ['', *block]
                return True
    return False


def _landing(markdown: str, locale: str | None) -> str:
    """Homepage-only landing treatment, applied to the site copy only."""
    lines = markdown.split('\n')
    span = _table_span(lines)
    if span is None:
        log.warning(
            'homepage document-directory table not found — the landing '
            'page keeps the README layout'
        )
        return markdown
    start, end = span
    cards = _cards(_table_links(lines, start, end), locale)
    lines[start:end] = [
        '<div class="mdx-directory" markdown="1">',
        '',
        *lines[start:end],
        '',
        '</div>',
    ]
    if cards and not _insert_after(lines, cards, _VIDEO_LINE, _RULE):
        lines[:0] = [*cards, '']
        log.warning(
            'homepage demo block not found — landing cards placed at '
            'the top of the page'
        )
    return '\n'.join(lines)


def on_page_markdown(
    markdown: str, *, page: Page, config: MkDocsConfig, files: Files
) -> str:
    """Per-page site-only adaptations (source files stay GitHub-clean)."""
    # First, on the source text: rejoined wrapped Chinese lines cannot
    # confuse the anchors the homepage transform looks for below.
    markdown = join_cjk_lines(markdown)
    markdown = mark_ui_labels(markdown)
    locale = _current_language(config)
    if locale == 'zh' and not _has_chinese_version(page, config):
        markdown = f'{ZH_ONLY_NOTE}\n\n{markdown}'
    if _is_home(page):
        markdown = _landing(markdown, locale)
    markdown = _MEDIA_SRC.sub('src="/images/', markdown)
    markdown = markdown.replace('](../README.md)', '](index.md)')
    return markdown.replace('](index.en.md)', '](/en/)')


def on_post_page(template: str, *, page: Page, config: MkDocsConfig) -> str:
    """Mark the rendered homepage body so extra.css styles it as one."""
    if _is_home(page):
        template = template.replace('<body ', '<body class="mdx-home" ', 1)
    return template
