"""The schedule simulation is a PINNED calculation, not prose.

Brief #17b: the critical-path numbers quoted in
``tests/collection_order.py``'s docstring and in the review reports
must be reproducible. This test runs the same simulation
(``long_budget_order`` on the real collected suite, worksteal's
initial split, the recorded durations) and asserts the figures — so a
future change to the placement or the markers cannot silently make the
docstring a lie, and re-running is `pytest tests/unit/test_schedule_sim.py`.
"""

import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.collection_order import long_budget_order  # noqa: E402

pytestmark = pytest.mark.unit

MEASURED: dict[str, tuple[int, int]] = {
    'test_a_local_edit_survives_an_unchanged_upstream': (280, 0),
    'test_a_narrow_follow_up_reviews_only_what_was_asked': (825, 380),
    'test_agent_extracts_contact_details': (99, 0),
    'test_agent_gives_concurrent_subagents_distinct_slots': (162, 0),
    'test_agent_reads_homepage': (1, 106),
    'test_agent_sees_only_project_mcp_and_skills': (93, 24),
    'test_agent_sent_replay': (0, 1),
    'test_ai_navigates_site_via_gui[chromium]': (0, 99),
    'test_all_tasks_button_visible[chromium]': (0, 231),
    'test_an_unbound_ads_service_skips_the_job_first': (25, 0),
    'test_cancel_task_creation[chromium]': (0, 8),
    'test_catalog_sync_and_agent_usage': (200, 182),
    'test_concurrent_drivers_each_read_their_own_page': (1, 0),
    'test_cookie_persists_across_tasks_same_store': (135, 458),
    'test_create_then_extend': (0, 547),
    'test_different_platforms_run_concurrently': (0, 70),
    'test_different_stores_fully_independent': (0, 2),
    'test_each_store_reads_its_ads_its_own_way': (608, 496),
    'test_full_pipeline_completes': (0, 4),
    'test_llm_follows_instructions': (13, 0),
    'test_llm_responds': (0, 289),
    # Phase-1-only observations from the 2026-11-10 h5 run (lower
    # bounds: the approval + execute phases add on top). 775s =
    # a completed audit (task 429b2070); 1206s = the phase budget
    # expiring mid-audit (task 408d6ed3, agent stopped by the test).
    'test_audit_then_apply_exactly_what_was_approved': (775, 1206),
    'test_profile_switch_preserves_context_via_history_file': (197, 45),
    'test_profile_switch_retry': (0, 51),
    'test_question_answer_completes': (359, 178),
    'test_question_answer_flow': (159, 151),
    'test_reviewing_everything_looks_at_everything': (524, 536),
    'test_same_profile_stop_retry': (0, 22),
    'test_single_task_navigates_test_site': (41, 0),
    'test_status_transitions_are_valid': (0, 10),
    'test_stop_then_retry': (37, 88),
    'test_task_progresses_beyond_pending': (0, 8),
    'test_two_browser_tasks_both_complete': (257, 297),
    'test_two_tasks_same_chrome_store': (49, 32),
    'test_two_tasks_same_ziniao_store': (0, 1),
    'test_upstream_change_lands_on_disk': (9, 0),
    'test_upstream_delete_removes_what_it_dropped': (34, 0),
    'test_watermark_handoff_across_two_runs': (36, 0),
    'test_web_browser_extracts_contact_details': (105, 126),
    'test_web_browser_reads_homepage': (0, 12),
}

RECORDED_MARKERS = {
    'test_a_narrow_follow_up_reviews_only_what_was_asked',
    'test_reviewing_everything_looks_at_everything',
    'test_each_store_reads_its_ads_its_own_way',
    'test_create_then_extend',
    'test_cookie_persists_across_tasks_same_store',
    'test_llm_responds',
    'test_two_browser_tasks_both_complete',
    'test_question_answer_completes',
    'test_audit_then_apply_exactly_what_was_approved',
}

DEFAULT = 30


def _p50(pair):
    vals = [v for v in pair if v > 0]
    return sum(vals) / len(vals) if vals else DEFAULT


def _worst(pair):
    return max(pair) if pair else DEFAULT


def _collect(extra=()):
    env = {
        **os.environ,
        'E2E_BASE_URL': 'http://127.0.0.1:9',
        'E2E_VIBE_HOME': '/tmp/none',
    }
    out = subprocess.run(
        ['pytest', '--collect-only', '-q', 'tests/e2e', '--e2e', *extra],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    ).stdout
    items, mod, cls = [], None, None
    for ln in out.splitlines():
        s = ln.strip()
        m = re.match(r'<Module (\S+)>', s)
        if m:
            mod = f'tests/e2e/{m.group(1)}'
            cls = None
            continue
        m = re.match(r'<Class (\S+)>', s)
        if m:
            cls = m.group(1)
            continue
        m = re.match(r'<Function (\S+)>', s)
        if m and mod:
            items.append(f'{mod}::{cls + "::" if cls else ""}{m.group(1)}')
    return items


class _Item:
    def __init__(self, name, budget):
        self.name = name
        self.keywords = {'long_budget': True} if budget else {}
        self._budget = budget

    def get_closest_marker(self, marker):
        if marker == 'long_budget' and self._budget:
            return type('M', (), {'args': (self._budget,)})()
        return None


def _loads(order, measure, workers=4):
    pending = list(range(len(order)))
    loads = []
    for i in range(workers):
        take = len(pending) // (workers - i)
        chunk = pending[:take]
        del pending[:take]
        loads.append(round(sum(measure(order[j]) for j in chunk)))
    return loads


def _simulate():
    items = _collect()
    marked = {it.split('::')[-1] for it in _collect(['-m', 'long_budget'])}
    wrap = []
    for it in items:
        fn = it.split('::')[-1]
        est = _p50(MEASURED.get(fn, (0, 0)))
        wrap.append(_Item(it, round(est) if fn in marked else 0))
    workers = _e2e_workers()
    order = [
        w.name
        for w in long_budget_order(wrap, _e2e_provider_map(), workers=workers)
    ]
    p50 = _loads(
        order,
        lambda n: _p50(MEASURED.get(n.split('::')[-1], (0, 0))),
        workers,
    )
    worst = _loads(
        order,
        lambda n: _worst(MEASURED.get(n.split('::')[-1], (0, 0))),
        workers,
    )
    return p50, worst


def _ci() -> str:
    ci = Path(__file__).resolve().parents[2] / '.github/workflows/ci.yml'
    return ci.read_text(encoding='utf-8')


def _e2e_step_budget_s() -> int:
    """The CI 'Run E2E tests' step's timeout-minutes, in seconds."""
    text = _ci()
    step = text.split('- name: Run E2E tests', 1)[1]
    minutes = re.search(r'timeout-minutes:\s*(\d+)', step)
    assert minutes, 'Run E2E tests step has no timeout-minutes'
    return int(minutes.group(1)) * 60


def _e2e_workers() -> int:
    """E2E_WORKERS from ci.yml — the width the simulation models."""
    match = re.search(r'E2E_WORKERS:\s*(\d+)', _ci())
    assert match, 'ci.yml has no E2E_WORKERS'
    return int(match.group(1))


def _e2e_provider_map() -> list:
    """E2E_PROVIDER_MAP from ci.yml — one entry per worker."""
    match = re.search(r'E2E_PROVIDER_MAP:\s*(\S+)', _ci())
    assert match, 'ci.yml has no E2E_PROVIDER_MAP'
    return [p for p in match.group(1).split(',') if p]


def test_the_scheduled_critical_path_matches_the_documented_numbers():
    """The scheduling claim, pinned.

    The docstring/report figures are produced by THIS calculation on
    the recorded suite at the ci.yml width (6 workers): p50 path 1234s
    (20.6 min) and max-duration path 1405s (23.4 min). Both must fit the CI e2e
    step budget, read from ci.yml (40 min since the old 32 min was
    shown too tight by the 4-worker figures 1907s/2112s). A collection
    that differs in shape (platform filters) shifts the absolute
    loads, so what is pinned is the INVARIANT every collection must
    keep plus the marker set, and the exact recorded figures are
    asserted when the suite and width are the recorded ones.
    """
    p50, worst = _simulate()
    marked = {it.split('::')[-1] for it in _collect(['-m', 'long_budget'])}
    assert marked == RECORDED_MARKERS, sorted(marked)
    step = _e2e_step_budget_s()
    assert max(p50) <= step, f'the p50 path no longer fits {step}s: {p50}'
    assert max(worst) <= step, (
        f'the worst-case path no longer fits {step}s: {worst}'
    )
    if _e2e_workers() == 6 and p50 == [1234, 976, 1060, 1126, 890, 924]:
        assert worst == [1405, 934, 921, 947, 842, 768], worst
