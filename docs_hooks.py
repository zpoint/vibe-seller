"""MkDocs build hooks for the vibe-seller documentation site.

The two site homepages are generated at build time from the repository
READMEs so the text lives in exactly one place (``README.md`` is the
Chinese homepage, ``README_en.md`` the English one) and can never drift
between GitHub and the docs site.  Nothing here modifies a tracked file:
``docs/index.md`` and ``docs/index.en.md`` are written on every build and
are gitignored.

The rewrite rules below translate root-relative README conventions
(``docs/images/...``, ``docs/<page>.md``, ``DESIGN.md`` ...) into what
resolves inside ``docs/`` once the page sits at ``docs/index*.md``.
GitHub rendering of the READMEs is untouched — only the generated copies
are rewritten.
"""

from __future__ import annotations

from pathlib import Path
import re

from mkdocs.config.defaults import MkDocsConfig
from mkdocs.plugins import get_plugin_logger
from mkdocs.structure.files import Files
from mkdocs.structure.pages import Page

log = get_plugin_logger(__name__)

ROOT = Path(__file__).parent
DOCS_DIR = ROOT / 'docs'

REPO_URL = 'https://github.com/zpoint/vibe-seller'
REPO_BLOB = f'{REPO_URL}/blob/main/'
REPO_TREE = f'{REPO_URL}/tree/main/'

# Locale -> (README source, generated page, image prefix, homepage
# meta description). The image prefix points <img> tags at the shared
# copy of docs/images/ for the URL the generated page is served under.
HOMEPAGES = {
    'zh': (
        'README.md',
        'index.md',
        '',
        '跨境卖家的 AI 自动化框架——本地部署，Agent 通过 CDP 驱动紫鸟或'
        ' Chrome 完成广告调优、Listing 上架、报表导出、库存巡检等任务，'
        '支持任意大模型。',
    ),
    'en': (
        'README_en.md',
        'index.en.md',
        '../',
        'AI automation framework for cross-border sellers — locally '
        'deployed agents drive Ziniao or Chrome via CDP for ad tuning, '
        'listing launches, report exports and inventory checks, with any '
        'LLM provider.',
    ),
}

# Generated homepages: point the "edit this page" button at the README
# that was actually copied, not at the transient generated file.
_EDIT_SOURCE = {
    'index.md': 'README.md',
    'index.en.md': 'README_en.md',
}

# Repo-root files linked from the READMEs; on the site they are not under
# docs/ so they get stable absolute GitHub URLs instead.
_ROOT_FILE_LINKS = {
    'DESIGN.md': f'{REPO_BLOB}DESIGN.md',
    'CLAUDE.md': f'{REPO_BLOB}CLAUDE.md',
    'LICENSE': f'{REPO_BLOB}LICENSE',
    'docs/': f'{REPO_TREE}docs',
}

# A standalone demo-video URL line in a README (renders as a bare link on
# GitHub).  Must be replaced before the generic link rewrites below.
_BARE_VIDEO = re.compile(
    r'^https://github\.com/user-attachments/assets/[\w-]+\s*$', re.M
)
# First inline <img> whose source is a user-attachment (the animated GIF
# at the top of the READMEs once PR #190 lands).
_ATTACHMENTS_IMG = re.compile(
    r'<img\b[^>]*src="https://github\.com/user-attachments/assets/[^"]+"[^>]*>'
)


def _video_link(label: str, url: str) -> str:
    return f'[▶ {label}]({url})'


def _rewrite_homepage(
    text: str, video_label: str, video_url: str, image_prefix: str
) -> str:
    """Adapt a README's links for its generated copy under ``docs/``."""
    # Demo video: today each README has a bare user-attachments URL on its
    # own line (a video GitHub renders as a plain link); replace that line
    # with a labelled link.  Once PR #190 swaps the bare URL for an inline
    # GIF, the same link is inserted right below the GIF instead.  Either
    # way the URL/label come from mkdocs.yml `extra.demo_video`.
    if _BARE_VIDEO.search(text):
        text = _BARE_VIDEO.sub(
            _video_link(video_label, video_url), text, count=1
        )
    elif _ATTACHMENTS_IMG.search(text):
        text = _ATTACHMENTS_IMG.sub(
            lambda m: f'{m.group(0)}\n\n{_video_link(video_label, video_url)}',
            text,
            count=1,
        )

    # `docs/` directory link -> GitHub tree view (handled before the
    # generic docs/ prefix strip below would empty the target).
    text = text.replace('](docs/)', f']({_ROOT_FILE_LINKS["docs/"]})')
    text = text.replace('href="docs/"', f'href="{_ROOT_FILE_LINKS["docs/"]}"')
    # Links into docs/*.md resolve next to the generated index page.
    text = text.replace('](docs/', '](')
    # Images live under docs/images/. The i18n plugin rewrites *markdown*
    # image links per language, but the READMEs use raw <img> tags, which
    # it leaves untouched — so point them at the shared root copy here
    # (``../images/`` from the ``/en/`` build).
    text = text.replace('docs/images/', f'{image_prefix}images/')
    # Language-switch links between the two README homepages.
    text = text.replace('](README_en.md)', '](/en/)')
    text = text.replace('](README.md)', '](/)')
    text = text.replace('href="README_en.md"', 'href="/en/"')
    text = text.replace('href="README.md"', 'href="/"')
    # Repo-root documents are not part of the site; link to GitHub.
    for name, url in _ROOT_FILE_LINKS.items():
        if name == 'docs/':
            continue
        text = text.replace(f']({name})', f']({url})')
        text = text.replace(f'href="{name}"', f'href="{url}"')
    # GitHub renders markdown inside <details> blocks; python-markdown
    # needs the md_in_html `markdown` attribute to do the same. Injected
    # into the generated copy only — the READMEs stay untouched.
    text = text.replace('<details open>', '<details open markdown="1">')
    text = text.replace('<details>', '<details markdown="1">')
    return text


def on_pre_build(config: MkDocsConfig) -> None:
    """Generate the two homepages from the READMEs, then build."""
    videos = config.extra.get('demo_video', {})
    for locale, (
        readme_name,
        index_name,
        image_prefix,
        description,
    ) in HOMEPAGES.items():
        source = ROOT / readme_name
        video = videos.get(locale, {})
        text = _rewrite_homepage(
            source.read_text(encoding='utf-8'),
            video.get('label', 'Video'),
            video.get('url', 'https://github.com/zpoint/vibe-seller'),
            image_prefix,
        )
        target = DOCS_DIR / index_name
        target.write_text(
            f'---\ndescription: {description}\n---\n\n{text}',
            encoding='utf-8',
        )
        log.info(f'Generated {target.relative_to(ROOT)} from {readme_name}')


def on_page_markdown(
    markdown: str, *, page: Page, config: MkDocsConfig, files: Files
) -> str:
    """Fix docs-page links that only resolve on GitHub.

    ``docs/dev-guide.md`` links to ``../README.md`` (correct on GitHub,
    outside the docs root on the site); point it at the generated
    homepage instead.
    """
    if page.file.src_path in _EDIT_SOURCE:
        return markdown
    return markdown.replace('](../README.md)', '](index.md)')


def on_page_content(
    html: str, *, page: Page, config: MkDocsConfig, files: Files
) -> str:
    """Point the homepage edit button at the README it was generated from."""
    source = _EDIT_SOURCE.get(page.file.src_path)
    if source:
        page.edit_url = f'{REPO_URL}/edit/main/{source}'
    return html
