"""A monthly report run is checked because it said so — not by its folders.

The journey that broke: a user asked an ad-hoc "show me current Amazon
sales", the agent saved its CSVs into ``reports_sales_2026-11/``, and the
result was refused for missing last month's MonthlyTransaction.csv. The
deliverable check had inferred an obligation from the shape of the agent's
own output, which is the one thing this project's gates are not allowed to
do.

What is pinned here is the replacement, end to end: the declare route, the
months the server will accept, and a submit that is refused once and then
ships with its gaps as caveats (the one-round rule).
"""

import asyncio
import datetime as dt
import json
import types

import pytest

from app.agent_mcp.registry import tool_definitions
from app.browser.wrapper import store_slug
from app.config import VIBE_SELLER_DIR
from app.deliverables.manifest import derive_manifest, month_before
from app.mcp_tool_schemas import TOOLS
from tests.workflow.conftest import wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow

# A back-fill month far enough past that nothing about it can be excused
# as upstream latency, and old enough that no other test can collide with
# its filenames in the shared task directory.
_BACKFILL = '2025-03'


def _reporting_month() -> str:
    """The month before this one — what the server derives for a task
    created today."""
    today = dt.date.today()
    return (
        f'{today.year - 1:04d}-12'
        if today.month == 1
        else f'{today.year:04d}-{today.month - 1:02d}'
    )


def _this_month() -> str:
    today = dt.date.today()
    return f'{today.year:04d}-{today.month:02d}'


def _next_month() -> str:
    today = dt.date.today()
    return (
        f'{today.year + 1:04d}-01'
        if today.month == 12
        else f'{today.year:04d}-{today.month + 1:02d}'
    )


async def _running_task(admin_client, install_fake_agent, name, *, store=True):
    """A task whose fake agent stays alive, so submits reach the gates.

    ``store=False`` is the orchestrator case: a non-store task always runs
    in plan mode, so it never reaches RUNNING and nothing holds it open —
    there is no result to submit and the declaration is refused anyway.
    """
    store_id = None
    if store:
        gate = asyncio.Event()
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='streamed narration from the agent', gate=gate
        )
        r = await admin_client.post(
            '/api/stores',
            json={
                'name': name,
                # Two marketplaces, so one monthly file is provably not the
                # whole set: a single-marketplace store with no declared
                # capabilities owes exactly one file, which would make the
                # "one report is not a run" test pass before the fix too.
                'platform_countries': {'amazon': ['SA', 'AE']},
            },
        )
        store_id = r.json()['id']
    else:
        gate = None
    r = await admin_client.post(
        '/api/tasks',
        json={'title': 'Download the monthly reports', 'store_id': store_id},
    )
    task_id = r.json()['id']
    if store:
        for _ in range(200):
            data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
            if data['status'] == 'running':
                break
            await asyncio.sleep(0.02)
        assert data['status'] == 'running', data['status']
    return task_id, gate


async def _declare(admin_client, task_id, month):
    return await admin_client.post(
        f'/api/tasks/{task_id}/report-declaration', json={'month': month}
    )


def _task_dir(task_id):
    return VIBE_SELLER_DIR / 'tasks' / task_id


def _deliver(task_id, paths):
    """Write every path in *paths* into the task workspace, with a row."""
    root = _task_dir(task_id)
    for rel in paths:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('a,b\n1,2\n', encoding='utf-8')


async def _monthly_ledger(admin_client, task_id, month):
    """This task's store's monthly transaction file, as the server names it.

    Derived here with the product's own function rather than spelled out,
    because a test that retypes ``reports_10_sa_acme/2026Oct…`` pins the
    naming twice and drifts silently the day the layout changes.
    """
    task = (await admin_client.get(f'/api/tasks/{task_id}')).json()
    store = (await admin_client.get(f'/api/stores/{task["store_id"]}')).json()
    row = types.SimpleNamespace(
        id=store['id'],
        name=store['name'],
        platform_countries=json.dumps(store['platform_countries']),
        capabilities='{}',  # what POST /api/stores leaves it at
    )
    entries = derive_manifest(
        row,
        store_slug(store['name'], store['id']),
        month,
        fee_month=month_before(month),
    )
    return next(d.relpath for d in entries if 'MonthlyTransaction' in d.relpath)


class TestDeclaringAMonthlyRun:
    async def test_a_declared_run_is_accepted_and_reads_back(
        self, admin_client, install_fake_agent
    ):
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Declare Store'
        )
        r = await _declare(admin_client, task_id, _reporting_month())
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['seq'] == 1
        assert body['month'] == _reporting_month()
        # The answer names what the run owes, derived server-side, so the
        # agent is never working against a rule it cannot see.
        assert any(
            'MonthlyTransaction.csv' in p for p in body['expected_files']
        ), body

        r = await admin_client.get(f'/api/tasks/{task_id}/report-declarations')
        assert [d['month'] for d in r.json()] == [_reporting_month()]
        gate.set()
        await wait_for_task(admin_client, task_id)

    async def test_a_back_fill_month_is_accepted_and_owed_by_name(
        self, admin_client, install_fake_agent
    ):
        """An explicit back-fill declares its own month and owes that one.

        The folder scan could only ever check the calendar's previous
        month, so a run asked to pull March 2025 in November had no way to
        be verified at all — and declaring March would have been refused.
        """
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Backfill Store'
        )
        r = await _declare(admin_client, task_id, _BACKFILL)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['reporting_month'] == _reporting_month()
        owed = body['expected_files']
        assert await _monthly_ledger(admin_client, task_id, _BACKFILL) in owed
        # This month's ledger is not owed by a March run: the declared
        # month is the denominator, not the calendar's preference.
        assert (
            await _monthly_ledger(admin_client, task_id, _reporting_month())
            not in owed
        )
        assert _BACKFILL in body['note']

        # And the check that follows is for March, not for this month.
        submit = await admin_client.post(
            f'/api/tasks/{task_id}/result', json={'result': 'march done'}
        )
        assert submit.status_code == 400
        assert _BACKFILL in submit.text
        gate.set()
        await wait_for_task(admin_client, task_id)

    async def test_declarations_accumulate_and_none_is_rewritten(
        self, admin_client, install_fake_agent
    ):
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Append Store'
        )
        assert (
            await _declare(admin_client, task_id, _reporting_month())
        ).status_code == 200
        r = await _declare(admin_client, task_id, '2025-06')
        assert r.status_code == 200, r.text
        assert r.json()['seq'] == 2

        r = await admin_client.get(f'/api/tasks/{task_id}/report-declarations')
        rows = r.json()
        assert [d['seq'] for d in rows] == [1, 2]
        assert [d['month'] for d in rows] == [_reporting_month(), '2025-06']
        gate.set()
        await wait_for_task(admin_client, task_id)


class TestTheServerPicksTheAllowedMonths:
    """The agent names a month; it cannot choose which months are checkable.

    A declared month is the run's denominator, so the server keeps it a
    complete calendar month: the one still running would fail a run for
    data that cannot exist yet, and a future one is not a report at all.
    """

    async def test_the_month_still_running_is_refused_naming_the_expected_one(
        self, admin_client, install_fake_agent
    ):
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Running Store'
        )
        r = await _declare(admin_client, task_id, _this_month())
        assert r.status_code == 400, r.text
        detail = r.json()['detail']
        assert 'has not ended' in detail
        assert _reporting_month() in detail, 'the no has to carry the yes'
        gate.set()
        await wait_for_task(admin_client, task_id)

    async def test_a_future_month_is_refused(
        self, admin_client, install_fake_agent
    ):
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, 'Future Store'
        )
        r = await _declare(admin_client, task_id, _next_month())
        assert r.status_code == 400, r.text
        assert 'future' in r.json()['detail']
        gate.set()
        await wait_for_task(admin_client, task_id)

    @pytest.mark.parametrize('bad', ['2025-3', 'last month', '', '2025-13'])
    async def test_a_value_that_is_not_a_month_is_refused(
        self, admin_client, install_fake_agent, bad
    ):
        task_id, gate = await _running_task(
            admin_client, install_fake_agent, f'Bad Month {bad or "empty"}'
        )
        r = await _declare(admin_client, task_id, bad)
        assert r.status_code == 400, r.text
        assert 'YYYY-MM' in r.json()['detail']
        gate.set()
        await wait_for_task(admin_client, task_id)

    async def test_a_task_with_no_store_cannot_declare_one(
        self, admin_client, install_fake_agent
    ):
        """The file list comes from a store row; an orchestrator has none."""
        task_id, _gate = await _running_task(
            admin_client, install_fake_agent, 'No Store Here', store=False
        )
        r = await _declare(admin_client, task_id, _reporting_month())
        assert r.status_code == 400, r.text
        assert 'store' in r.json()['detail']
        # Nothing was recorded, so nothing will be checked later.
        r = await admin_client.get(f'/api/tasks/{task_id}/report-declarations')
        assert r.json() == []


class TestTheSubmitFollowsTheDeclaration:
    async def test_a_declared_run_with_gaps_is_refused_once_then_ships(
        self, admin_client, install_fake_agent
    ):
        """The one-round rule on the real gate, not a stubbed one."""
        task_id, hold = await _running_task(
            admin_client, install_fake_agent, 'Gappy Store'
        )
        r = await _declare(admin_client, task_id, _reporting_month())
        assert r.status_code == 200, r.text
        owed = r.json()['expected_files']
        url = f'/api/tasks/{task_id}/result'

        r = await admin_client.post(url, json={'result': 'reports done'})
        assert r.status_code == 400, r.text
        # The transaction ledger is owed whatever the store declared about
        # FBA and ads, so it is always in the first refusal's list.
        ledger = next(p for p in owed if 'MonthlyTransaction' in p)
        assert ledger in r.text

        # Second submit: the check has had its round, so the result ships
        # with the gap as a caveat instead of looping the agent forever.
        r = await admin_client.post(url, json={'result': 'reports done'})
        assert r.status_code == 200, r.text
        data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        assert ledger in (data['review_gaps'] or '')

        hold.set()
        await wait_for_task(admin_client, task_id)

    async def test_a_declared_run_that_delivered_the_set_is_accepted(
        self, admin_client, install_fake_agent
    ):
        task_id, hold = await _running_task(
            admin_client, install_fake_agent, 'Full Store'
        )
        r = await _declare(admin_client, task_id, _reporting_month())
        _deliver(task_id, r.json()['expected_files'])

        url = f'/api/tasks/{task_id}/result'
        r = await admin_client.post(url, json={'result': 'all here'})
        assert r.status_code == 200, r.text
        data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        assert not data['review_gaps']

        hold.set()
        await wait_for_task(admin_client, task_id)

    async def test_one_report_file_with_no_declaration_is_accepted(
        self, admin_client, install_fake_agent
    ):
        """THE BUG, end to end: a lookup is not graded as a run.

        An ad-hoc sales question that filed its CSV under a ``reports_*``
        folder. The old trigger read the folder name and refused this for
        a month's worth of files nobody had asked for.
        """
        task_id, hold = await _running_task(
            admin_client, install_fake_agent, 'Lookup Store'
        )
        _deliver(task_id, ['reports_sales_2026-11/current_sales.csv'])

        r = await admin_client.post(
            f'/api/tasks/{task_id}/result',
            json={'result': 'current sales: 12 units of widget-006'},
        )
        assert r.status_code == 200, r.text
        data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        assert not data['review_gaps']

        hold.set()
        await wait_for_task(admin_client, task_id)

    async def test_one_monthly_report_of_the_set_is_accepted_undeclared(
        self, admin_client, install_fake_agent
    ):
        """The reviewer's case: the user asked for ONE report.

        Last month's transaction report for one marketplace, filed under
        its real monthly folder name, with the rest of the month absent.
        The other files are not owed, because no run was declared — and
        this is the shape that used to be refused.
        """
        task_id, hold = await _running_task(
            admin_client, install_fake_agent, 'One Report Store'
        )
        ledger = await _monthly_ledger(
            admin_client, task_id, _reporting_month()
        )
        _deliver(task_id, [ledger])

        r = await admin_client.post(
            f'/api/tasks/{task_id}/result',
            json={'result': 'here are last month transactions'},
        )
        assert r.status_code == 200, r.text

        hold.set()
        await wait_for_task(admin_client, task_id)


class TestTheToolIsInTheRightToolset:
    """The in-task stdio MCP serves it; the external one must not.

    ``app/mcp_server.py`` is in-task and keyed to one task id. Publishing
    the same surface over ``/api/mcp`` would let an outside agent file
    declarations against somebody else's task.
    """

    def test_the_in_task_toolset_serves_it(self):
        tool = next(
            t for t in TOOLS if t['name'] == 'vibe_seller_declare_report_run'
        )
        assert tool['inputSchema']['required'] == ['month']

    def test_the_external_toolset_does_not(self):
        served = {d['name'] for d in tool_definitions()}
        assert 'vibe_seller_declare_report_run' not in served
