"""``scripts/gen_readme.py`` turns the docs homepages into the READMEs.

The READMEs are generated output, so a rewrite rule that misses a form
ships a broken link to GitHub that ``--check`` happily accepts.
"""

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SPEC = importlib.util.spec_from_file_location(
    'gen_readme',
    Path(__file__).resolve().parents[2] / 'scripts' / 'gen_readme.py',
)
gen_readme = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gen_readme)


@pytest.mark.parametrize('locale', ['zh', 'en'])
def test_markdown_image_links_point_into_docs_in_both_locales(locale):
    out = gen_readme.generate('![Demo](images/demo.gif)\n', 'x.md', locale)
    assert '](docs/images/demo.gif)' in out


def test_raw_html_image_paths_follow_each_locale():
    zh = gen_readme.generate('<img src="images/a.png" />\n', 'x.md', 'zh')
    en = gen_readme.generate('<img src="../images/a.png" />\n', 'x.md', 'en')
    assert 'src="docs/images/a.png"' in zh
    assert 'src="docs/images/a.png"' in en


def test_site_only_video_link_is_dropped():
    src = '<img src="images/demo.gif" />\n\n[▶ Watch](videos/demo.mp4)\n'
    assert 'videos/' not in gen_readme.generate(src, 'x.md', 'zh')


def test_user_attachment_urls_are_refused():
    src = '<img src="https://github.com/user-attachments/assets/x" />\n'
    with pytest.raises(SystemExit):
        gen_readme.generate(src, 'x.md', 'zh')


def test_committed_readmes_are_current():
    assert gen_readme.check() == 0


@pytest.mark.parametrize(
    ('source', 'locale', 'expected'),
    [
        (
            '](quickstart.md)',
            'zh',
            '](https://docs.vibe-sellers.com/quickstart/)',
        ),
        (
            '](ziniao.md#报错)',
            'zh',
            '](https://docs.vibe-sellers.com/ziniao/#报错)',
        ),
        ('](mcp.en.md)', 'en', '](https://docs.vibe-sellers.com/en/mcp/)'),
        ('](mcp.md)', 'en', '](https://docs.vibe-sellers.com/en/mcp/)'),
        ('](not-a-page.md)', 'zh', '](not-a-page.md)'),
    ],
)
def test_docs_page_links_open_on_the_site(source, locale, expected):
    out = gen_readme.generate(f'see [x]{source}\n', 'index.md', locale)
    assert expected in out
