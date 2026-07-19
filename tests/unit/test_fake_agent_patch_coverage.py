"""Every module that imports ``agent_manager`` must be accounted for in
the workflow-test fake-agent patch list.

``install_fake_agent`` (tests/workflow/conftest.py) patches
``agent_manager`` per IMPORT SITE — a module that binds the manager at
import time and is not in that list silently calls the REAL manager
during workflow tests. This broke live: a new lifecycle module
(``task_runner_followup``) was extracted, follow-ups routed through it,
and every follow-up workflow test failed with an empty ``run_calls``
because the fake never saw the call.

This test moves the contract into code: any module importing
``agent_manager`` must appear either in the conftest patch list or in
the explicit allowlist below (modules whose manager use is not on any
workflow-test path). Adding a new import site forces a conscious choice.
"""

from pathlib import Path
import re

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]

# Modules that import agent_manager but are deliberately NOT patched:
# their manager use is not exercised by workflow tests (startup wiring,
# background reapers, deletion cleanup, schedule planning fan-out).
# If a workflow test starts exercising one of these, move it to the
# conftest patch list instead of extending this allowlist.
UNPATCHED_ALLOWLIST = {
    'app.main',
    'app.routers.schedule_planning',
    'app.scheduler.plan_reaper',
    'app.scheduler.stall_reaper',
    'app.task_delete',
    # Package facade re-export only — nothing on a workflow-test path
    # imports the manager through it.
    'app.ai.__init__',
}


def _import_sites() -> set[str]:
    sites = set()
    for py in (REPO / 'app').rglob('*.py'):
        text = py.read_text(encoding='utf-8', errors='ignore')
        # Match single-line AND parenthesized multiline import forms.
        if re.search(
            r'from app\.ai\.claude_backend_manager import '
            r'(?:\([^)]*\bagent_manager\b[^)]*\)|[^\n]*\bagent_manager\b)',
            text,
        ):
            rel = py.relative_to(REPO).with_suffix('')
            sites.add('.'.join(rel.parts))
    return sites


def _patched_sites() -> set[str]:
    conftest = (REPO / 'tests/workflow/conftest.py').read_text(encoding='utf-8')
    return set(re.findall(r"setattr\(\s*'([\w.]+)\.agent_manager'", conftest))


def test_all_agent_manager_import_sites_are_accounted_for():
    sites = _import_sites()
    assert sites, 'scanner found no import sites — regex broken?'
    unaccounted = sites - _patched_sites() - UNPATCHED_ALLOWLIST
    assert not unaccounted, (
        f'Modules import agent_manager but are neither patched by '
        f'install_fake_agent nor allowlisted: {sorted(unaccounted)}. '
        'If the module is on a workflow-test path, add it to the '
        'conftest patch list; otherwise add it to UNPATCHED_ALLOWLIST '
        'with a reason.'
    )


def test_allowlist_has_no_stale_entries():
    stale = UNPATCHED_ALLOWLIST - _import_sites()
    assert not stale, (
        f'Allowlist entries no longer import agent_manager: '
        f'{sorted(stale)} — remove them.'
    )
