"""Binding to an external ads service, and the agent's one door to it.

The ads service is faked at :mod:`app.ads_client` because what these
tests are about is what this side does: whether the key stays out of the
agent's reach, whether a store's marketplaces come back onto the store
row, and whether the pass-through refuses the paths that belong to a
person rather than an agent.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app import ads_client
from app.models.store import Store

pytestmark = pytest.mark.workflow


@pytest.fixture
def bound(monkeypatch):
    """A configured service, with every outbound call recorded."""
    calls: list[dict] = []

    async def fake_credentials(_session):
        return 'https://ads.example', 'vas_secret'

    async def fake_call(
        _session, path, *, method='GET', params=None, json_body=None, **kw
    ):
        calls.append({
            'path': path,
            'method': method,
            'params': params,
            'body': json_body,
        })
        if path == '/me':
            return {'tenant': 'acme', 'active': True}
        if path == '/me/stores':
            return {'stores': []}
        if path == '/auth-url':
            return {
                'url': 'https://amazon.example/ap/oa?...',
                'expires_in': 1800,
            }
        return {'rows': []}

    monkeypatch.setattr(ads_client, '_credentials', fake_credentials)
    monkeypatch.setattr(ads_client, 'call', fake_call)
    return calls


class TestConfig:
    async def test_config_never_returns_the_key(
        self, authenticated_client, bound
    ):
        await authenticated_client.put(
            '/api/ads/config',
            json={'host': 'https://ads.example', 'api_key': 'vas_secret'},
        )
        response = await authenticated_client.get('/api/ads/config')
        body = response.json()

        assert body['configured'] is True
        assert 'vas_secret' not in json.dumps(body)
        assert 'api_key' not in body

    async def test_binding_is_verified_immediately(
        self, authenticated_client, bound
    ):
        """A binding that only fails inside a task costs a run to find."""
        response = await authenticated_client.put(
            '/api/ads/config',
            json={'host': 'https://ads.example', 'api_key': 'vas_secret'},
        )
        assert response.status_code == 200
        assert any(call['path'] == '/me' for call in bound)

    async def test_a_verified_binding_pulls_the_skill_itself(
        self, authenticated_client, bound
    ):
        """There is nothing for a person to decide here.

        A bound deployment always wants the current bundle, and a button
        they have to find is one they will forget — leaving agents on
        instructions older than the service they are calling.
        """
        await authenticated_client.put(
            '/api/ads/config',
            json={'host': 'https://ads.example', 'api_key': 'vas_secret'},
        )
        assert any(call['path'] == '/skill/version' for call in bound)

    async def test_a_failed_skill_pull_does_not_undo_the_binding(
        self, authenticated_client, monkeypatch
    ):
        """The binding worked; the bundle can arrive later."""

        async def fake_call(_session, path, **kw):
            if path == '/me':
                return {'tenant': 'acme', 'active': True}
            raise ads_client.AdsServiceError('bundle endpoint is down')

        monkeypatch.setattr(ads_client, 'call', fake_call)

        response = await authenticated_client.put(
            '/api/ads/config',
            json={'host': 'https://ads.example', 'api_key': 'vas_secret'},
        )
        assert response.status_code == 200
        assert response.json()['configured'] is True
        assert response.json()['skill']['updated'] is False

    async def test_an_empty_host_unbinds_without_calling_out(
        self, authenticated_client, bound
    ):
        response = await authenticated_client.put(
            '/api/ads/config', json={'host': ''}
        )
        assert response.json() == {'configured': False}
        assert bound == []


class TestAuthUrl:
    async def test_a_ziniao_store_is_told_where_to_open_the_link(
        self, authenticated_client, async_db_session, test_store, bound
    ):
        """The consent screen must see the seller's own Amazon session.

        On an anti-detect backend that session lives inside that
        browser's store window. Opening the link anywhere else authorizes
        whichever account the operator happens to be signed into — a
        success that binds the wrong advertiser.
        """
        test_store.browser_backend = 'ziniao'
        await async_db_session.commit()

        response = await authenticated_client.get(
            f'/api/ads/stores/{test_store.id}/auth-url'
        )
        body = response.json()
        assert body['open_in'] == 'ziniao'
        assert 'url' in body

    async def test_a_chrome_store_opens_the_link_directly(
        self, authenticated_client, async_db_session, test_store, bound
    ):
        test_store.browser_backend = 'chrome'
        await async_db_session.commit()

        response = await authenticated_client.get(
            f'/api/ads/stores/{test_store.id}/auth-url'
        )
        assert response.json()['open_in'] == 'browser'

    async def test_unknown_store_is_404(self, authenticated_client, bound):
        response = await authenticated_client.get(
            '/api/ads/stores/no-such-store/auth-url'
        )
        assert response.status_code == 404


class TestStoreSync:
    async def test_authorized_marketplaces_land_on_the_store_row(
        self, authenticated_client, async_db_session, test_store, monkeypatch
    ):
        """One store, two marketplaces — the normal case, not an edge."""

        async def fake_call(
            _session, path, *, method='GET', params=None, json_body=None, **kw
        ):
            assert path == '/me/stores'
            # The upload says nothing about marketplaces; the service
            # reads them off the profiles the advertiser granted.
            sent = json_body['stores'][0]
            assert set(sent) == {'local_id', 'name'}
            return {
                'stores': [
                    {
                        'local_id': test_store.id,
                        'marketplace': 'SA',
                        'store_key': 'sk_sa',
                        'authorized': True,
                    },
                    {
                        'local_id': test_store.id,
                        'marketplace': 'AE',
                        'store_key': 'sk_ae',
                        'authorized': True,
                    },
                ]
            }

        monkeypatch.setattr(ads_client, 'call', fake_call)

        response = await authenticated_client.post('/api/ads/stores/sync')
        assert response.status_code == 200

        store = (
            await async_db_session.execute(
                select(Store).where(Store.id == test_store.id)
            )
        ).scalar_one()
        await async_db_session.refresh(store)
        assert json.loads(store.ads_store_keys) == {
            'AE': 'sk_ae',
            'SA': 'sk_sa',
        }
        assert store.ads_authorized is True


class TestAgentCall:
    async def test_binding_paths_are_refused(self, authenticated_client, bound):
        """Binding is a person's decision, not one an agent reaches."""
        for path in ('/me/stores', '/auth-url', '/assignments'):
            response = await authenticated_client.post(
                '/api/ads/call', json={'path': path}
            )
            assert response.status_code == 403, path

    async def test_the_store_key_is_injected_not_supplied(
        self, authenticated_client, async_db_session, test_store, monkeypatch
    ):
        """The agent names a store; it never learns the service's ids."""
        test_store.ads_store_keys = json.dumps({'SA': 'sk_sa'})
        test_store.ads_authorized = True
        await async_db_session.commit()

        seen: dict = {}

        async def fake_call(
            _session, path, *, method='GET', params=None, json_body=None, **kw
        ):
            seen.update({'path': path, 'params': params})
            return {'rows': []}

        monkeypatch.setattr(ads_client, 'call', fake_call)

        response = await authenticated_client.post(
            '/api/ads/call',
            json={'path': '/facts/rollup', 'store': test_store.name},
        )
        assert response.status_code == 200
        assert seen['params']['store_key'] == 'sk_sa'

    async def test_an_unauthorized_store_says_where_to_fix_it(
        self, authenticated_client, test_store, bound
    ):
        response = await authenticated_client.post(
            '/api/ads/call',
            json={'path': '/facts/rollup', 'store': test_store.name},
        )
        assert response.status_code == 400
        assert 'Settings' in response.json()['detail']

    async def test_two_marketplaces_without_a_name_is_refused(
        self, authenticated_client, async_db_session, test_store, bound
    ):
        test_store.ads_store_keys = json.dumps({'SA': 'sk_sa', 'AE': 'sk_ae'})
        test_store.ads_authorized = True
        await async_db_session.commit()

        response = await authenticated_client.post(
            '/api/ads/call',
            json={'path': '/facts/rollup', 'store': test_store.name},
        )
        assert response.status_code == 400
        detail = response.json()['detail']
        assert 'SA' in detail and 'AE' in detail
