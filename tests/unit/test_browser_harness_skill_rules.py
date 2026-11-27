"""The browser-harness skill must carry the two click-accuracy rules.

Both rules pin failure modes seen live on a Windows store task and
reproduced deterministically against a real Chrome at
devicePixelRatio 1.5:

- **CSS vs device pixels**: ``click_at_xy`` takes CSS pixels. An agent
  that reads a click point off a SCREENSHOT (device pixels) misses by
  the display-scale factor — on a 150%-scaled Windows display exactly
  1.5x, which spares the middle of large buttons and kills corner and
  small-target clicks. The skill must teach the CSS-pixel contract and
  the divide-by-``devicePixelRatio`` conversion.
- **Background tabs drop trusted input**: ``Input.dispatchMouseEvent``
  on a tab that was never ``activate_tab``-ed produces zero page
  events — the click is reported as fine and silently does nothing.
  The skill must teach activation after ``new_tab``.

If either rule disappears from the skill, agents go back to clicking
pictures and wondering why nothing happens; this test fails before
that ships.
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SKILL = (
    Path(__file__).resolve().parents[2]
    / 'app'
    / 'skills_v2'
    / 'browser-harness'
    / 'SKILL.md'
)


def _skill_text() -> str:
    return SKILL.read_text(encoding='utf-8')


def test_skill_teaches_click_at_xy_takes_css_pixels():
    text = _skill_text()
    assert 'CSS pixels' in text, (
        'the skill must state that click_at_xy takes CSS pixels'
    )
    assert 'devicePixelRatio' in text, (
        'the skill must teach dividing screenshot pixels by '
        'window.devicePixelRatio'
    )


def test_skill_teaches_activation_after_new_tab():
    text = _skill_text()
    assert 'activate_tab' in text, (
        'the skill must tell agents to activate_tab after new_tab'
    )
    assert 'background tab' in text, (
        'the skill must say a background tab silently drops clicks'
    )
