"""Only a missing route counts as a missing path.

The service answers 404 in two situations that mean opposite things to
the agent: a path it has no route for (the agent guessed) and a route
that has nothing to say (the store or the campaign is not there). The
body tells them apart, and only the first may be counted against the
task.
"""

from __future__ import annotations

import json

import pytest

from app import ads_client, ads_path_guard

pytestmark = pytest.mark.unit


class _Response:
    """What the service sent back, at the HTTP layer."""

    def __init__(self, status: int, body: str):
        self.status_code = status
        self.content = body.encode()
        self.text = body
        self.headers: dict = {}

    def json(self):
        return json.loads(self.content)


async def _failed_call(monkeypatch, status: int, body: str):
    """One call against a service that answers this, and only this."""

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, method, url, **kwargs):
            return _Response(status, body)

    async def fake_credentials(_session):
        return 'https://ads.example', 'vas_secret'

    monkeypatch.setattr(ads_client, '_credentials', fake_credentials)
    monkeypatch.setattr(
        ads_client.httpx, 'AsyncClient', lambda *a, **kw: Client()
    )
    with pytest.raises(ads_client.AdsServiceError) as exc:
        await ads_client.call(None, '/facts/rollup')
    return exc.value


def _entity_miss(path: str) -> ads_client.AdsServiceError:
    """The service answered 404 about something the path points at."""
    return ads_client.AdsServiceError(
        f'GET {path} failed (404): No store', status=404
    )


def _route_miss(path: str) -> ads_client.AdsServiceError:
    """The service has no such path."""
    return ads_client.AdsServiceError(
        f'GET {path} failed (404): Not Found',
        status=404,
        unknown_route=True,
    )


class TestTheBodySaysWhich404IsAMissingPath:
    async def test_an_unrouted_path_is_one(self, monkeypatch):
        error = await _failed_call(
            monkeypatch, 404, json.dumps({'detail': 'Not Found'})
        )
        assert error.unknown_route is True

    async def test_a_missing_entity_is_not_one(self, monkeypatch):
        error = await _failed_call(
            monkeypatch, 404, json.dumps({'detail': "No store 'sk_x'."})
        )
        assert error.unknown_route is False

    async def test_a_body_that_is_not_json_is_not_one(self, monkeypatch):
        error = await _failed_call(monkeypatch, 404, '<html>not found</html>')
        assert error.unknown_route is False

    async def test_another_status_is_not_one(self, monkeypatch):
        """The body alone does not decide; the status has to be a 404."""
        error = await _failed_call(
            monkeypatch, 400, json.dumps({'detail': 'Not Found'})
        )
        assert error.unknown_route is False

    def test_the_other_raisers_keep_their_meaning(self):
        """Registration and store-key errors are not missing routes."""
        error = ads_client.AdsServiceError('service is down')
        assert error.unknown_route is False
        assert error.status is None


class TestTheGuardCountsOnlyMissingRoutes:
    """Three entities the service does not have are not three guesses."""

    def test_entity_misses_do_not_count(self):
        for n in range(ads_path_guard.MAX_UNKNOWN):
            path = f'/facts/{n}'
            ads_path_guard.record(
                'task-entity',
                path,
                ads_path_guard.outcome_for(_entity_miss(path)),
            )
        assert ads_path_guard.refusal('task-entity', '/facts/9') is None

    def test_route_misses_count(self):
        for n in range(ads_path_guard.MAX_UNKNOWN):
            path = f'/guess/{n}'
            ads_path_guard.record(
                'task-route',
                path,
                ads_path_guard.outcome_for(_route_miss(path)),
            )
        assert ads_path_guard.refusal('task-route', '/guess/9')

    def test_a_working_route_stays_open_after_the_cap(self):
        """The cap must not close the routes that work."""
        for n in range(ads_path_guard.MAX_UNKNOWN):
            path = f'/guess/{n}'
            ads_path_guard.record(
                'task-mixed',
                path,
                ads_path_guard.outcome_for(_route_miss(path)),
            )
        working = '/facts/rollup'
        ads_path_guard.record(
            'task-mixed',
            working,
            ads_path_guard.outcome_for(_entity_miss(working)),
        )
        assert ads_path_guard.refusal('task-mixed', working) is None

    def test_a_bare_status_cannot_be_recorded(self):
        """The old heuristic must not be reachable by accident."""
        with pytest.raises(ValueError):
            ads_path_guard.record('task-raw', '/facts/0', 404)
