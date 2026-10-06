"""``fanout_serial`` through the schedules API.

Set through the API — never by writing the row — because the API is
what re-registers the APScheduler job. A schedule edited in SQLite
directly kept firing on its old in-memory trigger for a month.
"""

import pytest

pytestmark = pytest.mark.workflow


async def _fanout(admin_client, **extra):
    r = await admin_client.post(
        '/api/schedules',
        json={
            'title': 'Monthly download',
            'schedule_type': 'monthly',
            'schedule_day': 3,
            'schedule_time': '02:10',
            'store_id': None,
            'phase_mode': 'fanout',
            **extra,
        },
    )
    assert r.status_code in (200, 201), r.text
    return r.json()


class TestFanoutSerialApi:
    async def test_defaults_off(self, admin_client):
        assert (await _fanout(admin_client))['fanout_serial'] is False

    async def test_create_on(self, admin_client):
        body = await _fanout(admin_client, fanout_serial=True)
        assert body['fanout_serial'] is True

    async def test_update_turns_it_on_and_leaving_it_out_keeps_it(
        self, admin_client
    ):
        sched = await _fanout(admin_client)
        r = await admin_client.put(
            f'/api/schedules/{sched["id"]}', json={'fanout_serial': True}
        )
        assert r.status_code == 200, r.text
        assert r.json()['fanout_serial'] is True

        r = await admin_client.put(
            f'/api/schedules/{sched["id"]}', json={'schedule_time': '02:40'}
        )
        assert r.status_code == 200, r.text
        assert r.json()['fanout_serial'] is True

    async def test_store_bound_schedule_rejects_it(self, admin_client):
        store = await admin_client.post('/api/stores', json={'name': 'Solo'})
        r = await admin_client.post(
            '/api/schedules',
            json={
                'title': 'One store',
                'schedule_type': 'days',
                'store_id': store.json()['id'],
                'fanout_serial': True,
            },
        )
        assert r.status_code == 400

    async def test_single_phase_schedule_rejects_it(self, admin_client):
        r = await admin_client.post(
            '/api/schedules',
            json={
                'title': 'Mailbox',
                'schedule_type': 'days',
                'store_id': None,
                'phase_mode': 'single',
                'fanout_serial': True,
            },
        )
        assert r.status_code == 400
