"""MkDocs build hooks for the vibe-seller documentation site.

``docs/`` is the single source of truth: ``docs/index.md`` (zh) and
``docs/index.en.md`` (en) are committed sources, and the GitHub READMEs
are generated from them by ``scripts/gen_readme.py`` (see that script
for the full list of docs -> README rewrite rules). Nothing here reads
or writes the READMEs; these hooks only adapt content at site build
time:

- ``on_page_markdown``: docs pages that link ``../README.md`` (correct
  on GitHub, outside the docs root on the site) are pointed at the
  homepage of whichever language is being built instead. Links to
  ``index.en.md`` (a repo file, but not part of the zh build's page set)
  are pointed at the ``/en/`` homepage.
- ``on_page_markdown``: on the Chinese build, every English-only page is
  prefixed with a short note that it has no Chinese version yet. Today
  that is "every page except the zh homepage" — the deep-dive pages are
  English text served as the zh fallback by mkdocs-static-i18n. When a
  page gains a real Chinese version, exclude it like ``index.md``.
"""

from __future__ import annotations

from mkdocs.config.defaults import MkDocsConfig
from mkdocs.plugins import get_plugin_logger
from mkdocs.structure.files import Files
from mkdocs.structure.pages import Page

log = get_plugin_logger(__name__)

# The one page that is actually written in Chinese.
ZH_HOMEPAGE = 'index.md'

# Prefix added to English-only pages on the Chinese build.
ZH_ONLY_NOTE = '!!! note "本页暂时只有英文版"'


def _current_language(config: MkDocsConfig) -> str | None:
    """Locale of the build pass currently running (set by i18n)."""
    i18n = config.plugins.get('i18n')
    return getattr(i18n, 'current_language', None) if i18n else None


def on_page_markdown(
    markdown: str, *, page: Page, config: MkDocsConfig, files: Files
) -> str:
    """Per-page site-only adaptations (source files stay GitHub-clean)."""
    if page.file.src_path == ZH_HOMEPAGE:
        return markdown
    if _current_language(config) == 'zh':
        markdown = f'{ZH_ONLY_NOTE}\n\n{markdown}'
    markdown = markdown.replace('](../README.md)', '](index.md)')
    return markdown.replace('](index.en.md)', '](/en/)')
