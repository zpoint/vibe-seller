"""Yuehulian is routed through the anti-detect prompt path (like ziniao),
but the ziniao-SBP-specific credential/OTP auto-fill is NOT applied — a
yuehulian store env is cookie-persisted, so telling the agent to wait for
auto-fill would be wrong. Each assertion pairs with a twist.
"""

import pytest

from app.models.store import Store
from app.task_runner_context import build_store_context

pytestmark = pytest.mark.unit


def _store(backend: str) -> Store:
    return Store(
        id='store-1',
        name='Alpha',
        browser_backend=backend,
        browser_config='{}',
        platforms='["amazon"]',
        countries='["US"]',
        browser_oauth=None,
    )


def test_yuehulian_gets_antidetect_dual_browser_context():
    ctx = build_store_context(_store('yuehulian'))
    assert '## BROWSER' in ctx
    assert 'DUAL BROWSER SYSTEM' in ctx


def test_yuehulian_omits_ziniao_autofill():
    # Auto-fill is ziniao SBP-specific; yuehulian envs are cookie-persisted.
    ctx = build_store_context(_store('yuehulian'))
    assert 'ZINIAO AUTO-FILL' not in ctx


def test_ziniao_twist_still_has_autofill_and_dual_browser():
    ctx = build_store_context(_store('ziniao'))
    assert 'ZINIAO AUTO-FILL' in ctx
    assert 'DUAL BROWSER SYSTEM' in ctx


def test_chrome_twist_is_not_antidetect():
    ctx = build_store_context(_store('chrome'))
    assert 'DUAL BROWSER SYSTEM' not in ctx
    assert 'ZINIAO AUTO-FILL' not in ctx
