"""A weekly/monthly schedule always has its day — on every way in.

Observed: a weekly schedule created over the API (or by an agent over
MCP) without ``schedule_day`` was accepted, fired EVERY DAY, and showed
"Weekly Monday 09:00". The web form always sends the day; the API and
MCP did not have to. Both share the schedule routes, so the routes
refuse it, an edit that would leave a weekly/monthly schedule without
its day is refused, and rows written before this get the day the UI has
always shown for them (1) at boot.
"""

import pytest
from sqlalchemy import select

from app.database import backfill_schedule_day
from app.models.schedule import Schedule
from tests.workflow.test_wf_agent_mcp_schedules import call_tool

pytestmark = pytest.mark.workflow


def _body(**overrides) -> dict:
    return {
        'title': 'Store notice check',
        'description': 'Check the store notices',
        'schedule_type': 'weekly',
        'schedule_time': '09:00',
        **overrides,
    }


async def _titles(client) -> set[str]:
    return {s['title'] for s in (await client.get('/api/schedules')).json()}


class TestCreate:
    async def test_weekly_without_its_day_is_refused(self, admin_client):
        r = await admin_client.post(
            '/api/schedules', json=_body(title='No weekday')
        )
        assert r.status_code == 400, r.text
        assert 'needs schedule_day' in r.json()['detail']
        assert '1..7' in r.json()['detail']
        assert 'No weekday' not in await _titles(admin_client)

    async def test_monthly_without_its_day_is_refused(self, admin_client):
        r = await admin_client.post(
            '/api/schedules',
            json=_body(title='No date', schedule_type='monthly'),
        )
        assert r.status_code == 400, r.text
        assert '1..31' in r.json()['detail']

    @pytest.mark.parametrize(('kind', 'day'), [('weekly', 8), ('monthly', 32)])
    async def test_a_day_out_of_range_is_refused(self, admin_client, kind, day):
        r = await admin_client.post(
            '/api/schedules',
            json=_body(schedule_type=kind, schedule_day=day),
        )
        assert r.status_code == 400, r.text

    async def test_weekly_with_its_day_is_created(self, admin_client):
        r = await admin_client.post(
            '/api/schedules', json=_body(schedule_day=3)
        )
        assert r.status_code == 201, r.text
        assert r.json()['schedule_day'] == 3

    async def test_an_agent_over_mcp_is_told_the_day_is_needed(
        self, admin_client
    ):
        result, is_error = await call_tool(
            admin_client,
            'create_schedule',
            {
                'title': 'Agent weekly check',
                'description': 'Check the store notices',
                'schedule_type': 'weekly',
                'schedule_time': '09:00',
            },
        )
        assert is_error, result
        assert 'needs schedule_day' in result['error']
        assert 'Agent weekly check' not in await _titles(admin_client)


class TestEdit:
    async def test_switching_to_weekly_needs_the_day(self, admin_client):
        r = await admin_client.post(
            '/api/schedules', json=_body(schedule_type='days')
        )
        assert r.status_code == 201, r.text
        sid = r.json()['id']

        r = await admin_client.put(
            f'/api/schedules/{sid}', json={'schedule_type': 'weekly'}
        )
        assert r.status_code == 400, r.text
        assert 'needs schedule_day' in r.json()['detail']
        kept = (await admin_client.get(f'/api/schedules/{sid}')).json()
        assert kept['schedule_type'] == 'days'

        r = await admin_client.put(
            f'/api/schedules/{sid}',
            json={'schedule_type': 'weekly', 'schedule_day': 5},
        )
        assert r.status_code == 200, r.text
        assert r.json()['schedule_day'] == 5

    async def test_clearing_a_weekly_schedules_day_is_refused(
        self, admin_client
    ):
        r = await admin_client.post(
            '/api/schedules', json=_body(schedule_day=2)
        )
        sid = r.json()['id']

        r = await admin_client.put(
            f'/api/schedules/{sid}', json={'schedule_day': None}
        )
        assert r.status_code == 400, r.text
        kept = (await admin_client.get(f'/api/schedules/{sid}')).json()
        assert kept['schedule_day'] == 2

        # An edit that leaves the day alone is fine.
        r = await admin_client.put(
            f'/api/schedules/{sid}', json={'schedule_time': '10:30'}
        )
        assert r.status_code == 200, r.text


class TestRowsWrittenBefore:
    async def test_boot_gives_them_the_day_the_ui_showed(
        self, test_engine, override_async_session
    ):
        maker = override_async_session
        async with maker() as db:
            for sid, kind in (
                ('legacy-weekly', 'weekly'),
                ('legacy-monthly', 'monthly'),
                ('legacy-daily', 'days'),
            ):
                db.add(
                    Schedule(
                        id=sid,
                        title=sid,
                        schedule_type=kind,
                        schedule_time='09:00',
                        schedule_day=None,
                        created_by='u',
                    )
                )
            await db.commit()

        async with test_engine.begin() as conn:
            await conn.run_sync(backfill_schedule_day)

        async with maker() as db:
            days = dict(
                (
                    await db.execute(select(Schedule.id, Schedule.schedule_day))
                ).all()
            )
        assert days == {
            'legacy-weekly': 1,
            'legacy-monthly': 1,
            'legacy-daily': None,
        }
