"""Probe an AI profile's endpoint before we let the user save it.

Every non-``default`` profile routes the agent through an
Anthropic-compatible endpoint declared entirely in the profile's
``env`` (``ANTHROPIC_BASE_URL`` + a token + ``ANTHROPIC_MODEL``). Nothing
today checks that those three actually work together: a typo'd base
URL, a wrong/expired key, or a model name that a provider has since
retired (these vendors rev model ids often) all save cleanly and only
blow up later on a real task run, with an opaque agent error.

This module gives the profile router a single async predicate that
makes one minimal Anthropic ``/v1/messages`` request against the
profile's own config and reports whether it round-trips. The router
calls it from a dedicated ``/validate`` endpoint that the Settings UI
hits on save, so the failure surfaces on the config page — in the
user's words, "ask the endpoint what model it is" before trusting it.

Design notes:

- We mirror how Claude Code authenticates so a pass here means a pass
  at runtime: ``ANTHROPIC_AUTH_TOKEN`` becomes ``Authorization: Bearer``
  and ``ANTHROPIC_API_KEY`` becomes ``x-api-key`` (both if both are set).
- A profile with no ``ANTHROPIC_BASE_URL`` is the native-Claude case
  (the ``default`` profile shape); there is no third-party endpoint to
  probe, so validation is a no-op pass.
- The token is never logged. Results carry a machine-readable ``code``
  plus a human ``error`` string (often the provider's own message,
  e.g. "model not found" — which is exactly the stale-model signal).
"""

import json
import logging
import re

import httpx

logger = logging.getLogger(__name__)

# Matches an unfilled ``{Placeholder}`` left in a preset base URL (e.g.
# the Alibaba International ``{WorkspaceId}`` template). Saving one is a
# guaranteed runtime failure, so the probe rejects it before any call.
_PLACEHOLDER_RE = re.compile(r'\{[^}]+\}')

# Trailing ``[1m]`` 1M-context tag on a model id (e.g. ``glm-5.2[1m]``).
# This is a Claude Code CLIENT convention, confirmed in the CLI source
# (``parseUserSpecifiedModel`` in src/utils/model/model.ts does
# ``normalizedModel.replace(/\[1m]$/i, '')`` and requests the
# ``context-1m-2025-08-07`` beta). Some endpoints (DeepSeek, MiniMax)
# also accept the literal suffix server-side; others (Z.AI/GLM,
# DashScope/Qwen) 400 on it. We strip it exactly as the client does so
# the probe reflects real runtime behavior for every provider.
_CONTEXT_TAG_RE = re.compile(r'\[1m\]$', re.IGNORECASE)

BASE_URL_KEY = 'ANTHROPIC_BASE_URL'
MODEL_KEY = 'ANTHROPIC_MODEL'
AUTH_TOKEN_KEY = 'ANTHROPIC_AUTH_TOKEN'
API_KEY_KEY = 'ANTHROPIC_API_KEY'

_ANTHROPIC_VERSION = '2023-06-01'
# Long enough that a provider defaulting to a thinking model does not
# 400 on too-small a budget, small enough to stay a trivial probe.
_PROBE_MAX_TOKENS = 64
_TIMEOUT_SECONDS = 20.0


class ProfileValidationResult:
    """Outcome of probing a profile's endpoint.

    ``ok`` is the gate the router enforces. ``reported_model`` is the
    ``model`` string the endpoint echoed back on success (handy for the
    UI to confirm which model actually answered). ``code``/``error``
    describe the failure for display; ``code`` is stable for i18n/tests,
    ``error`` is human-facing and may embed the provider's own text.
    """

    def __init__(
        self,
        ok: bool,
        *,
        code: str = 'ok',
        error: str = '',
        reported_model: str | None = None,
    ):
        self.ok = ok
        self.code = code
        self.error = error
        self.reported_model = reported_model

    def to_dict(self) -> dict:
        return {
            'ok': self.ok,
            'code': self.code,
            'error': self.error,
            'reported_model': self.reported_model,
        }


def _probe_url(base_url: str) -> str:
    """Join a profile base URL to the messages endpoint the way the
    Anthropic SDK does — a single ``/v1/messages`` suffix, tolerant of a
    trailing slash on the base (e.g. Kimi's ``.../coding/``)."""
    return base_url.rstrip('/') + '/v1/messages'


def _auth_headers(env: dict) -> dict:
    """Build auth headers mirroring Claude Code's env → header mapping."""
    headers: dict[str, str] = {}
    token = (env.get(AUTH_TOKEN_KEY) or '').strip()
    api_key = (env.get(API_KEY_KEY) or '').strip()
    if token:
        headers['Authorization'] = f'Bearer {token}'
    if api_key:
        headers['x-api-key'] = api_key
    return headers


def _extract_error_message(resp: httpx.Response) -> str:
    """Pull the most useful human string out of an error response.

    Anthropic-shaped errors nest it at ``error.message``; fall back to
    ``message``, then the raw (truncated) body."""
    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError):
        return (resp.text or '').strip()[:300]
    if isinstance(data, dict):
        err = data.get('error')
        if isinstance(err, dict) and err.get('message'):
            return str(err['message'])
        if isinstance(err, str) and err:
            return err
        if data.get('message'):
            return str(data['message'])
    return json.dumps(data)[:300]


def _interpret(
    resp: httpx.Response, requested_model: str
) -> ProfileValidationResult:
    """Turn a probe response into a validation verdict."""
    status = resp.status_code
    if status == 200:
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            return ProfileValidationResult(
                ok=False,
                code='protocol',
                error=(
                    'Endpoint returned 200 but the body was not JSON; it '
                    'does not look like an Anthropic-compatible API.'
                ),
            )
        if isinstance(data, dict) and (
            data.get('type') == 'message'
            or 'content' in data
            or data.get('role') == 'assistant'
        ):
            return ProfileValidationResult(
                ok=True,
                reported_model=(
                    str(data.get('model')) if data.get('model') else None
                ),
            )
        # A 200 that isn't an Anthropic message — most often an
        # OpenAI-style ``choices`` body from a base URL pointed at the
        # wrong (non-Anthropic) endpoint.
        return ProfileValidationResult(
            ok=False,
            code='protocol',
            error=(
                'Endpoint responded but not in Anthropic message format. '
                "Check that the base URL is the provider's "
                'Anthropic-compatible endpoint.'
            ),
        )
    message = _extract_error_message(resp)
    if status in (401, 403):
        return ProfileValidationResult(
            ok=False,
            code='auth',
            error=f'Authentication failed (HTTP {status}). '
            f'Check the API key. {message}'.strip(),
        )
    if status == 404:
        return ProfileValidationResult(
            ok=False,
            code='not_found',
            error=f'Endpoint not found (HTTP 404). Check the base URL. '
            f'{message}'.strip(),
        )
    # 400 most commonly means a stale/invalid model id for these
    # providers — surface the provider's own message verbatim.
    return ProfileValidationResult(
        ok=False,
        code='http_error',
        error=f'HTTP {status}: {message}'.strip(),
    )


async def validate_profile_env(
    env: dict | None,
    *,
    client: httpx.AsyncClient | None = None,
) -> ProfileValidationResult:
    """Probe *env*'s Anthropic endpoint and report if it round-trips.

    No-op pass when there is no ``ANTHROPIC_BASE_URL`` (native Claude —
    nothing third-party to probe). Otherwise a missing API key or model
    is reported without a network call. *client* is injectable so tests
    can drive it with an ``httpx.MockTransport``.
    """
    env = env or {}
    base_url = (env.get(BASE_URL_KEY) or '').strip()
    if not base_url:
        return ProfileValidationResult(ok=True, code='no_endpoint')

    placeholder = _PLACEHOLDER_RE.search(base_url)
    if placeholder:
        return ProfileValidationResult(
            ok=False,
            code='placeholder',
            error=(
                f'Base URL still contains the placeholder '
                f'{placeholder.group(0)} — replace it with your real '
                'value before saving.'
            ),
        )

    headers = _auth_headers(env)
    if not headers:
        return ProfileValidationResult(
            ok=False,
            code='missing_key',
            error='API key is required (set ANTHROPIC_AUTH_TOKEN or '
            'ANTHROPIC_API_KEY).',
        )

    model = (env.get(MODEL_KEY) or '').strip()
    if not model:
        return ProfileValidationResult(
            ok=False,
            code='missing_model',
            error=f'Model is required (set {MODEL_KEY}).',
        )

    # Strip a trailing beta-context tag (e.g. ``glm-5.2[1m]`` ->
    # ``glm-5.2``) before probing. ``[1m]`` is a Claude Code CLIENT
    # convention — the client parses it, requests the 1M-context beta,
    # and sends the BARE model id to the API. Some endpoints reject the
    # literal suffixed string (Z.AI: ``[1211] Unknown Model``; DashScope:
    # 400), so probing the raw string would false-negative a config that
    # works at runtime. We mirror the client and probe the bare id.
    probe_model = _CONTEXT_TAG_RE.sub('', model)

    url = _probe_url(base_url)
    headers.update({
        'anthropic-version': _ANTHROPIC_VERSION,
        'content-type': 'application/json',
    })
    body = {
        'model': probe_model,
        'max_tokens': _PROBE_MAX_TOKENS,
        'messages': [{'role': 'user', 'content': 'ping'}],
    }

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=_TIMEOUT_SECONDS)
    try:
        try:
            resp = await client.post(url, json=body, headers=headers)
        except httpx.HTTPError as e:
            logger.debug('Profile probe to %s failed: %s', url, e)
            return ProfileValidationResult(
                ok=False,
                code='unreachable',
                error=f'Could not reach {base_url}: {e}',
            )
        return _interpret(resp, requested_model=model)
    finally:
        if owns_client:
            await client.aclose()
