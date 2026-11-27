"""A server not on the port it hands its agents says so on the first request.

Pins app/port_check.py: one ERROR naming both ports and the fix when the
socket a request arrived on differs from BACKEND_PORT; nothing when they
match; test transports (no IP host) are skipped without using up the
check.
"""

import logging

import pytest

from app.port_check import PortCheckMiddleware

pytestmark = pytest.mark.unit


async def _noop_app(scope, receive, send):
    return None


def _scope(host, port):
    return {'type': 'http', 'server': (host, port)}


async def _hit(mw, host, port):
    await mw(_scope(host, port), None, None)


async def test_a_mismatch_logs_one_error_naming_the_fix(caplog):
    mw = PortCheckMiddleware(_noop_app, configured_port=7777)
    with caplog.at_level(logging.ERROR, logger='app.port_check'):
        await _hit(mw, '127.0.0.1', 7797)
        await _hit(mw, '127.0.0.1', 7797)
    errors = [r.getMessage() for r in caplog.records]
    assert len(errors) == 1
    assert 'BACKEND_PORT=7777' in errors[0]
    assert 'listens on 7797' in errors[0]
    assert 'vibe-seller start --port 7797' in errors[0]


async def test_a_matching_port_logs_nothing(caplog):
    mw = PortCheckMiddleware(_noop_app, configured_port=7797)
    with caplog.at_level(logging.ERROR, logger='app.port_check'):
        await _hit(mw, '127.0.0.1', 7797)
    assert not caplog.records


async def test_a_test_transport_is_skipped_and_the_check_stays_armed(caplog):
    mw = PortCheckMiddleware(_noop_app, configured_port=7777)
    with caplog.at_level(logging.ERROR, logger='app.port_check'):
        await _hit(mw, 'testserver', 80)
        assert not caplog.records
        await _hit(mw, '::1', 7797)
    assert len(caplog.records) == 1
