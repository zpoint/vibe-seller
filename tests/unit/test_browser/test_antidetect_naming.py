"""Agent-facing browser naming must match the store's actual backend.

A yuehulian store must never be presented to the agent as "Ziniao": it
reused ziniao's prompt path, so the routing prompt hardcoded "Ziniao" and
the agent reported a yuehulian store's failure as a 紫鸟 error. These pin
that the display name follows the backend and the shared template carries
no hardcoded product name.
"""

import pytest

from app.browser.base import antidetect_display_name
from app.models.store import Store
from app.prompts import DUAL_BROWSER_PROMPT
from app.task_runner_context import build_store_context

pytestmark = pytest.mark.unit


def test_display_name_follows_backend():
    assert '悦互联' in antidetect_display_name('yuehulian')
    assert '紫鸟' in antidetect_display_name('ziniao')
    # Unknown backend → neutral fallback, never the wrong product name.
    fb = antidetect_display_name('some-new-browser')
    assert 'Ziniao' not in fb and '悦互联' not in fb


def test_dual_browser_template_has_no_hardcoded_product():
    # The shared routing template uses the placeholder, not a product name.
    assert '__BROWSER_NAME__' in DUAL_BROWSER_PROMPT
    assert 'Ziniao' not in DUAL_BROWSER_PROMPT
    assert '紫鸟' not in DUAL_BROWSER_PROMPT


def test_yuehulian_context_names_yuehulian_not_ziniao():
    ctx = build_store_context(
        Store(
            id='s1',
            name='shop-x',
            browser_backend='yuehulian',
            browser_oauth='store-a',
        )
    )
    assert '悦互联' in ctx
    assert 'Ziniao' not in ctx and '紫鸟' not in ctx
    assert '__BROWSER_NAME__' not in ctx  # the placeholder was filled


def test_ziniao_context_still_names_ziniao():
    # Twist: the ziniao store keeps its own product name.
    ctx = build_store_context(
        Store(
            id='s2',
            name='shop-y',
            browser_backend='ziniao',
            browser_oauth='',
        )
    )
    assert 'Ziniao' in ctx
    assert '__BROWSER_NAME__' not in ctx
