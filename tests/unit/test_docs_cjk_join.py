"""Unit tests for the docs build's Chinese line-join (docs_cjk.py).

Chinese sources are wrapped by hand and Markdown turns each newline into
a space, so 「…步骤、\\n打开浏览器」 renders as 「…步骤、 打开浏览器」.
``join_cjk_lines`` rejoins those lines at build time; these tests pin
what it does and, just as much, what it must not touch — code, block
structure, and the CJK/Latin spaces that are wanted.
"""

import pytest

from docs_cjk import join_cjk_lines


@pytest.mark.unit
def test_cjk_to_cjk_joins_without_a_space():
    out = join_cjk_lines('它自己规划步骤、\n打开浏览器操作。')
    assert out == '它自己规划步骤、打开浏览器操作。'


@pytest.mark.unit
def test_ideograph_to_ideograph_joins_across_three_lines():
    out = join_cjk_lines('一格一格\n点\n网页')
    assert out == '一格一格点网页'


@pytest.mark.unit
def test_cjk_to_ascii_is_not_joined():
    # 「API Key 卡片」 is written that way on purpose: the lines stay
    # apart, so Markdown still renders exactly one space between them.
    assert (
        join_cjk_lines('填好 API Key\n卡片就能用') == '填好 API Key\n卡片就能用'
    )
    assert join_cjk_lines('附件放进工作目录，\nAI 可以直接看到') == (
        '附件放进工作目录，\nAI 可以直接看到'
    )


@pytest.mark.unit
def test_blank_line_ends_the_paragraph():
    assert (
        join_cjk_lines('中文第一行\n\n中文第二行') == '中文第一行\n\n中文第二行'
    )


@pytest.mark.unit
def test_fenced_code_is_kept_as_is():
    src = '\n'.join([
        '说明中文第一行，',
        '第二行继续。',
        '',
        '```bash',
        '注释一，',
        '注释二',
        '```',
        '后面中文，',
        '还有下一行。',
    ])
    out = join_cjk_lines(src)
    assert '注释一，\n注释二' in out, 'code inside the fence must not join'
    assert '说明中文第一行，第二行继续。' in out
    assert '后面中文，还有下一行。' in out


@pytest.mark.unit
def test_indented_code_block_is_kept_as_is():
    src = '段落中文。\n\n    代码第一行，\n    代码第二行\n\n收尾中文，\n继续。'
    out = join_cjk_lines(src)
    assert '    代码第一行，\n    代码第二行' in out
    assert '收尾中文，继续。' in out


@pytest.mark.unit
def test_list_continuation_joins_and_keeps_the_marker():
    out = join_cjk_lines(
        '1. **选择范围**——在左侧店铺列表里点击某个店铺，\n'
        '   任务就建在这个店铺下'
    )
    assert (
        out
        == '1. **选择范围**——在左侧店铺列表里点击某个店铺，任务就建在这个店铺下'
    )


@pytest.mark.unit
def test_a_new_list_item_is_not_joined():
    src = '- 第一个列表项中文，\n- 第二个列表项中文'
    assert join_cjk_lines(src) == src


@pytest.mark.unit
def test_blockquote_continuation_joins():
    out = join_cjk_lines('> 文中的 `~` 指你的\n> 用户目录，Windows 上就是它。')
    assert out == '> 文中的 `~` 指你的用户目录，Windows 上就是它。'


@pytest.mark.unit
def test_heading_does_not_swallow_the_next_line():
    src = '## 支持的平台\n中文正文开头。'
    assert join_cjk_lines(src) == src


@pytest.mark.unit
def test_table_rows_are_left_alone():
    src = '| 店铺 | 该店绑定的紫鸟，\n  或 Chrome | 另一格 |'
    assert join_cjk_lines(src) == src


@pytest.mark.unit
def test_admonition_body_joins_under_its_header():
    src = '!!! note "标题"\n    正文第一行中文，\n    正文第二行中文。'
    assert (
        join_cjk_lines(src)
        == '!!! note "标题"\n    正文第一行中文，正文第二行中文。'
    )


@pytest.mark.unit
def test_content_tab_body_joins():
    src = '=== "Kimi"\n\n    按量付费和套餐都有，\n    但两种 Key 在不同平台。'
    assert join_cjk_lines(src) == (
        '=== "Kimi"\n\n    按量付费和套餐都有，但两种 Key 在不同平台。'
    )


@pytest.mark.unit
def test_join_reaches_through_inline_markup():
    # The edge the reader sees is Chinese even though the source edge is
    # a link or a bold marker.
    assert join_cjk_lines('还没装？先看\n[安装](install.md)。') == (
        '还没装？先看[安装](install.md)。'
    )
    assert join_cjk_lines(
        '最要紧的事：**任务在哪里创建**，\n决定了它能用什么浏览器。'
    ) == ('最要紧的事：**任务在哪里创建**，决定了它能用什么浏览器。')
    assert join_cjk_lines('使用一台通用 Chrome，\n**只能访问公开网页**。') == (
        '使用一台通用 Chrome，**只能访问公开网页**。'
    )


@pytest.mark.unit
def test_adjacent_emphasis_markers_are_not_joined():
    # ``**a****b**`` would re-parse as different emphasis, so the lines
    # stay apart.
    src = '前面写**重要**\n**也只**是说明'
    assert join_cjk_lines(src) == src


@pytest.mark.unit
def test_forced_line_break_is_respected():
    src = '中文第一行  \n中文第二行'
    assert join_cjk_lines(src) == src


@pytest.mark.unit
def test_front_matter_is_untouched():
    src = '---\ndescription: 中文一句，\n中文二句。\n---\n\n正文中文，\n继续。'
    out = join_cjk_lines(src)
    assert out.startswith('---\ndescription: 中文一句，\n中文二句。\n---')
    assert '正文中文，继续。' in out


@pytest.mark.unit
def test_html_line_is_not_joined():
    src = '说明中文一行，\n<img alt="图" src="images/a.png" />'
    assert join_cjk_lines(src) == src


@pytest.mark.unit
def test_english_text_is_left_alone():
    src = 'The agent opens the browser\nand runs the task in one go.'
    assert join_cjk_lines(src) == src


@pytest.mark.unit
def test_joining_is_idempotent():
    src = '中文第一行，\n第二行继续。\n\n- 列表中文，\n  续行中文'
    once = join_cjk_lines(src)
    assert join_cjk_lines(once) == once
