"""Live tests for the profile save-time validation probe.

Marked ``ai`` so they run only in the AI-integration workflow (which
injects the real ``{PROVIDER}_API_KEY`` secrets), never on the per-PR
fast lane. Each provider self-skips when its key is absent, so the file
is a no-op locally and in workflows that don't set a given key.

Proves ``validate_profile_env`` against the actual vendor endpoints —
the matrix the config UI relies on:

    good key + good model  -> ok
    good key + bad  model  -> rejected (stale/typo'd model id)
    bad  key + good model  -> rejected (auth)

Env-secret overrides (``{PROVIDER}_BASE_URL`` / ``_MODEL``) win over the
shipped preset, mirroring how the E2E harness builds a profile.
"""

import os

import pytest

from app.ai.profile_validation import validate_profile_env
from app.ai.profiles import PROVIDER_PRESETS

pytestmark = pytest.mark.ai

# Providers whose secrets may be present in CI (each gated individually).
PROVIDERS = ['minimax', 'deepseek', 'kimi', 'glm']


def _secret(provider: str, suffix: str) -> str:
    return (os.environ.get(f'{provider.upper()}_{suffix}') or '').strip()


def _good_env(provider: str) -> dict:
    """Preset env + real key, with secret base-url/model overrides."""
    env = dict(PROVIDER_PRESETS[provider]['env'])
    env['ANTHROPIC_AUTH_TOKEN'] = _secret(provider, 'API_KEY')
    if base := _secret(provider, 'BASE_URL'):
        env['ANTHROPIC_BASE_URL'] = base
    if model := _secret(provider, 'MODEL'):
        env['ANTHROPIC_MODEL'] = model
    return env


def _require_key(provider: str) -> None:
    if not _secret(provider, 'API_KEY'):
        pytest.skip(f'{provider.upper()}_API_KEY not set')


@pytest.mark.parametrize('provider', PROVIDERS)
class TestLiveProfileValidation:
    async def test_good_key_good_model_passes(self, provider):
        _require_key(provider)
        result = await validate_profile_env(_good_env(provider))
        assert result.ok, (
            f'{provider}: expected ok, got {result.code}: {result.error}'
        )

    async def test_bad_model_handled(self, provider):
        """A bogus model id is either rejected (providers that validate
        ids server-side, e.g. DeepSeek) OR silently accepted with the
        endpoint reporting a *different*, real served model (providers
        that fall back, e.g. MiniMax → ``MiniMax-M3``). Either way the
        probe surfaces a signal the caller can act on; what it must
        never do is pass the bogus id back as if it were honored."""
        _require_key(provider)
        bogus = 'no-such-model-vibe-seller-xyz'
        env = _good_env(provider)
        env['ANTHROPIC_MODEL'] = bogus
        result = await validate_profile_env(env)
        if result.ok:
            assert result.reported_model and result.reported_model != bogus, (
                f'{provider}: accepted a bogus model without reporting the '
                f'real one (reported={result.reported_model!r})'
            )
        else:
            assert result.code in {'http_error', 'not_found', 'protocol'}

    async def test_bad_key_rejected(self, provider):
        _require_key(provider)
        env = _good_env(provider)
        env['ANTHROPIC_AUTH_TOKEN'] = 'sk-invalid-vibe-seller-000'
        result = await validate_profile_env(env)
        assert not result.ok, f'{provider}: a bogus key must be rejected'
