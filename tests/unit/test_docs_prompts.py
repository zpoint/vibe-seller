"""Unit tests for the user-prompt bubble transform (docs_prompts.py).

A prompt — anything the reader types or says to Vibe Seller or to their
own AI tool — is written in curly double quotes in the sources; the
build wraps the pair in ``<span class="say">`` so the stylesheet can
draw it as a chat bubble instead of the grey code/UI look a mis-guessed
``「…」`` gave it (the owner's complaint about 「帮我看看 acme 上周的广
告」). These tests pin both halves: what becomes a bubble, and what
must not be touched — the same guards as ``test_docs_ui_chips.py``.
"""

import pytest

from docs_prompts import mark_prompts
from docs_ui_chips import mark_ui_labels


@pytest.mark.unit
def test_prompt_becomes_a_bubble_and_quotes_are_dropped():
    assert mark_prompts('说一句“帮我看看店铺A上周的广告”，它就会去执行。') == (
        '说一句<span class="say">帮我看看店铺A上周的广告</span>，它就会去执行。'
    )


@pytest.mark.unit
def test_two_prompts_on_one_line_both_become_bubbles():
    out = mark_prompts('试“看看这个邮箱最近有什么邮件”或“第二封的草稿给我看”。')
    assert out.count('<span class="say">') == 2
    assert '“' not in out and '”' not in out


@pytest.mark.unit
def test_a_list_item_that_is_only_a_prompt():
    # The 「可以这样用」 bullets: the CSS keys the bubble-per-line look
    # on li > .say:only-child, which needs this exact shape.
    assert mark_prompts('- “现在 vibe-seller 里有几个店铺？”') == (
        '- <span class="say">现在 vibe-seller 里有几个店铺？</span>'
    )


@pytest.mark.unit
def test_wrapped_english_prompt_is_one_bubble():
    src = (
        "- “Look at Store A's ad spend over the past week and suggest\n"
        "  improvements — don't change anything yet.”"
    )
    out = mark_prompts(src)
    assert out.count('<span class="say">') == 1
    assert '“' not in out and '”' not in out
    assert 'improvements' in out  # the continuation joined the same bubble


@pytest.mark.unit
def test_a_bubble_cannot_span_a_non_prose_line():
    # The opening quote is never answered inside the same block, so the
    # heading between the two quotes stops the pair: nothing converts.
    src = '一句“开头的\n\n## 标题\n\n结尾”的话'
    assert mark_prompts(src) == src


@pytest.mark.unit
def test_heading_line_is_left_alone():
    src = '## 你可以问“现在有几个店铺”吗'
    assert mark_prompts(src) == src


@pytest.mark.unit
def test_inline_code_is_left_alone():
    src = '运行 `“启动” --now` 即可。'
    assert mark_prompts(src) == src


@pytest.mark.unit
def test_fenced_code_is_left_alone():
    src = '```\n“帮我看看店铺A上周的广告”\n```'
    assert mark_prompts(src) == src


@pytest.mark.unit
def test_html_tag_is_left_alone():
    src = '<summary title="“示例”">说明</summary>'
    assert mark_prompts(src) == src


@pytest.mark.unit
def test_image_alt_text_is_left_alone():
    src = '!["保存"按钮的截图](images/a.png)'
    assert mark_prompts(src) == src


@pytest.mark.unit
def test_link_target_is_left_alone_but_link_text_gets_the_bubble():
    src = '见[“保存一下”](faq.md#“保存一下”)。'
    assert mark_prompts(src) == (
        '见[<span class="say">保存一下</span>](faq.md#“保存一下”)。'
    )


@pytest.mark.unit
def test_unpaired_quote_is_left_alone():
    src = '只有开头“没有结尾'
    assert mark_prompts(src) == src


@pytest.mark.unit
def test_front_matter_is_left_alone():
    src = (
        '---\ndescription: 说一句“帮我看看上周的广告”。\n---\n\n正文“查一下”。'
    )
    out = mark_prompts(src)
    assert out.startswith('---\ndescription: 说一句“帮我看看上周的广告”。\n---')
    assert '正文<span class="say">查一下</span>。' in out


@pytest.mark.unit
def test_ui_label_and_prompt_on_one_line_both_render():
    # The chip pass runs after this one: 「保存」 inside prose becomes a
    # UI chip, the curly-quoted prompt a bubble, and a label named
    # inside a prompt keeps its chip within the bubble.
    out = mark_ui_labels(
        mark_prompts('点「保存」，然后说“把这套流程存成技能，下次照这个做”。')
    )
    assert '点<span class="ui">保存</span>' in out
    assert '<span class="say">把这套流程存成技能，下次照这个做</span>' in out


@pytest.mark.unit
def test_label_quoted_inside_a_prompt_still_gets_its_chip():
    out = mark_ui_labels(mark_prompts('说一句“点击「新建任务」帮我建个任务”。'))
    assert '<span class="say">点击' in out
    assert '<span class="ui">新建任务</span>' in out
