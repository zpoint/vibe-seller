"""No key to enter: the installation registers itself; stores are revoked
one at a time; an authorization writes Amazon back onto the store.

The service is faked at the HTTP client for registration (that is where
the key comes from) and at :func:`app.ads_client.call` for the rest.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import select

from app import ads_client, ads_skill
from app.models.app_settings import AppSettings
from app.models.store import Store
from app.utils.crypto import decrypt_password, encrypt_password

pytestmark = pytest.mark.workflow


@pytest.fixture(autouse=True)
async def admin(test_user, async_db_session):
    test_user.role = 'admin'
    await async_db_session.commit()
    return test_user


@pytest.fixture(autouse=True)
def skill_home(tmp_path, monkeypatch):
    """Never touch the developer's real installed skill."""
    root = tmp_path / 'skills' / ads_skill.SKILL_NAME
    monkeypatch.setattr(ads_skill, 'skill_dir', lambda: root)
    return root


class FakeService:
    """The service at the HTTP layer: registration, then plain calls."""

    def __init__(self, register_status=200):
        self.register_status = register_status
        self.registrations = 0
        self.registered_as: list[dict | None] = []
        self.requests: list[tuple[str, str, dict]] = []

    def client(self, *a, **kw):
        service = self

        class Response:
            def __init__(self, status, body):
                self.status_code = status
                self._body = body
                self.content = json.dumps(body).encode()
                self.text = self.content.decode()
                self.headers: dict = {}

            def json(self):
                return self._body

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, **kw):
                assert url == f'{ads_client.SERVICE_URL}/installations'
                assert 'headers' not in kw, 'registration carries no key'
                service.registrations += 1
                service.registered_as.append(kw.get('json'))
                await asyncio.sleep(0)
                return Response(
                    service.register_status,
                    {'api_key': f'vas_minted_{service.registrations}'},
                )

            async def request(self, method, url, **kw):
                service.requests.append((method, url, kw.get('headers', {})))
                return Response(200, {'stores': []})

        return Client()


@pytest.fixture
def service(monkeypatch):
    fake = FakeService()
    monkeypatch.setattr(ads_client.httpx, 'AsyncClient', fake.client)
    return fake


async def _stored_key(db) -> str | None:
    row = await db.get(AppSettings, ads_client.KEY_KEY)
    return row.value if row else None


class TestRegistration:
    async def test_nothing_is_sent_until_ads_are_used(
        self, authenticated_client, service
    ):
        body = (await authenticated_client.get('/api/ads/config')).json()
        assert body == {'configured': False}
        assert service.registrations == 0
        assert service.requests == []

    async def test_first_use_registers_and_every_call_carries_the_key(
        self, authenticated_client, async_db_session, service
    ):
        response = await authenticated_client.post('/api/ads/stores/sync')

        assert response.status_code == 200
        assert service.registrations == 1
        assert [h['X-Api-Key'] for _m, _u, h in service.requests] == [
            'vas_minted_1'
        ]
        assert all(
            url.startswith(ads_client.SERVICE_URL)
            for _m, url, _h in service.requests
        )

    async def test_the_key_is_kept_encrypted_and_never_returned(
        self, authenticated_client, async_db_session, service
    ):
        await authenticated_client.post('/api/ads/stores/sync')

        stored = await _stored_key(async_db_session)
        assert stored and 'vas_minted_1' not in stored
        assert decrypt_password(stored) == 'vas_minted_1'
        body = (await authenticated_client.get('/api/ads/config')).json()
        assert body == {'configured': True}

    async def test_an_installation_registers_once(
        self, authenticated_client, service
    ):
        await authenticated_client.post('/api/ads/stores/sync')
        await authenticated_client.post('/api/ads/stores/sync')
        assert service.registrations == 1
        assert {h['X-Api-Key'] for _m, _u, h in service.requests} == {
            'vas_minted_1'
        }

    async def test_racing_first_calls_register_once(
        self, async_db_session, service
    ):
        keys = await asyncio.gather(
            ads_client._credentials(async_db_session),
            ads_client._credentials(async_db_session),
        )
        assert service.registrations == 1
        assert keys[0] == keys[1]

    async def test_a_key_entered_before_this_release_keeps_working(
        self, authenticated_client, async_db_session, service
    ):
        async_db_session.add(
            AppSettings(
                key=ads_client.KEY_KEY, value=encrypt_password('vas_legacy')
            )
        )
        await async_db_session.commit()

        await authenticated_client.post('/api/ads/stores/sync')

        assert service.registrations == 0
        assert service.requests[0][2]['X-Api-Key'] == 'vas_legacy'

    async def test_a_failed_registration_is_reported_and_not_kept(
        self, authenticated_client, async_db_session, monkeypatch
    ):
        fake = FakeService(register_status=503)
        monkeypatch.setattr(ads_client.httpx, 'AsyncClient', fake.client)

        response = await authenticated_client.post('/api/ads/stores/sync')

        assert response.status_code == 400
        assert 'Registering' in response.json()['detail']
        assert not await _stored_key(async_db_session)

    async def test_it_registers_under_the_id_shown_under_the_version(
        self, authenticated_client, service
    ):
        info = (await authenticated_client.get('/api/system/info')).json()
        await authenticated_client.post('/api/ads/stores/sync')

        assert info['install_id']
        assert service.registered_as == [
            {'installation_id': info['install_id']}
        ]

    async def test_the_install_id_is_stable(self, authenticated_client):
        first = (await authenticated_client.get('/api/system/info')).json()
        second = (await authenticated_client.get('/api/system/info')).json()
        assert first['install_id'] == second['install_id']

    async def test_a_member_cannot_register_or_upload_the_stores(
        self, authenticated_client, async_db_session, admin, service
    ):
        admin.role = 'user'
        await async_db_session.commit()

        response = await authenticated_client.post('/api/ads/stores/sync')

        assert response.status_code == 403
        assert service.registrations == 0
        assert service.requests == []
        assert not await _stored_key(async_db_session)

    async def test_there_is_no_key_to_set(self, authenticated_client):
        response = await authenticated_client.put(
            '/api/ads/config', json={'api_key': 'vas_x'}
        )
        assert response.status_code == 405

    async def test_an_agent_cannot_register_or_unbind(
        self, authenticated_client, service, test_task
    ):
        for path in ('/installations', '/me/stores/x/unbind'):
            response = await authenticated_client.post(
                '/api/ads/call',
                json={
                    'path': path,
                    'method': 'POST',
                    'task_id': test_task.id,
                },
            )
            assert response.status_code == 403
        assert service.registrations == 0


def _rows(store_id, *markets, authorized=True):
    return [
        {
            'local_id': store_id,
            'name': 'shop',
            'marketplace': m,
            'store_key': f'sk_{m.lower()}',
            'authorized': authorized,
        }
        for m in markets
    ]


@pytest.fixture
def answers(monkeypatch):
    """Fake ``ads_client.call``: record calls, answer from a dict."""
    calls: list[dict] = []
    table: dict = {}

    async def fake_call(_session, path, *, method='GET', json_body=None, **kw):
        calls.append({'path': path, 'method': method, 'body': json_body})
        for prefix, answer in table.items():
            if path.startswith(prefix):
                if isinstance(answer, Exception):
                    raise answer
                return answer
        return {}

    monkeypatch.setattr(ads_client, 'call', fake_call)
    return calls, table


async def _reload(db, store_id) -> Store:
    store = (
        await db.execute(select(Store).where(Store.id == store_id))
    ).scalar_one()
    await db.refresh(store)
    return store


class TestUnbind:
    @pytest.fixture
    async def authorized(
        self, authenticated_client, async_db_session, test_store, answers
    ):
        _calls, table = answers
        table['/me/stores/'] = {
            'stores': _rows(test_store.id, 'SA', authorized=False)
        }
        table['/me/stores'] = {'stores': _rows(test_store.id, 'SA')}
        await authenticated_client.post('/api/ads/stores/sync')
        assert (await _reload(async_db_session, test_store.id)).ads_authorized
        return test_store

    async def test_revoking_tells_the_service_and_clears_the_store(
        self, authenticated_client, async_db_session, authorized, answers
    ):
        calls, _ = answers
        response = await authenticated_client.post(
            f'/api/ads/stores/{authorized.id}/unbind', json={'purge': False}
        )

        assert response.status_code == 200
        sent = calls[-1]
        assert sent['path'] == f'/me/stores/{authorized.id}/unbind'
        assert sent['method'] == 'POST'
        assert sent['body'] == {'purge': False}
        store = await _reload(async_db_session, authorized.id)
        assert store.ads_authorized is False
        assert json.loads(store.ads_store_keys) == {}

    async def test_purge_is_passed_through(
        self, authenticated_client, authorized, answers
    ):
        calls, _ = answers
        await authenticated_client.post(
            f'/api/ads/stores/{authorized.id}/unbind', json={'purge': True}
        )
        assert calls[-1]['body'] == {'purge': True}

    async def test_revoking_keeps_where_the_store_sells(
        self, authenticated_client, async_db_session, authorized
    ):
        """An authorization ending says nothing about where it sells."""
        before = await _reload(async_db_session, authorized.id)
        platforms, countries = before.platforms, before.countries

        await authenticated_client.post(
            f'/api/ads/stores/{authorized.id}/unbind', json={}
        )

        after = await _reload(async_db_session, authorized.id)
        assert (after.platforms, after.countries) == (platforms, countries)

    async def test_no_store_left_authorized_keeps_the_skill_away(
        self, authenticated_client, async_db_session, authorized
    ):
        """What boot and the skills sync check before reinstalling it."""
        assert await ads_skill._any_store_authorized(async_db_session)
        await authenticated_client.post(
            f'/api/ads/stores/{authorized.id}/unbind', json={}
        )
        assert not await ads_skill._any_store_authorized(async_db_session)

    async def test_the_skill_goes_with_the_last_authorized_store(
        self, authenticated_client, authorized, skill_home
    ):
        skill_home.mkdir(parents=True, exist_ok=True)
        (skill_home / 'SKILL.md').write_text('# skill')

        await authenticated_client.post(
            f'/api/ads/stores/{authorized.id}/unbind', json={}
        )

        assert not skill_home.exists()

    async def test_a_member_cannot_revoke(
        self, authenticated_client, async_db_session, admin, test_store, answers
    ):
        admin.role = 'user'
        await async_db_session.commit()
        calls, _ = answers

        response = await authenticated_client.post(
            f'/api/ads/stores/{test_store.id}/unbind', json={}
        )

        assert response.status_code == 403
        assert calls == []

    async def test_an_unknown_store_is_404(self, authenticated_client, answers):
        calls, _ = answers
        response = await authenticated_client.post(
            '/api/ads/stores/nope/unbind', json={}
        )
        assert response.status_code == 404
        assert calls == []

    async def test_a_service_error_is_reported(
        self, authenticated_client, test_store, answers
    ):
        _, table = answers
        table['/me/stores/'] = ads_client.AdsServiceError('service is down')
        response = await authenticated_client.post(
            f'/api/ads/stores/{test_store.id}/unbind', json={}
        )
        assert response.status_code == 400
        assert 'service is down' in response.json()['detail']


class TestAuthorizationIsWrittenBack:
    """Amazon's profiles are its own word on where a store is."""

    async def test_marketplaces_land_on_platforms_and_countries(
        self, authenticated_client, async_db_session, test_store, answers
    ):
        _, table = answers
        table['/me/stores'] = {'stores': _rows(test_store.id, 'SA', 'AE')}

        await authenticated_client.post('/api/ads/stores/sync')

        store = await _reload(async_db_session, test_store.id)
        assert json.loads(store.platforms) == ['amazon']
        assert json.loads(store.countries) == ['US', 'AE', 'SA']
        assert json.loads(store.platform_countries) == {'amazon': ['AE', 'SA']}

    async def test_an_unauthorized_store_is_left_alone(
        self, authenticated_client, async_db_session, test_store, answers
    ):
        _, table = answers
        table['/me/stores'] = {
            'stores': _rows(test_store.id, 'SA', authorized=False)
        }

        await authenticated_client.post('/api/ads/stores/sync')

        store = await _reload(async_db_session, test_store.id)
        assert json.loads(store.countries) == ['US']
        assert json.loads(store.platform_countries or '{}') == {}

    async def test_syncing_again_adds_nothing_twice(
        self, authenticated_client, async_db_session, test_store, answers
    ):
        _, table = answers
        table['/me/stores'] = {'stores': _rows(test_store.id, 'SA')}

        await authenticated_client.post('/api/ads/stores/sync')
        await authenticated_client.post('/api/ads/stores/sync')

        store = await _reload(async_db_session, test_store.id)
        assert json.loads(store.countries) == ['US', 'SA']
        assert json.loads(store.platform_countries) == {'amazon': ['SA']}
