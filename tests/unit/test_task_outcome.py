"""Terminal outcome resolution — app/task_outcome.py.

The invariant these pin: **a task's terminal status may not contradict
the deliverable it is holding.** A scheduled ad-audit run shipped FAILED
while a finished report sat in the row, because status was derived from
``if task.error:`` by a reader that never looked at ``task.result``, and
three different writers could set that pair at three different times.
"""

import pytest

from app.task_outcome import (
    OutcomeKind,
    TaskOutcome,
    apply_outcome,
    resolve_outcome,
)
from app.task_states import TaskStatus

pytestmark = pytest.mark.unit


class _Row:
    """Minimal stand-in for a Task row (resolve_outcome is pure)."""

    def __init__(self, **kw):
        self.result = kw.get('result')
        self.accepted_result = kw.get('accepted_result')
        self.submitted_result = kw.get('submitted_result')
        self.review_gaps = kw.get('review_gaps')
        self.transcript_tail = kw.get('transcript_tail')
        self.error = kw.get('error')
        self.error_category = kw.get('error_category')


class TestPrecedence:
    """accepted → retained submission → prose → nothing."""

    def test_accepted_result_wins(self):
        out = resolve_outcome(
            _Row(
                accepted_result='# Report',
                submitted_result='older',
                transcript_tail='chatter',
            )
        )
        assert out.kind is OutcomeKind.DELIVERED
        assert out.content == '# Report'

    def test_retained_submission_beats_prose(self):
        out = resolve_outcome(
            _Row(
                submitted_result='# Report (95%)',
                review_gaps='["noon AE 缺少搜索词层"]',
                transcript_tail='我来开始执行广告审核任务。',
            )
        )
        assert out.kind is OutcomeKind.INCOMPLETE
        assert out.content == '# Report (95%)'
        assert out.caveats == ('noon AE 缺少搜索词层',)

    def test_prose_is_a_real_fallback(self):
        """Most tasks never call set_task_result; chat IS the answer."""
        out = resolve_outcome(_Row(transcript_tail='The price is 42.'))
        assert out.kind is OutcomeKind.DELIVERED
        assert out.content == 'The price is 42.'

    def test_nothing_at_all_fails_but_invents_no_reason(self):
        """ "Produced nothing" is not the same as "failed with a reason".

        The finalizer still has to tell an agent awaiting input (which
        legitimately has no result yet → WAITING) from an empty run.
        Synthesising a reason here would collapse both into FAILED.
        """
        out = resolve_outcome(_Row())
        assert out.kind is OutcomeKind.FAILED
        assert out.reason is None
        assert out.category is None

    def test_blank_strings_do_not_count_as_deliverables(self):
        out = resolve_outcome(
            _Row(
                accepted_result='   ',
                submitted_result='\n',
                transcript_tail='',
            )
        )
        assert out.kind is OutcomeKind.FAILED


class TestErrorHandling:
    def test_infra_error_outranks_any_prose(self):
        """Narration about a browser that never started is not output."""
        out = resolve_outcome(
            _Row(
                error='Ziniao failed to launch',
                error_category='browser_launch_failed',
                transcript_tail='Let me try starting the browser again…',
            )
        )
        assert out.kind is OutcomeKind.FAILED
        assert out.reason == 'Ziniao failed to launch'

    def test_infra_error_outranks_even_an_accepted_result(self):
        out = resolve_outcome(
            _Row(
                accepted_result='# Report',
                error='stopped by user',
                error_category='stopped_by_user',
            )
        )
        assert out.kind is OutcomeKind.FAILED

    def test_agent_error_over_a_result_is_a_caveat_not_a_failure(self):
        out = resolve_outcome(
            _Row(
                accepted_result='# Report',
                error='noon SA could not be finished',
                error_category='agent_reported',
            )
        )
        assert out.kind is OutcomeKind.DELIVERED
        assert out.caveats == ('noon SA could not be finished',)

    def test_agent_error_plus_retained_submission_merges_caveats(self):
        out = resolve_outcome(
            _Row(
                submitted_result='# Report (95%)',
                review_gaps='["gap one", "gap two"]',
                error='reviewer kept refusing the format',
                error_category='agent_reported',
            )
        )
        assert out.kind is OutcomeKind.INCOMPLETE
        assert out.caveats == (
            'gap one',
            'gap two',
            'reviewer kept refusing the format',
        )

    def test_agent_error_with_only_prose_still_fails(self):
        """THE REGRESSION.

        Exactly the shape of the reported bug: every submission was
        refused (so no result and — on the old build — no retained
        submission), the agent used the error channel as its exit, and
        the end-of-turn fallback filed its own narration as the result.
        The run must not complete showing a chat transcript; it fails,
        carrying the agent's own message.
        """
        out = resolve_outcome(
            _Row(
                transcript_tail='我来开始执行广告审核任务。先加载必要的技能。',
                error='服务器完整性审核器持续报告格式不匹配',
                error_category='agent_reported',
            )
        )
        assert out.kind is OutcomeKind.FAILED
        assert out.reason == '服务器完整性审核器持续报告格式不匹配'

    def test_that_same_run_completes_once_the_submission_is_retained(self):
        """…and with retention on, it ships the report instead.

        Same run as above plus the retained submission that the old
        refusal path discarded — the whole point of keeping it.
        """
        out = resolve_outcome(
            _Row(
                submitted_result='# AD_AUDIT (95% complete)',
                review_gaps='["noon AE 6 个活动缺少搜索词层"]',
                transcript_tail='我来开始执行广告审核任务。',
                error='服务器完整性审核器持续报告格式不匹配',
                error_category='agent_reported',
            )
        )
        assert out.kind is OutcomeKind.INCOMPLETE
        assert out.status is TaskStatus.COMPLETED
        assert '95% complete' in out.content


class TestMalformedGaps:
    @pytest.mark.parametrize(
        'raw', ['not json', '{"a": 1}', '[]', 'null', '["", "  "]']
    )
    def test_bad_review_gaps_never_raise(self, raw):
        out = resolve_outcome(_Row(submitted_result='x', review_gaps=raw))
        assert out.kind is OutcomeKind.INCOMPLETE
        assert out.caveats == ()


class TestStatusMapping:
    def test_incomplete_completes(self):
        """A partial deliverable with an honest gap list is not a failure."""
        assert (
            TaskOutcome(kind=OutcomeKind.INCOMPLETE, content='x').status
            is TaskStatus.COMPLETED
        )

    def test_delivered_completes(self):
        assert (
            TaskOutcome(kind=OutcomeKind.DELIVERED, content='x').status
            is TaskStatus.COMPLETED
        )

    def test_failed_fails(self):
        assert (
            TaskOutcome(kind=OutcomeKind.FAILED, reason='x').status
            is TaskStatus.FAILED
        )

    def test_every_kind_is_mapped(self):
        """No kind may be added without deciding its terminal status."""
        for kind in OutcomeKind:
            assert TaskOutcome(kind=kind, content='x').status is not None


class TestRendered:
    def test_caveats_are_appended(self):
        out = TaskOutcome(
            kind=OutcomeKind.INCOMPLETE,
            content='# Report',
            caveats=('gap one', 'gap two'),
        )
        text = out.rendered()
        assert text.startswith('# Report')
        assert 'gap one' in text and 'gap two' in text

    def test_no_caveats_leaves_content_untouched(self):
        out = TaskOutcome(kind=OutcomeKind.DELIVERED, content='# Report')
        assert out.rendered() == '# Report'


class TestApplyOutcome:
    def test_non_failed_clears_the_error_pair(self):
        """Otherwise a downstream ``if task.error:`` resurrects the bug."""
        row = _Row(
            accepted_result='# Report',
            error='partial note',
            error_category='agent_reported',
        )
        apply_outcome(row, resolve_outcome(row))
        assert row.error is None
        assert row.error_category is None
        assert 'partial note' in row.result  # kept, as a caveat

    def test_failed_records_reason_and_category(self):
        row = _Row(error='browser down', error_category='browser_failed')
        apply_outcome(row, resolve_outcome(row))
        assert row.error == 'browser down'
        assert row.error_category == 'browser_failed'

    def test_is_idempotent(self):
        """Finalizers may run more than once; the verdict must not drift."""
        row = _Row(submitted_result='# Report', review_gaps='["only gap"]')
        apply_outcome(row, resolve_outcome(row))
        first = row.result
        apply_outcome(row, resolve_outcome(row))
        assert row.result == first

    def test_is_idempotent_when_an_agent_error_became_a_caveat(self):
        """The caveat must survive the pass that cleared its source.

        ``apply_outcome`` clears ``error`` after folding it into the
        result. If that were the caveat's only home, the SECOND resolve
        (end-of-turn writes once, cleanup again) would render the same
        deliverable with the caveat silently gone.
        """
        row = _Row(
            submitted_result='# Report',
            review_gaps='["gate gap"]',
            error='agent could not finish noon',
            error_category='agent_reported',
        )
        apply_outcome(row, resolve_outcome(row))
        first = row.result
        assert 'gate gap' in first
        assert 'could not finish noon' in first

        apply_outcome(row, resolve_outcome(row))
        assert row.result == first
