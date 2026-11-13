"""A stale ads skill is noticed on the next call, not the next day.

The ``ads_skill`` job checks the service's version once a day. The
service also names that version on **every** answer it gives
(``X-Skill-Version``), and these tests are what this client does with
it: one background refresh when an answer says something this machine
does not hold, and nothing at all when it says the same thing back.

What is asserted is the trigger — the decision to ask — never the
download itself: pulling the bundle is the job's work, and its own
behaviour is :mod:`tests.unit.test_ads_skill_sync`.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import logging
from typing import Any

import httpx
import pytest

from app import ads_client, ads_skill
from app.models.app_settings import AppSettings
from app.sync import jobs as sync_jobs

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

INSTALLED = 'v1'
NEWER = 'v2'


class FakeSession:
    """The ``AppSettings`` row this machine would read at boot.

    Counts its reads: half of what is being tested is that a header is
    answered from memory and not from the database.
    """

    def __init__(self, version: str | None = INSTALLED):
        self.opens = 0
        self.rows = {}
        if version is not None:
            self.rows[ads_skill.VERSION_KEY] = AppSettings(
                key=ads_skill.VERSION_KEY, value=version
            )

    async def get(self, _model, key):
        self.opens += 1
        return self.rows.get(key)

    def add(self, row):
        self.rows[row.key] = row

    async def commit(self) -> None:
        pass


class FakeScheduler:
    """The sync scheduler, reduced to what these tests assert on."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None

    async def trigger(self, name: str, reason: str, force: bool = False):
        self.calls.append({'name': name, 'reason': reason, 'force': force})
        if self.error is not None:
            raise self.error
        return None


class _Response:
    def __init__(
        self,
        status: int = 200,
        body: Any = None,
        headers: dict | None = None,
    ):
        self.status_code = status
        payload = json.dumps(body if body is not None else {'ok': True})
        self.content = payload.encode()
        self.text = payload
        # The real header type, so the lookup is case-insensitive the
        # way the wire is.
        self.headers = httpx.Headers(headers or {})

    def json(self) -> Any:
        return json.loads(self.content)


class _Client:
    """Answers what the service would — including not answering."""

    def __init__(self, outcomes: list[Any]):
        self.outcomes = outcomes
        self.calls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method, url, **kwargs):
        index = len(self.calls)
        self.calls.append(f'{method} {url}')
        only = self.outcomes[0] if len(self.outcomes) == 1 else None
        if callable(only):
            # One callable answers by URL: a test can drive two paths
            # through the same service.
            outcome = only(url)
        elif index < len(self.outcomes):
            outcome = self.outcomes[index]
        elif only is not None:
            # A steady service: the same answer to the same call.
            outcome = only
        else:
            raise AssertionError(f'service ran out of answers at {url}')
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def service(monkeypatch, tmp_path):
    """A bound service, a machine holding ``v1``, the job's trigger faked."""
    root = tmp_path / 'skills' / ads_skill.SKILL_NAME
    root.mkdir(parents=True)
    (root / 'SKILL.md').write_text(f'# {INSTALLED}', encoding='utf-8')
    monkeypatch.setattr(ads_skill, 'skill_dir', lambda: root)

    session = FakeSession()
    scheduler = FakeScheduler()

    @asynccontextmanager
    async def fake_async_session():
        yield session

    async def fake_credentials(_db):
        return 'https://ads.example', 'vas_secret'

    monkeypatch.setattr(ads_skill, 'async_session', fake_async_session)
    monkeypatch.setattr(ads_skill, 'sync_scheduler', scheduler)
    monkeypatch.setattr(ads_client, '_credentials', fake_credentials)
    # A cold machine: nothing read, nothing asked for yet.
    ads_skill.reset_for_tests()

    def answer(*outcomes):
        """The service's next answers, in order (one = steady state)."""
        client = _Client(list(outcomes))
        monkeypatch.setattr(
            ads_client.httpx, 'AsyncClient', lambda *a, **kw: client
        )
        return client

    yield session, scheduler, answer, monkeypatch
    ads_skill.reset_for_tests()


#: Every wait in this file is bounded by this. A refresh that never
#: arrives, or never starts, is a FAILED test — not a hung CI job that
#: has to be killed by hand. (A negative-control run on "the header is
#: never read at all" hung on the unbounded version of this.)
SETTLE_TIMEOUT = 5.0


async def _settled() -> None:
    """Let every refresh the calls scheduled run to its end."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + SETTLE_TIMEOUT
    while pending := list(ads_skill._pending):
        left = deadline - loop.time()
        if left <= 0:
            raise AssertionError(
                f'{len(pending)} scheduled refresh(es) never finished'
            )
        await asyncio.wait_for(
            asyncio.gather(*pending, return_exceptions=True), left
        )


def _header(version: str | None) -> dict:
    if version is None:
        return {}
    return {ads_client.SKILL_VERSION_HEADER: version}


class TestOneTriggerPerMismatch:
    async def test_a_differing_header_triggers_once(self, service):
        _session, scheduler, answer, _mp = service
        answer(_Response(headers=_header(NEWER)))

        await ads_client.call(None, '/facts/rollup')
        await _settled()

        assert scheduler.calls == [
            {
                'name': sync_jobs.ADS_SKILL_JOB,
                'reason': 'stale',
                'force': False,
            }
        ]

    async def test_the_version_we_hold_triggers_nothing(self, service):
        _session, scheduler, answer, _mp = service
        answer(_Response(headers=_header(INSTALLED)))

        await ads_client.call(None, '/facts/rollup')
        await _settled()

        assert scheduler.calls == []

    async def test_nothing_is_asked_of_a_service_that_says_nothing(
        self, service
    ):
        """``note_service_version``'s own contract, not the header's.

        An empty version is no information. Treated as a mismatch it
        would ask the job for a refresh on every call, since nothing
        this machine installs is ever empty.
        """
        _session, scheduler, _answer, _mp = service
        ads_skill._installed = INSTALLED

        ads_skill.note_service_version('')
        await _settled()

        assert scheduler.calls == []
        assert ads_skill._pending == set()

    async def test_a_missing_header_triggers_nothing(self, service):
        """An older service, or a proxy that ate the header.

        No information is not the same as "up to date", which is why
        the daily job stays the backstop.
        """
        _session, scheduler, answer, _mp = service
        answer(_Response(headers=_header(None)))

        await ads_client.call(None, '/facts/rollup')
        await _settled()

        assert scheduler.calls == []

    async def test_an_empty_header_triggers_nothing(self, service):
        _session, scheduler, answer, _mp = service
        answer(_Response(headers=_header('')))

        await ads_client.call(None, '/facts/rollup')
        await _settled()

        assert scheduler.calls == []

    async def test_the_refreshs_own_two_calls_are_never_news(self, service):
        """``/skill/version`` and ``/skill/bundle`` answer with the very
        version being installed, so reading their header would make
        every refresh notice itself and start the next one."""
        _session, scheduler, answer, _mp = service
        answer(_Response(headers=_header(NEWER)))

        await ads_client.call(None, '/skill/version')
        await ads_client.call(None, '/skill/bundle')
        await _settled()

        assert scheduler.calls == []

    async def test_a_pass_through_skill_call_is_skipped_too(self, service):
        """The agent can call those paths through ``/api/ads/call``;
        the skip is by path, not by who asked."""
        _session, scheduler, answer, _mp = service
        answer(_Response(headers=_header(NEWER)))

        await ads_client.call(None, 'skill/version')
        await _settled()

        assert scheduler.calls == []

    async def test_every_other_path_is_read(self, service):
        _session, scheduler, answer, _mp = service
        answer(_Response(headers=_header(NEWER)))

        await ads_client.call(None, '/sync', method='POST')
        await _settled()

        assert len(scheduler.calls) == 1


class TestRefusalsAndFailures:
    async def test_a_refused_answer_still_tells_us(self, service):
        """404 is the shape of a stale skill: the path moved.

        The header is read before the status becomes an exception, so
        the call that fails is allowed to carry the news.
        """
        _session, scheduler, answer, _mp = service
        answer(
            _Response(
                status=404,
                body={'detail': 'Not Found'},
                headers=_header(NEWER),
            )
        )

        with pytest.raises(ads_client.AdsServiceError):
            await ads_client.call(None, '/facts/pulse')
        await _settled()

        assert len(scheduler.calls) == 1

    async def test_a_5xx_answer_still_tells_us(self, service):
        _session, scheduler, answer, _mp = service
        answer(
            _Response(
                status=503,
                body={'detail': 'down'},
                headers=_header(NEWER),
            )
        )

        with pytest.raises(ads_client.AdsServiceError):
            await ads_client.call(None, '/facts/rollup')
        await _settled()

        assert len(scheduler.calls) == 1

    async def test_a_request_that_never_arrived_tells_us_nothing(self, service):
        """No answer, so no version: not news of a change."""
        _session, scheduler, answer, _mp = service
        answer(httpx.ConnectError('nothing listening'))

        with pytest.raises(ads_client.AdsServiceError):
            await ads_client.call(None, '/facts/rollup')
        await _settled()

        assert scheduler.calls == []

    async def test_a_failing_refresh_does_not_break_the_call(
        self, service, caplog
    ):
        """The agent asked for a rollup, not for a skill."""
        _session, scheduler, answer, _mp = service
        scheduler.error = RuntimeError('the sync job exploded')
        answer(_Response(headers=_header(NEWER)))

        with caplog.at_level(logging.WARNING):
            got = await ads_client.call(None, '/facts/rollup')
            await _settled()

        assert got == {'ok': True}
        assert len(scheduler.calls) == 1
        assert 'ads skill refresh' in caplog.text


class TestOffTheRequestPath:
    async def test_the_call_answers_while_the_refresh_is_still_running(
        self, service, monkeypatch
    ):
        """The agent asked for a rollup; a download is not its business.

        The refresh starts inside the call and finishes after it. Were
        the sync awaited, every call of the ten minutes after a service
        deploy would sit behind a bundle download — and a service that
        answers slowly at that moment would slow the agent down for a
        reason it cannot see.
        """
        _session, scheduler, answer, _mp = service
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_trigger(name, reason, force=False):
            started.set()
            await release.wait()
            scheduler.calls.append({
                'name': name,
                'reason': reason,
                'force': force,
            })

        monkeypatch.setattr(scheduler, 'trigger', slow_trigger)
        answer(_Response(headers=_header(NEWER)))

        got = await ads_client.call(None, '/facts/rollup')
        # Bounded, so "the refresh never started" reads as a failed
        # assertion here rather than as this test hanging.
        await asyncio.wait_for(started.wait(), SETTLE_TIMEOUT)

        assert got == {'ok': True}
        assert scheduler.calls == []

        release.set()
        await _settled()
        assert len(scheduler.calls) == 1


class TestStormGuard:
    async def test_one_trigger_per_version_per_cooldown(self, service):
        """A bundle download that keeps failing must not turn every
        agent call into a sync run."""
        _session, scheduler, answer, _mp = service
        answer(_Response(headers=_header(NEWER)))

        for _ in range(6):
            await ads_client.call(None, '/facts/rollup')
        await _settled()

        assert len(scheduler.calls) == 1

    async def test_after_the_cooldown_a_persistent_mismatch_asks_again(
        self, service
    ):
        """The guard limits how often, never whether.

        The ledger is aged out rather than the clock sped up: what is
        being tested is that an entry stops suppressing once it is a
        cooldown old, and a still-current entry keeps suppressing.
        """
        _session, scheduler, answer, _mp = service
        answer(_Response(headers=_header(NEWER)))

        for _ in range(2):
            await ads_client.call(None, '/facts/rollup')
        await _settled()
        assert len(scheduler.calls) == 1

        ads_skill._refreshed_at[NEWER] -= ads_skill.STALE_REFRESH_COOLDOWN + 1
        for _ in range(2):
            await ads_client.call(None, '/facts/rollup')
        await _settled()

        assert len(scheduler.calls) == 2

    async def test_a_different_version_is_not_the_one_on_cooldown(
        self, service
    ):
        """Two ships in the night: the second is news of its own."""
        _session, scheduler, answer, _mp = service
        answer(
            _Response(headers=_header(NEWER)),
            _Response(headers=_header('v3')),
        )

        for _ in range(2):
            await ads_client.call(None, '/facts/rollup')
        await _settled()

        assert [c['reason'] for c in scheduler.calls] == ['stale', 'stale']


class TestInstalledVersionIsMemory:
    async def test_a_header_matching_what_we_hold_reads_no_db(self, service):
        """The point of holding it: an answer per call, no query."""
        session, scheduler, answer, _mp = service
        ads_skill._installed = NEWER

        for _ in range(3):
            answer(_Response(headers=_header(NEWER)))
            await ads_client.call(None, '/facts/rollup')
        await _settled()

        assert session.opens == 0
        assert scheduler.calls == []

    async def test_sync_bundle_moves_it_so_the_next_header_is_not_news(
        self, service
    ):
        """The whole point of the memory: the refresh that installed v2
        is also what stops v2 from being news again — and it stops it
        without a database read."""
        session, scheduler, answer, _mp = service

        answer(_Response(headers=_header(NEWER)))
        await ads_client.call(None, '/facts/rollup')
        await _settled()
        assert len(scheduler.calls) == 1

        # The job's own run, over the same fake service: version, then
        # bundle, installed under the fake session it would use.
        def ship(url):
            if url.endswith('/skill/bundle'):
                return _Response(
                    body={'version': NEWER, 'files': {'SKILL.md': f'# {NEWER}'}}
                )
            return _Response(body={'version': NEWER, 'files': 1})

        answer(ship)
        result = await ads_skill.sync_bundle(session)

        assert result['updated'] is True
        assert (ads_skill.skill_dir() / 'SKILL.md').read_text() == f'# {NEWER}'
        assert await ads_skill.installed_version() == NEWER

        reads_before = session.opens
        answer(_Response(headers=_header(NEWER)))
        await ads_client.call(None, '/facts/rollup')
        await _settled()

        assert session.opens == reads_before
        assert len(scheduler.calls) == 1

    async def test_after_a_restart_the_memory_is_seeded_from_the_row(
        self, service
    ):
        """Cold machine, current service: nothing to do, and exactly one
        read to find that out."""
        session, scheduler, answer, _mp = service
        assert ads_skill._installed is None

        for _ in range(3):
            answer(_Response(headers=_header(INSTALLED)))
            await ads_client.call(None, '/facts/rollup')
        await _settled()

        assert scheduler.calls == []
        assert session.opens == 1

    async def test_remove_forgets_it_so_a_rebind_installs_again(self, service):
        """Unbinding drops the skill; the service's next header has to
        be news again, or the skill stays missing until a run that also
        looks at the files."""
        _session, scheduler, answer, _mp = service
        ads_skill._installed = INSTALLED

        await ads_skill.remove()
        assert await ads_skill.installed_version() == ''

        answer(_Response(headers=_header(INSTALLED)))
        await ads_client.call(None, '/facts/rollup')
        await _settled()

        assert len(scheduler.calls) == 1


class TestTheCallItselfIsUntouched:
    async def test_the_answer_the_agent_gets_is_untouched(self, service):
        """Not the status, not the body, not the file framing."""
        _session, _scheduler, answer, _mp = service
        answer(_Response(headers=_header(NEWER), body={'rows': []}))

        got = await ads_client.call(None, '/facts/rollup')

        assert got == {'rows': []}

    async def test_an_attachment_is_still_an_attachment(self, service):
        _session, _scheduler, answer, _mp = service
        response = _Response(
            body='day,cost\n2026-01-01,1.00\n',
            headers={
                **_header(NEWER),
                'content-disposition': 'attachment; filename="rollup.csv"',
                'x-meta': json.dumps({'rows': 1}),
            },
        )
        response.content = b'day,cost\n2026-01-01,1.00\n'
        response.text = 'day,cost\n2026-01-01,1.00\n'
        answer(response)

        got = await ads_client.call(None, '/facts/rollup')

        assert isinstance(got, ads_client.AdsFile)
        assert got.meta == {'rows': 1}
