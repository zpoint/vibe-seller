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
    def test_unmarked_task_is_not_a_followup(self):
        assert rr.is_followup('t-unmarked') is False
        assert rr.is_followup('') is False

    def test_mark_is_idempotent(self):
        rr.mark_followup('t-1')
        rr.mark_followup('t-1')
        assert rr.is_followup('t-1')

    def test_marker_lives_outside_the_workspace(self, tmp_path):
        """The agent works in the task dir; a marker it can ``ls`` is a
        marker it investigates (observed in CI)."""
        tid = tmp_path.name
        rr.mark_followup(tid)
        assert rr.is_followup(tid)
        assert list(tmp_path.iterdir()) == []

    def test_unsafe_id_cannot_escape_the_dir(self):
        rr.mark_followup('../../etc/x')
        assert all(
            p.parent == rr.FOLLOWUP_DIR for p in rr.FOLLOWUP_DIR.iterdir()
        )

    def test_marker_survives_turn_rollover(self, tmp_path):
        """A second follow-up is still a follow-up."""
        rr.mark_followup(tmp_path.name)
        (tmp_path / 'REVIEW_2026-07-16_iter1.md').write_text('Status: ok\n')
        rr.rollover_reviews(tmp_path)
        assert rr.is_followup(tmp_path.name)

    def test_clear_forgets_it(self):
        """``/retry`` and task deletion clear it: a retried task's first
        run is reviewed again."""
        rr.mark_followup('t-2')
        rr.clear_followup('t-2')
        assert not rr.is_followup('t-2')
        rr.clear_followup('t-never-marked')  # must not raise


class TestReviewerStandsDown:
    def test_completed_followup_needs_no_reviewer(self, tmp_path):
        assert rr.reviewer_verdict(tmp_path) is not None  # first run
        rr.mark_followup(tmp_path.name)
        assert rr.reviewer_verdict(tmp_path) is None

    def test_failed_then_continued_is_still_reviewed(self, tmp_path):
        """No marker (the endpoint only marks COMPLETED tasks) → a run
        that failed and is continued still owes a verdict."""
        rr.rollover_reviews(tmp_path)  # the continued turn's init
        deny = rr.reviewer_verdict(tmp_path)
        assert deny is not None and 'Reviewer never ran' in deny

    def test_stop_hook_path_stands_down(self, tmp_path, review_bound):
        assert check_review_status(tmp_path) is not None
        rr.mark_followup(tmp_path.name)
        assert check_review_status(tmp_path) is None

    def test_ad_task_followup_stands_down(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            bash_safety, 'recorded_skills', lambda _t: frozenset({'noon-ads'})
        )
        assert check_review_status(tmp_path) is not None
        rr.mark_followup(tmp_path.name)
        assert check_review_status(tmp_path) is None

    def test_upload_verdict_gate_still_applies(self, tmp_path, review_bound):
        """A follow-up that uploads a listing batch still owes the
        parse-feedback verdict for it — that gates a live write."""
        rr.mark_followup(tmp_path.name)
        (tmp_path / 'UPLOAD_BATCH_100000000001.json').write_text(
            json.dumps({'batch_id': '100000000001'}), encoding='utf-8'
        )
        deny = check_review_status(tmp_path)
        assert deny is not None and '100000000001' in deny

    def test_exec_review_still_applies(self, tmp_path):
        """ "Now execute the plan" in a follow-up changes live bids; the
        execution reviewer still has to sign off."""
        rr.mark_followup(tmp_path.name)
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
        assert rr.WAIT_FOR_BACKGROUND in text

    def test_wait_rule_does_not_depend_on_an_optional_tool(self):
        """A CI run's CLI had no TaskOutput; the rule must still say what
        to do — short waits under the guard's threshold."""
        text = rr.WAIT_FOR_BACKGROUND
        assert 'under 30s' in text and 'if your tools include it' in text

    def test_followup_note_tells_the_agent_not_to_review(self):
        assert 'Do not spawn a reviewer' in rr.FOLLOWUP_NOTE

    def test_followup_note_does_not_say_followup(self):
        """An earlier wording ("Follow-up on a task…") sent an agent to
        load an unrelated skill named ``*-followup-*``. The note is about
        the review only."""
        assert 'follow' not in rr.FOLLOWUP_NOTE.lower()


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
        assert rr.WAIT_FOR_BACKGROUND in deny and self.PENDING in deny

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
        assert rr.WAIT_FOR_BACKGROUND in out['permissionDecisionReason']

    def test_backgrounded_sleep_is_not_blocking(self):
        out = _bash_hook('sleep 200', {'tu1': 'agent-a'}, background=True)
        assert out.get('permissionDecision') != 'deny'

    def test_sleep_allowed_once_nothing_is_pending(self):
        out = _bash_hook('sleep 200', {})
        assert out.get('permissionDecision') != 'deny'
