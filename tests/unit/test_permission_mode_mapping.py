"""Pin the agent-mode → ``--permission-mode`` CLI flag mapping.

FakeAgent-based workflow tests can verify that the router passed
``mode='plan_then_execute'`` to the agent manager, but they do NOT
exercise the CLI flag that actually turns on plan mode in Claude
Code. If ``permission_mode_for_agent`` regresses (e.g. someone
"simplifies" the branch), plan-only Tasks silently start in
``bypassPermissions`` and their ExitPlanMode calls fail with
``"You are not in plan mode"`` — exactly the production bug fixed
in 0d3a14c.

This unit test pins the mapping directly so that class of bug is
caught at build time rather than from a sad schedule.
"""

import pytest

from app.ai.claude_backend import permission_mode_for_agent

pytestmark = pytest.mark.unit


class TestPermissionModeMapping:
    def test_plan_then_execute_maps_to_plan(self):
        """plan_then_execute MUST map to 'plan' — without it,
        ExitPlanMode fails in Claude Code."""
        assert permission_mode_for_agent('plan_then_execute') == 'plan'

    def test_execute_maps_to_bypass_permissions(self):
        """Past-planning runs want full access."""
        assert permission_mode_for_agent('execute') == 'bypassPermissions'

    def test_auto_maps_to_bypass_permissions(self):
        """Auto-mode (no plan phase) also wants full access."""
        assert permission_mode_for_agent('auto') == 'bypassPermissions'

    def test_unknown_mode_defaults_to_bypass(self):
        """Unknown/future modes default to bypassPermissions (not
        plan) — guard against accidentally landing a new task
        variant in plan mode without explicit opt-in."""
        assert permission_mode_for_agent('woken') == 'bypassPermissions'
        assert permission_mode_for_agent('future-mode') == 'bypassPermissions'
