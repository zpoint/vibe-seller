"""A store's platform is only what is known: unknown is empty."""

import json

import pytest
from sqlalchemy import select

from app.models.store import Store
from app.scheduler.heartbeat import send_install_heartbeat
from app.store_platforms import migrate_defaulted_platforms

pytestmark = pytest.mark.workflow


async def _add(maker, sid, platforms, countries, pc):
    async with maker() as db:
        db.add(
            Store(
                id=sid,
                name=sid,
                browser_backend='chrome',
                platforms=platforms,
                countries=countries,
                platform_countries=pc,
            )
        )
        await db.commit()


class TestCreate:
    async def test_no_platform_given_is_empty(self, admin_client):
        r = await admin_client.post('/api/stores', json={'name': 'Plain'})
        assert r.status_code == 200
        s = r.json()
        assert (s['platforms'], s['countries'], s['platform_countries']) == (
            [],
            [],
            {},
        )

    async def test_given_platform_is_kept(self, admin_client):
        r = await admin_client.post(
            '/api/stores',
            json={'name': 'Shop', 'platforms': ['taobao'], 'countries': ['CN']},
        )
        s = r.json()
        assert s['platforms'] == ['taobao']
        assert s['platform_countries'] == {'taobao': ['CN']}

    async def test_update_platforms_sets_them(self, admin_client):
        sid = (
            await admin_client.post('/api/stores', json={'name': 'Upd'})
        ).json()['id']
        r = await admin_client.put(
            f'/api/stores/{sid}', json={'platforms': ['noon']}
        )
        assert r.json()['platforms'] == ['noon']


class TestReaders:
    async def test_response_does_not_read_the_legacy_list(
        self, admin_client, override_async_session
    ):
        await _add(
            override_async_session, 'legacy', '["amazon"]', '["US"]', '{}'
        )
        r = await admin_client.get('/api/stores/legacy')
        assert r.json()['platforms'] == []
        assert r.json()['countries'] == []

    async def test_heartbeat_counts_platform_countries(
        self, override_async_session, monkeypatch
    ):
        events = []
        monkeypatch.setattr(
            'app.telemetry.send',
            lambda e, p=None: events.append((e, dict(p or {}))),
        )
        monkeypatch.setattr(
            'app.telemetry.set_person_properties', lambda p=None: None
        )
        await _add(
            override_async_session,
            'a',
            '["amazon"]',
            '["US"]',
            '{"noon": ["SA"]}',
        )
        await _add(override_async_session, 'b', '["amazon"]', '["US"]', '{}')
        await send_install_heartbeat()
        (props,) = [p for e, p in events if e == 'install_heartbeat']
        assert props['store_platform_counts'] == {'noon': 1}


class TestMigration:
    async def test_defaulted_rows_are_cleared_and_others_kept(
        self, test_engine, override_async_session
    ):
        maker = override_async_session
        await _add(maker, 'defaulted', '["amazon"]', '["US"]', '{}')
        await _add(maker, 'declared', '["taobao"]', '["CN"]', '{}')
        await _add(maker, 'known', '["amazon"]', '["US"]', '{"amazon": ["AE"]}')

        for _ in range(2):
            async with test_engine.begin() as conn:
                await conn.run_sync(migrate_defaulted_platforms)
            async with maker() as db:
                rows = {
                    s.id: (
                        json.loads(s.platforms),
                        json.loads(s.countries),
                        json.loads(s.platform_countries),
                    )
                    for s in (await db.execute(select(Store))).scalars()
                }
            assert rows == {
                'defaulted': ([], [], {}),
                'declared': (['taobao'], ['CN'], {'taobao': ['CN']}),
                'known': (['amazon'], ['AE'], {'amazon': ['AE']}),
            }
