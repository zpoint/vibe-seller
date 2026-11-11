"""Only a missing route counts as a missing path.

The service answers 404 in two situations that mean opposite things to
the agent: a path it has no route for (the agent guessed) and a route
that has nothing to say (the store or the campaign is not there). The
body tells them apart, and only the first may be counted against the
task.
"""

from __future__ import annotations

import json
import time

import pytest

from app import ads_client, ads_path_guard, ads_skill

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


DOC = frozenset({'/facts/rollup', '/data/{dataset}', '/live/*'})


def _guessed_out(task: str) -> None:
    """Three invented paths the service said it has no route for."""
    for n in range(ads_path_guard.MAX_UNKNOWN):
        path = f'/guess/{n}'
        ads_path_guard.record(
            task, path, ads_path_guard.outcome_for(_route_miss(path))
        )


class TestDocumentedPathsStayOpen:
    """The budget limits GUESSING. A path the skill names is never a
    guess, and spending the cap must not close it — that made every
    later step of a documented procedure a refusal."""

    def test_documented_untried_path_passes_after_the_cap(self):
        _guessed_out('task-doc')
        assert ads_path_guard.refusal('task-doc', '/facts/rollup', DOC) is None

    def test_placeholder_and_wildcard_open_their_family(self):
        _guessed_out('task-wild')
        assert ads_path_guard.refusal('task-wild', '/data/ads', DOC) is None
        assert (
            ads_path_guard.refusal('task-wild', '/live/campaigns', DOC) is None
        )
        assert ads_path_guard.refusal('task-wild', '/live/a/b', DOC) is None

    def test_undocumented_new_path_is_still_refused(self):
        _guessed_out('task-undoc')
        assert ads_path_guard.refusal('task-undoc', '/ads/summary', DOC)
        # An exact shape does not open its whole prefix.
        assert ads_path_guard.refusal('task-undoc', '/facts/other', DOC)

    def test_query_string_is_not_part_of_the_path(self):
        _guessed_out('task-query')
        assert (
            ads_path_guard.refusal(
                'task-query', '/facts/rollup?grain=campaign', DOC
            )
            is None
        )

    def test_no_declaration_means_the_old_cap_behaviour(self):
        """With no installed bundle nothing is known documented; the
        answered set is all the guard can honour."""
        _guessed_out('task-empty')
        assert ads_path_guard.refusal(
            'task-empty', '/facts/rollup', frozenset()
        )


class TestTheDeclaredListComesFromTheBundle:
    """documented_paths() reads the service's own words: the path
    shapes written in the installed skill bundle."""

    def _install_bundle(self, monkeypatch, tmp_path):
        d = tmp_path / 'skills' / 'amazon-ads-api'
        (d / 'references').mkdir(parents=True)
        (d / 'SKILL.md').write_text(
            'Start at `/facts/rollup`. Writes: `POST /targets/update`, '
            'rows `GET /data/{dataset}`, the tree `/live/*`.\n'
            'The service lives at https://ads.test/ads-api — never '
            'call it with curl.\n',
            encoding='utf-8',
        )
        (d / 'references' / 'facts.md').write_text(
            '`/facts/search-terms?target_id=…` lists terms.\n'
            'A `python -c "x=1"` snippet must not read as a path.\n',
            encoding='utf-8',
        )
        monkeypatch.setattr(ads_skill, 'skill_dir', lambda: d)
        return d

    def test_bundle_paths_are_extracted(self, monkeypatch, tmp_path):
        self._install_bundle(monkeypatch, tmp_path)
        paths = ads_path_guard.documented_paths()
        assert {
            '/facts/rollup',
            '/targets/update',
            '/data/{dataset}',
            '/live/*',
            '/facts/search-terms',
        } <= paths

    def test_a_url_is_not_a_declared_route(self, monkeypatch, tmp_path):
        self._install_bundle(monkeypatch, tmp_path)
        assert '/ads-api' not in ads_path_guard.documented_paths()

    def test_editing_the_bundle_recomputes(self, monkeypatch, tmp_path):
        d = self._install_bundle(monkeypatch, tmp_path)
        assert '/live/campaigns' not in ads_path_guard.documented_paths()
        # A re-synced bundle (new file content) is picked up without
        # an explicit invalidation call.
        time.sleep(0.01)
        (d / 'SKILL.md').write_text('`/live/campaigns` now.\n', 'utf-8')
        assert '/live/campaigns' in ads_path_guard.documented_paths()

    def test_no_bundle_no_declared_paths(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ads_skill, 'skill_dir', lambda: tmp_path / 'absent')
        assert ads_path_guard.documented_paths() == frozenset()
