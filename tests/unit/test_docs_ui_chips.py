"""Unit tests for the UI-label chip transform (docs_ui_chips.py).

The Chinese pages name a button or a menu with corner brackets, which
reads as plain text; the build wraps the ones that look like UI elements
in ``<span class="ui">`` so the stylesheet can draw them as chips. These
tests pin both halves: what becomes a chip, and — more importantly —
what must not be touched (prose in the same brackets, headings whose
anchors would change, code, HTML attributes, link targets).
"""

import pytest

from docs_ui_chips import mark_ui_labels


@pytest.mark.unit
def test_short_label_becomes_a_chip():
    assert mark_ui_labels('点击「新建任务」按钮。') == (
        '点击<span class="ui">新建任务</span>按钮。'
    )


@pytest.mark.unit
def test_label_inside_a_table_cell_becomes_a_chip():
    assert mark_ui_labels('| 入口 | 「任务」页 |') == (
        '| 入口 | <span class="ui">任务</span>页 |'
    )


@pytest.mark.unit
def test_long_label_may_wrap():
    # More than 16 characters: the chip gets the wrap-allowed class.
    out = mark_ui_labels('勾选「把这条规则写进店铺知识库的运营笔记里」保存。')
    assert '<span class="ui ui--long">' in out


@pytest.mark.unit
def test_quoted_sentence_is_left_alone():
    # Sentence punctuation inside means prose, not a label; so does a
    # length over 24 characters.
    src = (
        '提示「先看报表，再决定」即可，'
        '然后「把这条规则写进店铺知识库的运营笔记里，下次自动执行」保存。'
    )
    assert mark_ui_labels(src) == src


@pytest.mark.unit
def test_heading_line_is_left_alone():
    src = '## 绑定店铺时紫鸟提示「无权限通过 webdriver 登录」怎么办'
    assert mark_ui_labels(src) == src


@pytest.mark.unit
def test_inline_code_is_left_alone():
    src = '运行 `vibe-seller 「启动」` 即可。'
    assert mark_ui_labels(src) == src


@pytest.mark.unit
def test_fenced_code_is_left_alone():
    src = '```\n点击「新建任务」\n```'
    assert mark_ui_labels(src) == src


@pytest.mark.unit
def test_html_attribute_is_left_alone():
    src = '<img alt="「新建店铺」表单" src="images/a.png" />'
    assert mark_ui_labels(src) == src


@pytest.mark.unit
def test_image_alt_text_is_left_alone():
    src = '![「新建店铺」表单](images/a.png)'
    assert mark_ui_labels(src) == src


@pytest.mark.unit
def test_link_target_is_left_alone_but_link_text_gets_a_chip():
    src = '见[「新建任务」](faq.md#「新建任务」)。'
    assert mark_ui_labels(src) == (
        '见[<span class="ui">新建任务</span>](faq.md#「新建任务」)。'
    )


@pytest.mark.unit
def test_menu_path_becomes_one_chip_with_separators():
    assert mark_ui_labels('在「设置 → 集成 → 邮箱」里添加。') == (
        '在<span class="ui">设置'
        '<span class="ui-sep">›</span>集成'
        '<span class="ui-sep">›</span>邮箱</span>里添加。'
    )


@pytest.mark.unit
def test_menu_path_longer_than_16_chars_may_wrap():
    # The wrap class follows the *rendered* length (segments joined), not
    # the source's arrows and spaces: 20 characters here.
    out = mark_ui_labels('打开「设置 → 集成 → Google Workspace」。')
    assert '<span class="ui ui--long">' in out
    assert out.count('ui-sep') == 2


@pytest.mark.unit
def test_long_menu_path_is_chipped_despite_the_label_cap():
    # 31 characters: prose for a plain label, still a breadcrumb for a
    # path — this is the row that stayed bracketed on /integrations/.
    src = '见「设置 → 集成 → Vibe Seller 广告 API 服务」。'
    out = mark_ui_labels(src)
    assert out == (
        '见<span class="ui ui--long">设置'
        '<span class="ui-sep">›</span>集成'
        '<span class="ui-sep">›</span>Vibe Seller 广告 API 服务</span>。'
    )


@pytest.mark.unit
def test_menu_path_over_the_path_cap_stays_plain():
    # 66 rendered characters: past the path cap, so it stays as written.
    src = (
        '「连接器 → 自定义连接器 → MCP Server Management → '
        '+ Add MCP Server → 回到列表页 → 刷新一次 → 确认状态已更新为已连接」'
    )
    assert mark_ui_labels(src) == src


@pytest.mark.unit
def test_chained_labels_become_one_path_chip():
    out = mark_ui_labels('在「设置 → 邮箱」→「添加邮箱」里绑定。')
    assert out == (
        '在<span class="ui">设置'
        '<span class="ui-sep">›</span>邮箱'
        '<span class="ui-sep">›</span>添加邮箱</span>里绑定。'
    )
    assert '」→「' not in out


@pytest.mark.unit
def test_chain_with_a_sentence_segment_falls_back_to_per_label():
    # The chain is not a path when one half is prose; each half is then
    # judged on its own — the sentence keeps its brackets, the button
    # still gets its chip.
    src = '「先看报表，再决定」→「保存」都不行。'
    assert (
        mark_ui_labels(src)
        == '「先看报表，再决定」→<span class="ui">保存</span>都不行。'
    )


@pytest.mark.unit
def test_a_segments_own_menu_arrow_is_not_doubled():
    # 「GLM ›」 already ends in the UI's own separator; joining must not
    # produce 「GLM › › 国内」.
    assert mark_ui_labels('选「GLM ›」→「国内」。') == (
        '选<span class="ui">GLM<span class="ui-sep">›</span>国内</span>。'
    )


@pytest.mark.unit
def test_front_matter_is_left_alone():
    src = '---\ndescription: 点击「新建任务」开始。\n---\n\n正文「新建任务」。'
    out = mark_ui_labels(src)
    assert out.startswith('---\ndescription: 点击「新建任务」开始。\n---')
    assert '正文<span class="ui">新建任务</span>。' in out


@pytest.mark.unit
def test_english_quoted_bold_label_becomes_a_chip():
    assert mark_ui_labels('Click "**New API key**" to mint one.') == (
        'Click <span class="ui">New API key</span> to mint one.'
    )


@pytest.mark.unit
def test_english_bold_prose_with_quotes_is_left_alone():
    # **"…"** in these files is usually bolded prose, not a label.
    src = 'Fill **" and fill **" style prose, then "**Save**" it.'
    out = mark_ui_labels(src)
    assert 'Fill **" and fill **" style prose' in out
    assert '<span class="ui">Save</span>' in out


@pytest.mark.unit
def test_english_quoted_sentence_is_left_alone():
    src = (
        'The warning says "**Ziniao is not running or not in WebDriver mode.**"'
    )
    assert mark_ui_labels(src) == src


@pytest.mark.unit
def test_chips_do_not_nest_or_double_up():
    once = mark_ui_labels('点「新建任务」再看「任务」页。')
    assert mark_ui_labels(once) == once


@pytest.mark.unit
def test_line_without_brackets_is_untouched():
    src = '普通句子，没有角括号。'
    assert mark_ui_labels(src) == src
