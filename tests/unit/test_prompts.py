"""Unit tests for prompt loading and auto-mode derivation."""

import pytest

from app.prompts import DESIGN_SYSTEM_PROMPT, DESIGN_SYSTEM_PROMPT_AUTO

pytestmark = pytest.mark.unit


class TestPromptAutoDerivation:
    def test_auto_prompt_strips_plan_sections(self):
        """DESIGN_SYSTEM_PROMPT_AUTO has no ExitPlanMode or Phase 5."""
        # Plan mode prompt contains plan-specific sections
        assert 'ExitPlanMode' in DESIGN_SYSTEM_PROMPT
        assert 'Phase 5' in DESIGN_SYSTEM_PROMPT

        # Auto mode prompt strips them
        assert 'ExitPlanMode' not in DESIGN_SYSTEM_PROMPT_AUTO
        assert '## Phase 5' not in DESIGN_SYSTEM_PROMPT_AUTO

    def test_auto_prompt_preserves_shared_sections(self):
        """Knowledge Recall, Critical Thinking, etc. are preserved."""
        assert 'Knowledge Recall' in DESIGN_SYSTEM_PROMPT_AUTO
        assert 'Critical Thinking' in DESIGN_SYSTEM_PROMPT_AUTO
        assert 'Gather Required Info' in DESIGN_SYSTEM_PROMPT_AUTO
        assert 'Approach Selection' in DESIGN_SYSTEM_PROMPT_AUTO
        assert 'Subagent Delegation' in DESIGN_SYSTEM_PROMPT_AUTO

    def test_auto_prompt_no_plan_mode_delimiters(self):
        """HTML comment delimiters are stripped, not visible."""
        assert 'PLAN_MODE_ONLY' not in DESIGN_SYSTEM_PROMPT_AUTO
