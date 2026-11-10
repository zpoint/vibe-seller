"""The set_task_result attempt counter and the ad-rule thresholds.

Every soft gate is held to SOFT_GATE_MAX_DENIALS refusals per task by a
per-(task, gate) counter. Ad-rule thresholds are defaults a store's
``notes.md`` overrides — the gates read them through ``resolve_rules``.
"""

from __future__ import annotations

import pytest

from app.ai.stop_gates import (
    SOFT_GATE_MAX_DENIALS,
    _attempts,  # noqa: PLC2701 — tests inspect/clear the counter
    record_attempt,
    reset_attempts,
)
from app.ai.stop_gates.ad_rules import DEFAULT_RULES, resolve_rules


@pytest.fixture(autouse=True)
def _clear_attempts():
    _attempts.clear()
    yield
    _attempts.clear()


@pytest.mark.unit
class TestAttemptCounter:
    def test_record_attempt_increments(self):
        assert record_attempt('t1', 'g1') == 1
        assert record_attempt('t1', 'g1') == 2
        assert record_attempt('t1', 'g2') == 1
        assert record_attempt('t2', 'g1') == 1

    def test_reset_attempts_scoped_per_task(self):
        record_attempt('t1', 'g1')
        record_attempt('t2', 'g1')
        reset_attempts('t1')
        assert ('t1', 'g1') not in _attempts
        assert ('t2', 'g1') in _attempts

    def test_soft_max_denials_is_one(self):
        # The user explicitly asked: "let agent fix once but not
        # mandatory". This pins the contract.
        assert SOFT_GATE_MAX_DENIALS == 1


@pytest.mark.unit
class TestAdRules:
    """Single source of truth for bid-rule thresholds + notes.md override."""

    def test_defaults(self):
        assert DEFAULT_RULES['acos_no_lower'] == 25.0
        assert DEFAULT_RULES['scale_roas'] == 5.0

    def test_resolve_no_notes_returns_defaults(self):
        assert resolve_rules(None) == DEFAULT_RULES
        assert resolve_rules('') == DEFAULT_RULES
        # A copy, not the shared dict — mutating must not poison defaults.
        resolve_rules('scale_roas: 9')
        assert DEFAULT_RULES['scale_roas'] == 5.0

    def test_resolve_overrides_both_keys(self):
        notes = (
            '# demo-northshore notes\n\n## 广告规则 (ad-rules)\n'
            'scale_roas: 6\nacos_no_lower = 28\n'
        )
        r = resolve_rules(notes)
        assert r['scale_roas'] == 6.0
        assert r['acos_no_lower'] == 28.0

    def test_resolve_partial_override(self):
        r = resolve_rules('scale_roas: 7.5')
        assert r['scale_roas'] == 7.5
        assert r['acos_no_lower'] == 25.0  # untouched default

    def test_the_new_keys_have_defaults(self):
        assert DEFAULT_RULES['cpc_floor_multiplier'] == 1.1
        assert DEFAULT_RULES['margin'] == 25.0

    def test_every_default_is_overridable_from_notes(self):
        notes = '\n'.join(f'{k}: 0.5' for k in DEFAULT_RULES)
        assert set(resolve_rules(notes).values()) == {0.5}

    def test_a_space_may_stand_for_an_underscore(self):
        r = resolve_rules('CPC floor multiplier = 1.0')
        assert r['cpc_floor_multiplier'] == 1.0
