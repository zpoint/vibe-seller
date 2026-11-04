"""A missing route is not a missing entity.

The service answers 404 for both, and the agent is told "no such path" —
and only the first is counted against the task. Counting the second is
what closed a route that works and pushed the agent off it.
"""

from __future__ import annotations

import json

import pytest

from app import ads_client, ads_path_guard

pytestmark = pytest.mark.workflow


class _Service:
    """The service at the HTTP layer, answering with one canned body."""

    def __init__(self):
        self.status = 404
        self.body = json.dumps({'detail': 'Not Found'})
        self.requests: list[str] = []

    def answer(self, status: int, body) -> None:
        self.status = status
        self.body = body if isinstance(body, str) else json.dumps(body)

    def client(self, *args, **kwargs):
        service = self

        class Response:
            def __init__(self):
                self.status_code = service.status
                self.content = service.body.encode()
                self.text = service.body
                self.headers: dict = {}

            def json(self):
                return json.loads(self.content)

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def request(self, method, url, **kwargs):
                service.requests.append(url)
                return Response()

        return Client()


@pytest.fixture
def service(monkeypatch):
    """A service that answers 404, with the body each test chooses."""

    async def fake_credentials(_session):
        return 'https://ads.example', 'vas_secret'

    fake = _Service()
    monkeypatch.setattr(ads_client, '_credentials', fake_credentials)
    monkeypatch.setattr(ads_client.httpx, 'AsyncClient', fake.client)
    return fake


@pytest.fixture
async def authorized_store(async_db_session, test_store):
    """A store the service knows, so the call gets that far."""
    test_store.ads_store_keys = json.dumps({'SA': 'sk_sa'})
    test_store.ads_authorized = True
    await async_db_session.commit()
    return test_store


async def _call(client, task_id, path):
    return await client.post(
        '/api/ads/call', json={'path': path, 'task_id': task_id}
    )


class TestWhatTheAgentIsTold:
    async def test_the_body_tells_a_missing_route_from_a_missing_entity(
        self, authenticated_client, test_task, service, authorized_store
    ):
        """Same status, same call — the body decides what the agent hears.

        A route the service does not have is the agent's own guess, and
        it is told so. A route that has nothing to say — the store is
        not there — is the service's own answer, and it passes through
        as it is.
        """
        service.answer(404, {'detail': 'Not Found'})
        route = await _call(authenticated_client, test_task.id, '/facts/rollup')
        assert route.status_code == 400
        assert 'do not guess' in route.json()['detail']

        service.answer(404, {'detail': "No store 'sk_x'."})
        entity = await _call(
            authenticated_client, test_task.id, '/facts/rollup'
        )
        assert entity.status_code == 400
        detail = entity.json()['detail']
        assert "No store 'sk_x'." in detail
        assert 'do not guess' not in detail


class TestWhatCountsAgainstTheTask:
    async def test_only_missing_routes_count_against_the_task(
        self, authenticated_client, test_task, service, authorized_store
    ):
        """Three entities the service does not have are not three guesses.

        They leave the service open, and the routes that work stay open
        with it. Three missing routes do close it — that is the case the
        guard was written for.
        """
        service.answer(404, {'detail': "No store 'sk_x'."})
        for n in range(ads_path_guard.MAX_UNKNOWN):
            response = await _call(
                authenticated_client, test_task.id, f'/facts/{n}'
            )
            assert 'Blocked' not in response.json()['detail']

        fourth = await _call(
            authenticated_client, test_task.id, '/facts/rollup'
        )
        assert fourth.status_code == 400
        assert 'Blocked' not in fourth.json()['detail']
        assert len(service.requests) == ads_path_guard.MAX_UNKNOWN + 1

        service.answer(404, {'detail': 'Not Found'})
        for n in range(ads_path_guard.MAX_UNKNOWN):
            response = await _call(
                authenticated_client, test_task.id, f'/guess/{n}'
            )
            assert 'do not guess' in response.json()['detail']

        blocked = await _call(authenticated_client, test_task.id, '/guess/more')
        assert blocked.status_code == 400
        assert 'Blocked' in blocked.json()['detail']
        assert len(service.requests) == 2 * ads_path_guard.MAX_UNKNOWN + 1
