"""Unit tests for ``app.ai.profile_validation``.

Drives the endpoint probe with an ``httpx.MockTransport`` so every
verdict path is deterministic and offline. Pins the two contracts a
regression would silently break: the request we send matches how
Claude Code authenticates (Bearer / x-api-key, ``/v1/messages`` suffix,
the profile's own model), and each provider response shape maps to the
right ``ok``/``code``.
"""

import json

import httpx
import pytest

from app.ai.profile_validation import validate_profile_env

pytestmark = pytest.mark.unit

DEEPSEEK_ENV = {
    'ANTHROPIC_BASE_URL': 'https://api.deepseek.com/anthropic',
    'ANTHROPIC_AUTH_TOKEN': 'sk-test-token',
    'ANTHROPIC_MODEL': 'deepseek-v4-pro[1m]',
}


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _no_call(request):  # pragma: no cover - asserts it is never hit
    raise AssertionError(f'unexpected network call to {request.url}')


async def test_valid_response_round_trips_and_reports_model():
    captured = {}

    def handler(request):
        captured['url'] = str(request.url)
        captured['auth'] = request.headers.get('authorization')
        captured['version'] = request.headers.get('anthropic-version')
        captured['body'] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                'type': 'message',
                'role': 'assistant',
                'model': 'deepseek-v4-pro',
                'content': [{'type': 'text', 'text': 'pong'}],
            },
        )

    async with _client(handler) as c:
        result = await validate_profile_env(DEEPSEEK_ENV, client=c)

    assert result.ok
    assert result.code == 'ok'
    assert result.reported_model == 'deepseek-v4-pro'
    # Auth + endpoint + model must mirror runtime behavior.
    assert captured['url'] == 'https://api.deepseek.com/anthropic/v1/messages'
    assert captured['auth'] == 'Bearer sk-test-token'
    assert captured['version'] == '2023-06-01'
    assert captured['body']['model'] == 'deepseek-v4-pro[1m]'


async def test_trailing_slash_base_url_joins_cleanly():
    captured = {}

    def handler(request):
        captured['url'] = str(request.url)
        return httpx.Response(200, json={'type': 'message', 'content': []})

    env = {
        'ANTHROPIC_BASE_URL': 'https://api.kimi.com/coding/',
        'ANTHROPIC_AUTH_TOKEN': 'k',
        'ANTHROPIC_MODEL': 'k3[1m]',
    }
    async with _client(handler) as c:
        result = await validate_profile_env(env, client=c)

    assert result.ok
    assert captured['url'] == 'https://api.kimi.com/coding/v1/messages'


async def test_api_key_env_uses_x_api_key_header():
    captured = {}

    def handler(request):
        captured['x_api_key'] = request.headers.get('x-api-key')
        captured['auth'] = request.headers.get('authorization')
        return httpx.Response(200, json={'type': 'message', 'content': []})

    env = {
        'ANTHROPIC_BASE_URL': 'https://example.com/anthropic',
        'ANTHROPIC_API_KEY': 'xk-123',
        'ANTHROPIC_MODEL': 'm',
    }
    async with _client(handler) as c:
        result = await validate_profile_env(env, client=c)

    assert result.ok
    assert captured['x_api_key'] == 'xk-123'
    assert captured['auth'] is None


async def test_auth_failure_reported():
    def handler(request):
        return httpx.Response(
            401, json={'error': {'message': 'invalid api key'}}
        )

    async with _client(handler) as c:
        result = await validate_profile_env(DEEPSEEK_ENV, client=c)

    assert not result.ok
    assert result.code == 'auth'
    assert 'invalid api key' in result.error


async def test_not_found_reported():
    def handler(request):
        return httpx.Response(404, text='not found')

    async with _client(handler) as c:
        result = await validate_profile_env(DEEPSEEK_ENV, client=c)

    assert not result.ok
    assert result.code == 'not_found'


async def test_stale_model_surfaces_provider_message():
    """A retired model id most commonly comes back as a 400 with the
    provider's own message — the exact signal we want the user to see."""

    def handler(request):
        return httpx.Response(
            400,
            json={
                'error': {
                    'type': 'invalid_request_error',
                    'message': 'model deepseek-v4-pro[1m] does not exist',
                }
            },
        )

    async with _client(handler) as c:
        result = await validate_profile_env(DEEPSEEK_ENV, client=c)

    assert not result.ok
    assert result.code == 'http_error'
    assert 'does not exist' in result.error


async def test_unreachable_endpoint_reported():
    def handler(request):
        raise httpx.ConnectError('connection refused')

    async with _client(handler) as c:
        result = await validate_profile_env(DEEPSEEK_ENV, client=c)

    assert not result.ok
    assert result.code == 'unreachable'
    assert 'api.deepseek.com' in result.error


async def test_non_anthropic_200_is_protocol_error():
    """A base URL pointed at an OpenAI-style endpoint returns 200 with a
    ``choices`` body — usable-looking but the wrong protocol."""

    def handler(request):
        return httpx.Response(
            200, json={'choices': [{'message': {'content': 'hi'}}]}
        )

    async with _client(handler) as c:
        result = await validate_profile_env(DEEPSEEK_ENV, client=c)

    assert not result.ok
    assert result.code == 'protocol'


async def test_non_json_200_is_protocol_error():
    def handler(request):
        return httpx.Response(200, text='<html>hello</html>')

    async with _client(handler) as c:
        result = await validate_profile_env(DEEPSEEK_ENV, client=c)

    assert not result.ok
    assert result.code == 'protocol'


async def test_no_base_url_is_noop_pass_without_network():
    """Native-Claude shape (no endpoint) must not hit the network."""
    async with _client(_no_call) as c:
        result = await validate_profile_env(
            {'ANTHROPIC_AUTH_TOKEN': 'x'}, client=c
        )

    assert result.ok
    assert result.code == 'no_endpoint'


async def test_missing_key_reported_without_network():
    async with _client(_no_call) as c:
        result = await validate_profile_env(
            {
                'ANTHROPIC_BASE_URL': 'https://example.com/anthropic',
                'ANTHROPIC_MODEL': 'm',
            },
            client=c,
        )

    assert not result.ok
    assert result.code == 'missing_key'


async def test_missing_model_reported_without_network():
    async with _client(_no_call) as c:
        result = await validate_profile_env(
            {
                'ANTHROPIC_BASE_URL': 'https://example.com/anthropic',
                'ANTHROPIC_AUTH_TOKEN': 'tok',
            },
            client=c,
        )

    assert not result.ok
    assert result.code == 'missing_model'
