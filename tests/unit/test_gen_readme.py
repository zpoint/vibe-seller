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


def test_language_switch_links_open_the_docs_site():
    src = '<a href="/en/">English</a> · <a href="/">中文</a> [en](/en/) [zh](/)'
    out = gen_readme.generate(src, 'index.md', 'zh')
    assert 'href="https://docs.vibe-sellers.com/en/"' in out
    assert 'href="https://docs.vibe-sellers.com/"' in out
    assert '](https://docs.vibe-sellers.com/en/)' in out
    assert '](https://docs.vibe-sellers.com/)' in out
    assert 'README' not in out.split('-->', 1)[1]


def test_ui_labels_render_as_kbd_in_the_zh_readme():
    # The site draws 「标签」 as chips; GitHub strips the chip's class, so
    # without this the README shows bare brackets that read as prose.
    src = '1. 进入「设置 → AI」，点「添加模型」。\n'
    out = gen_readme.generate(src, 'x.md', 'zh')
    assert '<kbd>设置 › AI</kbd>' in out
    assert '<kbd>添加模型</kbd>' in out
    assert '「' not in out


def test_quoted_bold_labels_render_as_kbd_in_the_en_readme():
    out = gen_readme.generate('Click "**Add Model**".\n', 'x.md', 'en')
    assert '<kbd>Add Model</kbd>' in out


def test_headings_and_code_keep_their_brackets_in_the_readme():
    src = '## 「标题」\n\n```\n「代码」\n```\n'
    out = gen_readme.generate(src, 'x.md', 'zh')
    assert '## 「标题」' in out
    assert '「代码」' in out
