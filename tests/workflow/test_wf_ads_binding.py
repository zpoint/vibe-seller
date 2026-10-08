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

from app import ads_client, ads_skill
from app.models.store import Store
from app.routers import ads as ads_router

pytestmark = pytest.mark.workflow


@pytest.fixture(autouse=True)
def skill_home(tmp_path, monkeypatch):
    """Point the installed skill at a temp dir, for every test here.

    Binding pulls the bundle into the deployment's skills directory and
    unbinding deletes it. Without this, running the suite deleted the
    developer's real ``~/.vibe-seller/.claude/skills/amazon-ads-api``.
    """
    root = tmp_path / 'skills' / ads_skill.SKILL_NAME
    monkeypatch.setattr(ads_skill, 'skill_dir', lambda: root)
    return root


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
            json={'api_key': 'vas_secret'},
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
            json={'api_key': 'vas_secret'},
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
            json={'api_key': 'vas_secret'},
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
            json={'api_key': 'vas_secret'},
        )
        assert response.status_code == 200
        assert response.json()['configured'] is True
        assert response.json()['skill']['updated'] is False

    async def test_an_empty_key_unbinds_without_calling_out(
        self, authenticated_client, bound, skill_home
    ):
        (skill_home / 'SKILL.md').parent.mkdir(parents=True)
        (skill_home / 'SKILL.md').write_text('# skill')

        response = await authenticated_client.put(
            '/api/ads/config', json={'api_key': ''}
        )
        assert response.json() == {'configured': False}
        assert bound == []
        # Unbinding removes the skill — and only the one this test owns.
        assert not skill_home.exists()


class TestTheServiceIsNotConfigurable:
    async def test_a_host_in_the_request_is_ignored(
        self, authenticated_client, monkeypatch
    ):
        """There is one service. A host field is a way to send the store
        list somewhere else, and nothing more."""
        seen: list[str] = []

        class Response:
            status_code = 200
            content = b'{}'
            headers: dict = {}

            @staticmethod
            def json():
                return {'tenant': 'acme', 'active': True}

        class Client:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def request(self, method, url, **kw):
                seen.append(url)
                return Response()

        monkeypatch.setattr(ads_client.httpx, 'AsyncClient', Client)
        await authenticated_client.put(
            '/api/ads/config',
            json={'host': 'https://elsewhere.example', 'api_key': 'vas_x'},
        )
        assert seen
        assert all(url.startswith(ads_client.SERVICE_URL) for url in seen)

    async def test_config_says_only_whether_it_is_bound(
        self, authenticated_client, bound
    ):
        await authenticated_client.put(
            '/api/ads/config', json={'api_key': 'vas_secret'}
        )
        body = (await authenticated_client.get('/api/ads/config')).json()
        assert body == {'configured': True}


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


class TestFileAnswers:
    """A list too large for a reply arrives as a file, saved for the task."""

    TASK = '0b6e1c2a-1111-4222-8333-944455556666'

    @pytest.fixture
    def file_answer(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ads_router, 'VIBE_SELLER_DIR', tmp_path)

        async def fake_call(_session, path, **kw):
            return ads_client.AdsFile(
                filename='search-terms.csv',
                content=b'search_term,cost\nwidget,1.5\n',
                meta={'as_of': '2026-10-07T19:07:00', 'rows': 1},
            )

        monkeypatch.setattr(ads_client, 'call', fake_call)
        return tmp_path

    async def test_saved_into_the_tasks_folder_not_the_reply(
        self, authenticated_client, file_answer
    ):
        response = await authenticated_client.post(
            '/api/ads/call',
            json={'path': '/facts/search-terms', 'task_id': self.TASK},
        )
        result = response.json()['result']
        assert result['rows'] == 1 and result['as_of']
        saved = file_answer / 'tasks' / self.TASK / 'ads-data'
        [path] = list(saved.iterdir())
        assert result['file'] == str(path)
        assert path.read_bytes() == b'search_term,cost\nwidget,1.5\n'
        assert 'widget' not in response.text

    async def test_a_task_id_that_is_not_one_is_refused(
        self, authenticated_client, file_answer
    ):
        response = await authenticated_client.post(
            '/api/ads/call',
            json={'path': '/facts/search-terms', 'task_id': '../../etc'},
        )
        assert response.status_code == 400
        assert not (file_answer / 'etc').exists()
