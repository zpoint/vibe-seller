"""Workflow tests for schedule timezone resolution end-to-end.

Covers the three-way fallback chain exposed by the /api/schedules
endpoint: explicit body.timezone → AppSettings['default_schedule_timezone']
→ server local timezone. Also covers GET /api/settings and PUT validation
for the new AppSettings key.
"""

from unittest import mock

import pytest

from app.models.app_settings import AppSettings

pytestmark = pytest.mark.workflow


class TestCreateScheduleTimezone:
    async def test_explicit_timezone_wins(
        self, admin_client, override_async_session
    ):
        resp = await admin_client.post(
            '/api/schedules',
            json={
                'title': 'T',
                'schedule_type': 'days',
                'schedule_time': '09:00',
                'timezone': 'Europe/London',
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()['timezone'] == 'Europe/London'

    async def test_appsettings_default_used_when_body_tz_omitted(
        self, admin_client, override_async_session
    ):
        async with override_async_session() as db:
            db.add(
                AppSettings(
                    key='default_schedule_timezone',
                    value='America/Los_Angeles',
                )
            )
            await db.commit()

        resp = await admin_client.post(
            '/api/schedules',
            json={
                'title': 'T',
                'schedule_type': 'days',
                'schedule_time': '09:00',
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()['timezone'] == 'America/Los_Angeles'

    async def test_server_local_fallback_when_no_setting(self, admin_client):
        with mock.patch(
            'app.routers.schedules.get_server_timezone',
            return_value='Asia/Tokyo',
        ):
            resp = await admin_client.post(
                '/api/schedules',
                json={
                    'title': 'T',
                    'schedule_type': 'days',
                    'schedule_time': '09:00',
                },
            )
        assert resp.status_code == 201, resp.text
        assert resp.json()['timezone'] == 'Asia/Tokyo'

    async def test_invalid_timezone_rejected(self, admin_client):
        resp = await admin_client.post(
            '/api/schedules',
            json={
                'title': 'T',
                'schedule_type': 'days',
                'schedule_time': '09:00',
                'timezone': 'Not/A/Zone',
            },
        )
        assert resp.status_code == 400
        assert 'Invalid timezone' in resp.json()['detail']


class TestUpdateScheduleTimezone:
    async def test_put_persists_new_timezone(self, admin_client):
        create = await admin_client.post(
            '/api/schedules',
            json={
                'title': 'T',
                'schedule_type': 'days',
                'schedule_time': '09:00',
                'timezone': 'UTC',
            },
        )
        sid = create.json()['id']

        update = await admin_client.put(
            f'/api/schedules/{sid}',
            json={'timezone': 'Europe/Berlin'},
        )
        assert update.status_code == 200, update.text
        assert update.json()['timezone'] == 'Europe/Berlin'

    async def test_put_rejects_invalid_timezone(self, admin_client):
        create = await admin_client.post(
            '/api/schedules',
            json={
                'title': 'T',
                'schedule_type': 'days',
                'schedule_time': '09:00',
                'timezone': 'UTC',
            },
        )
        sid = create.json()['id']
        resp = await admin_client.put(
            f'/api/schedules/{sid}',
            json={'timezone': 'Not/Real'},
        )
        assert resp.status_code == 400


class TestAppSettingsTimezoneKey:
    async def test_get_returns_server_zone_when_unset(self, admin_client):
        with mock.patch(
            'app.routers.app_settings.get_server_timezone',
            return_value='Asia/Kolkata',
        ):
            resp = await admin_client.get('/api/settings')
        assert resp.status_code == 200
        assert resp.json()['default_schedule_timezone'] == 'Asia/Kolkata'

    async def test_put_persists_valid_timezone(self, admin_client):
        resp = await admin_client.put(
            '/api/settings',
            json={'default_schedule_timezone': 'Europe/Paris'},
        )
        assert resp.status_code == 200

        get_resp = await admin_client.get('/api/settings')
        assert get_resp.json()['default_schedule_timezone'] == 'Europe/Paris'

    async def test_put_rejects_invalid_timezone(self, admin_client):
        resp = await admin_client.put(
            '/api/settings',
            json={'default_schedule_timezone': 'Not/A/Zone'},
        )
        assert resp.status_code == 400
