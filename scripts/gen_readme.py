#!/usr/bin/env python3
"""Generate the GitHub READMEs from the docs-site homepage sources.

``docs/`` is the single source of truth: ``docs/index.md`` is the Chinese
homepage (and the source of ``README.md``), ``docs/index.en.md`` is the
English homepage (source of ``README_en.md``).  The MkDocs site builds
directly from the index files; this script writes the READMEs from them
so nobody edits the READMEs by hand.

Usage:
    python scripts/gen_readme.py           # write README.md / README_en.md
    python scripts/gen_readme.py --check   # exit 1 + diff if stale

Every rewrite rule (docs source -> GitHub README), applied per locale:

1.  The MkDocs YAML front-matter (``description``) is stripped — GitHub
    would render it as a table.
2.  A ``<!-- Generated from ... -->`` banner is prepended.
3.  SITE-ONLY CONTENT: markdown link lines targeting ``videos/*.mp4``
    (the "watch the full video" link under the demo GIF) are dropped —
    GitHub gets the GIF only, the site gets GIF + video link.  This is
    the only site-only marker; it is recognised by link target, not by
    a comment tag.
4.  Media paths gain the ``docs/`` prefix (the README sits at the repo
    root, the sources sit inside ``docs/``):
      zh:  ``src="images/…"``      -> ``src="docs/images/…"``
      zh:  ``](images/…``          -> ``](docs/images/…``
      en:  ``src="../images/…"``   -> ``src="docs/images/…"``
           (the en homepage is served under ``/en/``, so its raw <img>
           tags point one level up; markdown links are localized by
           mkdocs-static-i18n and use the same ``images/…`` form as zh)
      both: ``](videos/…``         -> ``](docs/videos/…`` (defensive)
5.  Links to sibling docs pages gain the ``docs/`` prefix, e.g.
    ``](tasks.md)`` -> ``](docs/tasks.md)``, for every page in ``docs/``.
6.  Language-switch links go back to the READMEs: ``](/en/)`` and
    ``href="/en/"`` -> ``README_en.md``; ``](/)`` and ``href="/"`` ->
    ``README.md``.
7.  ``markdown="1"`` attributes on ``<details>`` are stripped — they are
    for python-markdown (with ``md_in_html``), which the site needs and
    GitHub does not; GitHub renders markdown inside ``<details>``
    natively.
8.  Everything else passes through verbatim: absolute GitHub links to
    repo-root files (DESIGN.md / CLAUDE.md / LICENSE / the ``docs/``
    tree), Unicode heading anchors, and all body text.

The script refuses to emit a README containing a ``user-attachments``
URL: those only load for logged-in GitHub users and must not come back.
"""

from __future__ import annotations

import argparse
import difflib
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / 'docs'

# locale -> (source page, generated README)
PAIRS = {
    'zh': ('index.md', 'README.md'),
    'en': ('index.en.md', 'README_en.md'),
}

# Site-only content: a full line that is a markdown link to a video file.
_SITE_ONLY_VIDEO_LINE = re.compile(r'^\[.*\]\(videos/[^)]*\.mp4\)\s*$')

_FRONT_MATTER = re.compile(r'\A---\n.*?\n---\n\n?', re.S)

_USER_ATTACHMENTS = 'github.com/user-attachments'


def _docs_pages() -> list[str]:
    """Basenames of the docs pages that links may point at."""
    return sorted(
        p.name
        for p in DOCS.glob('*.md')
        if p.name not in {'README.md', 'requirements.txt'}
    )


def generate(text: str, source_name: str, locale: str) -> str:
    """Turn a docs homepage source into its GitHub README."""
    text = _FRONT_MATTER.sub('', text)

    # Rule 3: drop site-only video-link lines (and the blank line that
    # followed them, so the GIF does not gain a stray gap).
    lines = text.split('\n')
    kept: list[str] = []
    skip_next_blank = False
    for line in lines:
        if _SITE_ONLY_VIDEO_LINE.match(line):
            skip_next_blank = True
            continue
        if skip_next_blank and line == '':
            skip_next_blank = False
            continue
        kept.append(line)
    text = '\n'.join(kept)

    # Rule 4: media paths. Markdown links resolve against the source
    # file, so ``](images/`` is right in BOTH locales. Raw HTML is copied
    # verbatim and resolved against the served URL, which is under
    # ``/en/`` for English — hence ``src="../images/`` there only.
    if locale == 'en':
        text = text.replace('src="../images/', 'src="docs/images/')
        text = text.replace('](../images/', '](docs/images/')
    else:
        text = text.replace('src="images/', 'src="docs/images/')
    text = text.replace('](images/', '](docs/images/')
    text = text.replace('](videos/', '](docs/videos/')

    # Rule 5: sibling docs pages.
    for page in _docs_pages():
        text = text.replace(f']({page}', f'](docs/{page}')

    # Rule 6: language-switch links back to the READMEs.
    text = text.replace('](/en/)', '](README_en.md)')
    text = text.replace('](/)', '](README.md)')
    text = text.replace('href="/en/"', 'href="README_en.md"')
    text = text.replace('href="/"', 'href="README.md"')

    # Rule 7: strip the site-only markdown attribute on <details>.
    text = text.replace('<details markdown="1">', '<details>')
    text = text.replace('<details open markdown="1">', '<details open>')

    if _USER_ATTACHMENTS in text:
        raise SystemExit(
            f'error: {source_name} still references a user-attachments '
            'URL; those do not load for anonymous visitors — commit the '
            'media under docs/ and link it relatively'
        )

    banner = (
        f'<!-- Generated from docs/{source_name} by '
        'scripts/gen_readme.py — edit those, not this file. -->\n\n'
    )
    return banner + text


def check() -> int:
    """Exit 1 with a diff for every stale README; 0 when all are fresh."""
    stale = False
    for locale, (source_name, readme_name) in PAIRS.items():
        source = DOCS / source_name
        readme = ROOT / readme_name
        generated = generate(
            source.read_text(encoding='utf-8'), source_name, locale
        )
        current = readme.read_text(encoding='utf-8')
        if generated != current:
            stale = True
            diff = difflib.unified_diff(
                current.splitlines(keepends=True),
                generated.splitlines(keepends=True),
                fromfile=f'{readme_name} (on disk)',
                tofile=f'{readme_name} (expected)',
            )
            print(
                f'{readme_name} is stale — regenerate with '
                f'`python scripts/gen_readme.py`:',
                file=sys.stderr,
            )
            sys.stderr.writelines(diff)
    return 1 if stale else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--check',
        action='store_true',
        help='verify the READMEs are up to date; exit 1 with a diff if not',
    )
    args = parser.parse_args()
    if args.check:
        return check()
    for locale, (source_name, readme_name) in PAIRS.items():
        source = DOCS / source_name
        out = ROOT / readme_name
        out.write_text(
            generate(source.read_text(encoding='utf-8'), source_name, locale),
            encoding='utf-8',
        )
        print(f'wrote {readme_name} from docs/{source_name}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
