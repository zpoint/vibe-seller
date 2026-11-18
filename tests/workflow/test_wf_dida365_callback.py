"""The Dida365 OAuth callback: a public page on the app's own origin.

It takes no auth (the provider's redirect lands on it), so a link anyone
can send reaches it, and what it shows runs with the visitor's session.
Text from outside — the ``error`` parameter, the token endpoint's error
— must come back as text, never as markup. And the real redirect, the
one a user connecting TickTick goes through, still connects.
"""

import time

import httpx
import pytest

from app.routers import dida365_oauth as oauth
from app.utils.crypto import encrypt_password

pytestmark = pytest.mark.workflow

_SCRIPT = '<script>fetch("/api/tokens",{method:"POST"})</script>'


async def test_a_crafted_error_link_shows_text_not_script(unauthed_client):
    r = await unauthed_client.get(
        '/api/dida365/callback', params={'error': _SCRIPT}
    )
    assert r.status_code == 400
    assert r.headers['content-type'].startswith('text/html')
    assert '<script' not in r.text
    assert '&lt;script&gt;fetch(' in r.text


async def test_a_providers_own_error_still_reads_plainly(unauthed_client):
    r = await unauthed_client.get(
        '/api/dida365/callback', params={'error': 'access_denied'}
    )
    assert r.status_code == 400
    assert 'Error: access_denied' in r.text


@pytest.fixture
def pending_connect(monkeypatch):
    """A connect the user started: a live state and saved credentials."""
    monkeypatch.setitem(
        oauth._pending_states,
        'st-1',
        {'redirect_uri': 'http://test/cb', 'expires': time.time() + 600},
    )
    saved = {}
    monkeypatch.setattr(
        oauth,
        'load_backend_config',
        lambda name: {
            'client_id': 'cid',
            'client_secret_enc': encrypt_password('secret'),
            'token_url': 'https://provider.invalid/oauth/token',
        },
    )
    monkeypatch.setattr(
        oauth, 'save_backend_config', lambda name, cfg: saved.update(cfg)
    )
    monkeypatch.setattr(oauth, '_register_ticktick_mcp', lambda cfg: None)
    monkeypatch.setattr(oauth.telemetry, 'send', lambda *a, **k: None)
    return saved


def _token_endpoint(monkeypatch, answer):
    async def post(self, url, **kwargs):
        return answer(httpx.Request('POST', url))

    monkeypatch.setattr(httpx.AsyncClient, 'post', post)


async def test_a_token_endpoint_error_shows_text_not_script(
    unauthed_client, pending_connect, monkeypatch
):
    def refuse(request):
        raise httpx.HTTPError(f'upstream said {_SCRIPT}')

    _token_endpoint(monkeypatch, refuse)
    r = await unauthed_client.get(
        '/api/dida365/callback', params={'code': 'c', 'state': 'st-1'}
    )
    assert r.status_code == 500
    assert '<script' not in r.text
    assert 'upstream said &lt;script&gt;' in r.text


async def test_the_real_redirect_still_connects(
    unauthed_client, pending_connect, monkeypatch
):
    _token_endpoint(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json={'access_token': 'at', 'expires_in': 3600},
            request=request,
        ),
    )
    r = await unauthed_client.get(
        '/api/dida365/callback', params={'code': 'c', 'state': 'st-1'}
    )
    assert r.status_code == 200
    assert 'Connected Successfully!' in r.text
    # The page still closes the popup the user connected from.
    assert '<script>window.close()</script>' in r.text
    assert pending_connect['access_token_enc']
