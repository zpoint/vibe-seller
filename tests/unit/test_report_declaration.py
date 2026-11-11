"""The other half of the report-run declaration: the words the agent reads.

The deliverable check engages on a declaration and on nothing else, which
makes the declaration the only way a monthly run gets verified at all. So
the two facts have to be pinned together — a skill that describes the
monthly set without naming the tool leaves every real run unchecked, and a
skill that still describes the old folder trigger teaches an agent to
comply with a rule that no longer exists.

Both directions are checked here, along with the month vocabulary the
route validates against (``app.deliverables.declarations``).
"""

import datetime as dt
import pathlib
import re
import types

import pytest

from app.ai.tool_call_guards import planning_deny
from app.deliverables.declarations import (
    format_hint,
    month_error,
    parse_month,
    reporting_month,
)
from app.mcp_report_tool_schemas import REPORT_TOOLS

pytestmark = pytest.mark.unit

SKILLS = pathlib.Path('app/skills_v2')
DECLARE_TOOL = 'vibe_seller_declare_report_run'
# The phrase the skills use for the month-end download. A doc that says
# it is a doc the agent will read before deciding whether to declare.
RUN_PHRASE = 'report-download run'
# What the deleted trigger used to say, in the words the skills carried.
OLD_TRIGGER = 'holds any `reports_*`'


def _docs_with(phrase: str) -> list[pathlib.Path]:
    return [
        path
        for path in sorted(SKILLS.rglob('*.md'))
        if phrase in path.read_text(encoding='utf-8')
    ]


class TestTheSkillsTeachTheDeclaration:
    def test_some_skill_describes_the_monthly_run(self):
        # Guards this whole class from passing vacuously if the phrase or
        # the skill layout ever moves.
        assert _docs_with(RUN_PHRASE), 'no skill describes a monthly run'

    def test_every_doc_that_describes_a_run_names_the_tool(self):
        for path in _docs_with(RUN_PHRASE):
            text = path.read_text(encoding='utf-8')
            assert DECLARE_TOOL in text, (
                f'{path} tells the agent about the monthly set but never '
                'names the declaration that makes it owed'
            )

    def test_no_skill_still_teaches_the_folder_trigger(self):
        """The inference is gone, so the prose must be gone with it.

        An agent that reads "once the directory holds any `reports_*`
        folder you are checked" will either waste a run satisfying a rule
        that does not exist, or file its ad-hoc CSVs elsewhere to dodge one
        that does.
        """
        for path in sorted(SKILLS.rglob('*.md')):
            assert OLD_TRIGGER not in path.read_text(encoding='utf-8'), path

    def test_the_tool_says_a_single_report_is_not_a_run(self):
        """The judgement is the agent's, so the tool's own text must make
        it: one report for one marketplace is a lookup, and a lookup that
        declares gets refused for files nobody asked for."""

        description = REPORT_TOOLS[0]['description']
        assert 'One report' in description
        assert 'those are lookups' in description
        assert 'refused for reports nobody asked for' in description

    def test_the_tool_text_carries_no_shouting_or_history(self):
        """CLAUDE.md asks prompts and skills to drop the excess emphasis.

        Capitals are the tell of a reason that was not trusted to land on
        its own, and the history of the bug the tool replaced is not
        something the agent needs in order to use it.
        """

        description = REPORT_TOOLS[0]['description']
        shouted = {
            word
            for word in re.findall(r'\b[A-Z]{3,}\b', description)
            if word != 'YYYY'  # the month format, not an emphasis
        }
        assert not shouted, f'all-caps emphasis in the tool text: {shouted}'
        assert 'reports_*' not in description


class TestTheDeclaredMonth:
    """The month is the only thing the agent gets to pick, so it is the
    one thing the server checks."""

    TODAY = dt.date(2026, 11, 11)
    #: What a task created in November 2026 owes: October.
    EXPECTED = '2026-10'

    def _task(self, created_at):
        return types.SimpleNamespace(created_at=created_at)

    def test_a_plain_month_passes_through(self):
        assert parse_month('2026-10') == '2026-10'

    def test_surrounding_spaces_are_the_same_month(self):
        # The value goes into a path and a comparison; trimming at the
        # boundary means the stored row is always the canonical form.
        assert parse_month('  2026-10 ') == '2026-10'

    @pytest.mark.parametrize(
        'bad',
        [
            None,
            '',
            '2026-1',
            '2026-13',
            '2026-00',
            '26-10',
            '2026/10',
            '202610',
            'last month',
            'OCT-2026',
            202610,
        ],
    )
    def test_only_a_four_digit_year_and_a_two_digit_month_is_a_month(self, bad):
        assert parse_month(bad) is None

    def test_the_reporting_month_is_the_calendar_month_before(self):
        assert (
            reporting_month(self._task('2026-11-05T09:00:00+00:00'), self.TODAY)
            == '2026-10'
        )

    def test_a_retried_old_task_keeps_its_own_month(self):
        # A task created in October and re-run in November is still the
        # October run; grading it against October's data (now "September")
        # would ask for a month nobody was working on.
        assert (
            reporting_month(self._task('2026-10-31T09:00:00+00:00'), self.TODAY)
            == '2026-09'
        )

    def test_a_task_with_no_usable_date_falls_back_to_today(self):
        for created in (None, 'not a date', 2026):
            task = self._task(created)
            assert reporting_month(task, self.TODAY) == '2026-10', created

    def test_the_reporting_crosses_the_year(self):
        assert (
            reporting_month(self._task('2026-01-09T00:00:00+00:00'), self.TODAY)
            == '2025-12'
        )

    def test_the_derived_reporting_month_is_always_allowed(self):
        assert month_error(self.EXPECTED, self.EXPECTED, self.TODAY) is None

    def test_an_earlier_complete_month_is_allowed_as_a_back_fill(self):
        for month in ('2026-09', '2026-01', '2025-12', '2020-02'):
            assert month_error(month, self.EXPECTED, self.TODAY) is None, month

    def test_the_month_still_running_is_refused_naming_the_expected_one(self):
        error = month_error('2026-11', self.EXPECTED, self.TODAY)
        assert error is not None
        assert 'has not ended' in error
        assert self.EXPECTED in error, 'a refusal has to carry the right value'

    def test_a_future_month_is_refused(self):
        error = month_error('2027-02', self.EXPECTED, self.TODAY)
        assert error is not None and 'future' in error
        assert self.EXPECTED in error

    def test_the_hint_shows_the_form_by_example(self):
        assert 'YYYY-MM' in format_hint(self.EXPECTED)
        assert self.EXPECTED in format_hint(self.EXPECTED)


class TestDeclaringIsExecutionNotPlanning:
    """A plan-mode session decides WHAT to do; declaring a run commits it
    to a month of files, which is doing it. The CI observation that put
    the ad declaration here was a planner declaring before writing its
    plan — the same move here would grade a plan against a download."""

    DECLARE = f'mcp__vibe-seller__{DECLARE_TOOL}'

    def test_refused_while_planning(self):
        session = types.SimpleNamespace(
            mode='plan_then_execute', _executing=False
        )
        deny = planning_deny(session, self.DECLARE)
        assert deny and 'plan' in deny

    def test_allowed_once_executing(self):
        session = types.SimpleNamespace(
            mode='plan_then_execute', _executing=True
        )
        assert planning_deny(session, self.DECLARE) is None

    def test_allowed_in_auto_mode(self):
        session = types.SimpleNamespace(mode='auto', _executing=True)
        assert planning_deny(session, self.DECLARE) is None
