"""A follow-up on a COMPLETED task does not re-run the DoD reviewer.

A task whose deliverable was accepted got one more user message ("copy
the folder into the task directory"). The copy took seconds; the turn
then could not end, because every completion path demanded a fresh
reviewer verdict for it — the prior verdicts are rolled aside at each
turn start — and the reviewer re-verified the whole deliverable. A
tax-filing follow-up that only copied files ran four review rounds.

The marker (``report_reviewer.mark_followup``) is set by the message
endpoint when the task is COMPLETED at arrival; ``reviewer_verdict`` —
the one verdict the Stop hook, the ending-turn re-drive and
``set_task_result`` share — stands down on it. Gates on live writes
(listing upload verdicts, the ads execution review) do not.
"""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.ai import bash_safety
from app.ai.background_wait import check_blocking_sleep
from app.ai.bash_safety import check_exec_review_status, check_review_status
from app.ai.claude_backend import AgentSession
from app.ai.claude_backend_utils import AUTO_APPROVE_CALLBACK
from app.ai.stop_gates import report_reviewer as rr
from app.workspace import manager as mgr

pytestmark = pytest.mark.unit


@pytest.fixture
def review_bound(monkeypatch):
    """Bind a review-declaring (non-ad) skill to every task dir."""
    monkeypatch.setattr(
        bash_safety, 'recorded_skills', lambda _t: frozenset({'acme-skill'})
    )
    monkeypatch.setattr(
        bash_safety,
        'skills_requiring_review',
        lambda skills, _d: {'acme-skill': object()} if skills else {},
    )


class TestMarker:
    def test_unmarked_dir_is_not_a_followup(self, tmp_path):
        assert rr.is_followup(tmp_path) is False
        assert rr.is_followup(None) is False

    def test_mark_is_idempotent_and_none_safe(self, tmp_path):
        rr.mark_followup(None)  # must not raise
        rr.mark_followup(tmp_path)
        rr.mark_followup(tmp_path)
        assert rr.is_followup(tmp_path)

    def test_mark_never_creates_a_workspace(self, tmp_path):
        rr.mark_followup(tmp_path / 'missing')
        assert not (tmp_path / 'missing').exists()

    def test_marker_is_a_dotfile_hidden_from_task_files(self, tmp_path):
        # The task-files listing hides dotfiles; the marker is infra, not
        # a deliverable the user should see.
        assert rr.FOLLOWUP_MARKER.startswith('.')

    def test_marker_survives_turn_rollover(self, tmp_path):
        # A second follow-up is still a follow-up: rollover moves review
        # verdicts aside, never the marker.
        rr.mark_followup(tmp_path)
        (tmp_path / 'REVIEW_2026-07-16_iter1.md').write_text('Status: ok\n')
        rr.rollover_reviews(tmp_path)
        assert rr.is_followup(tmp_path)

    def test_retry_wipe_removes_the_marker(self, tmp_path, monkeypatch):
        """``/retry`` starts the task over; its first run is reviewed."""
        vibe = tmp_path / 'vibe'
        monkeypatch.setattr(mgr, 'VIBE_SELLER_DIR', vibe)
        monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(tmp_path / 'claude'))
        tid = 'abcd1234-0000-0000-0000-000000000000'
        ws = vibe / 'tasks' / tid
        ws.mkdir(parents=True)
        rr.mark_followup(ws)
        assert rr.is_followup(ws)
        mgr.reset_task_runtime_state(tid)
        assert not rr.is_followup(ws)


class TestReviewerStandsDown:
    def test_completed_followup_needs_no_reviewer(self, tmp_path):
        assert rr.reviewer_verdict(tmp_path) is not None  # first run
        rr.mark_followup(tmp_path)
        assert rr.reviewer_verdict(tmp_path) is None

    def test_failed_then_continued_is_still_reviewed(self, tmp_path):
        """No marker (the endpoint only marks COMPLETED tasks) → a run
        that failed and is continued still owes a verdict."""
        rr.rollover_reviews(tmp_path)  # the continued turn's init
        deny = rr.reviewer_verdict(tmp_path)
        assert deny is not None and 'Reviewer never ran' in deny

    def test_stop_hook_path_stands_down(self, tmp_path, review_bound):
        assert check_review_status(tmp_path) is not None
        rr.mark_followup(tmp_path)
        assert check_review_status(tmp_path) is None

    def test_ad_task_followup_stands_down(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            bash_safety, 'recorded_skills', lambda _t: frozenset({'noon-ads'})
        )
        assert check_review_status(tmp_path) is not None
        rr.mark_followup(tmp_path)
        assert check_review_status(tmp_path) is None

    def test_upload_verdict_gate_still_applies(self, tmp_path, review_bound):
        """A follow-up that uploads a listing batch still owes the
        parse-feedback verdict for it — that gates a live write."""
        rr.mark_followup(tmp_path)
        (tmp_path / 'UPLOAD_BATCH_100000000001.json').write_text(
            json.dumps({'batch_id': '100000000001'}), encoding='utf-8'
        )
        deny = check_review_status(tmp_path)
        assert deny is not None and '100000000001' in deny

    def test_exec_review_still_applies(self, tmp_path):
        """ "Now execute the plan" in a follow-up changes live bids; the
        execution reviewer still has to sign off."""
        rr.mark_followup(tmp_path)
        (tmp_path / 'EXECUTION_LOG.md').write_text('# log\n')
        deny = check_exec_review_status(tmp_path)
        assert deny is not None and 'Execution reviewer never ran' in deny


class TestLoopConduct:
    def test_every_deny_carries_the_conduct(self, tmp_path):
        """No-verdict, gaps and untrusted-verdict denies alike."""
        assert rr.REVIEW_LOOP_CONDUCT in rr.reviewer_verdict(tmp_path)
        name = 'REVIEW_2026-07-16_iter1.md'
        (tmp_path / name).write_text('Status: ok\n')
        deny = rr.reviewer_verdict(tmp_path, review_writers={name: 'main'})
        assert deny and deny.endswith(rr.REVIEW_LOOP_CONDUCT)

    def test_conduct_says_ok_ends_it_and_how_to_wait(self):
        text = rr.REVIEW_LOOP_CONDUCT
        assert 'non-blocking' in text and 'final reply' in text
        assert 'TaskOutput' in text and 'sleep' in text

    def test_followup_note_tells_the_agent_not_to_review(self):
        assert 'do NOT spawn a reviewer' in rr.FOLLOWUP_NOTE


class TestBlockingSleep:
    PENDING = 'reviewer-agent'

    @pytest.mark.parametrize(
        'cmd',
        [
            'sleep 200; ls REVIEW_2026-07-16_iter5.md',
            'sleep 30',
            'cd /tmp && sleep 45 && cat x',
            'sleep 1m',
            'sleep 0.5h',
        ],
    )
    def test_long_sleep_denied_while_work_pending(self, cmd):
        deny = check_blocking_sleep(cmd, self.PENDING)
        assert deny is not None
        assert 'TaskOutput' in deny and self.PENDING in deny

    @pytest.mark.parametrize(
        'cmd',
        [
            'sleep 20',  # a short poll between page checks
            'sleep 29.9',
            'echo "sleep 300"',  # not in command position
            'grep sleep notes.md',
            'until test -f x; do sleep 5; done',
        ],
    )
    def test_short_or_quoted_sleep_allowed(self, cmd):
        assert check_blocking_sleep(cmd, self.PENDING) is None

    def test_long_sleep_allowed_with_nothing_pending(self):
        assert check_blocking_sleep('sleep 300', '') is None
        assert check_blocking_sleep('sleep 300', None) is None


def _bash_hook(command, pending, background=False):
    """Run one Bash call through the live PreToolUse hook."""
    s = AgentSession(task_id='test-task', prompt='test', mode='auto')
    s._async_agents = dict(pending)
    sent = []
    s._send_hook_response = AsyncMock(
        side_effect=lambda rid, out: sent.append(out)
    )
    tool_input = {'command': command}
    if background:
        tool_input['run_in_background'] = True
    request = {
        'callback_id': AUTO_APPROVE_CALLBACK,
        'input': {'tool_name': 'Bash', 'tool_input': tool_input},
    }
    asyncio.run(s._handle_hook_callback('req-1', request))
    return sent[0].get('hookSpecificOutput', {})


class TestSleepGuardWiring:
    def test_foreground_sleep_denied_while_reviewer_runs(self):
        out = _bash_hook('sleep 200; ls REVIEW_*.md', {'tu1': 'agent-a'})
        assert out.get('permissionDecision') == 'deny'
        assert 'TaskOutput' in out['permissionDecisionReason']

    def test_backgrounded_sleep_is_not_blocking(self):
        out = _bash_hook('sleep 200', {'tu1': 'agent-a'}, background=True)
        assert out.get('permissionDecision') != 'deny'

    def test_sleep_allowed_once_nothing_is_pending(self):
        out = _bash_hook('sleep 200', {})
        assert out.get('permissionDecision') != 'deny'
