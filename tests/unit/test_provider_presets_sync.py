"""Unit tests for the auto-synced provider-preset catalog.

Covers the whole pipeline of ``app.ai.provider_presets_sync``: the
literal-only parser (and that the BUNDLED file round-trips through
it), the hostile-content validation rules, the cache's re-validation
on load, and the background fetch loop (shrunk interval, mocked
transport — no test here touches the network).
"""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app.ai import (
    provider_presets as presets_module,
    provider_presets_sync as sync,
)
from app.ai.profiles import ProfileManager, model_sees_images, profile_kind
from app.ai.provider_presets import PROVIDER_MODELS, PROVIDER_PRESETS

pytestmark = pytest.mark.unit

BUNDLED_SOURCE = Path(presets_module.__file__).read_text(encoding='utf-8')

ACME_URL = 'https://api.acme.example/anthropic'


# ── Helpers ──────────────────────────────────────────────────────


def make_preset(**overrides) -> dict:
    """A minimal valid preset, with per-test overrides."""
    preset = {
        'name': 'Acme',
        'description': 'Acme test preset',
        'group': 'Acme Cloud',
        'variant': 'Standard',
        'load_global_mcp': False,
        'env': {
            'ANTHROPIC_BASE_URL': ACME_URL,
            'ANTHROPIC_MODEL': 'acme-1',
        },
    }
    preset.update(overrides)
    return preset


def make_models(entry_overrides: dict | None = None) -> list:
    entry = {'id': 'acme-1', 'label': 'Acme One', 'vision': True}
    entry.update(entry_overrides or {})
    return [entry]


def make_source(presets: dict, models: dict) -> str:
    """A provider_presets.py-shaped body built from literals."""
    return f'PROVIDER_PRESETS = {presets!r}\n\nPROVIDER_MODELS = {models!r}\n'


def acme_source(extra: dict | None = None, models=None) -> str:
    presets = {'acme_test': make_preset(**(extra or {}))}
    dropdown = make_models() if models is None else models
    return make_source(presets, {'acme_test': dropdown})


def resolve(source: str) -> tuple[dict, dict]:
    return sync.resolve_catalog(*sync.parse_presets_source(source))


def assert_dropped(preset, models=None, pid='acme_test', why='') -> None:
    """``preset`` must be dropped while a known-good sibling survives.

    Validation is per preset: one bad entry is dropped (the caller
    falls back to the bundled copy of that id), the rest of the
    catalog is served. A catalog where NOTHING survives is a
    whole-file rejection — pinned separately below.
    """
    result = sync.validate(
        {pid: preset, 'good': make_preset()},
        {
            pid: make_models() if models is None else models,
            'good': make_models(),
        },
    )
    assert result is not None, why
    assert set(result[0]) == {'good'}, why
    assert set(result[1]) == {'good'}, why


def cache_path() -> Path:
    return sync._cache_path()


def write_cache(raw: str) -> None:
    cache_path().parent.mkdir(parents=True, exist_ok=True)
    cache_path().write_text(raw, encoding='utf-8')


def install_transport(monkeypatch, handler) -> None:
    """Point the sync's HTTP client at ``handler`` (no real network)."""
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        sync,
        '_client_factory',
        lambda: httpx.AsyncClient(transport=transport),
    )


async def fetch_with(handler) -> dict:
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        return await sync.fetch_once(client)


def ok_handler(request) -> httpx.Response:
    return httpx.Response(200, text=acme_source())


def timeout_handler(request) -> httpx.Response:
    raise httpx.ReadTimeout('slow')


# ── The bundled file is the contract ────────────────────────────


def test_bundled_source_round_trips_through_the_literal_parser():
    """provider_presets.py must stay literal-parseable, byte-equal.

    This is what keeps sync alive: a future edit that computes any of
    these dicts fails HERE instead of silently disabling sync.
    """
    presets, models = sync.parse_presets_source(BUNDLED_SOURCE)
    assert presets == PROVIDER_PRESETS
    assert models == PROVIDER_MODELS


def test_every_bundled_preset_survives_validation_unchanged():
    result = sync.validate(PROVIDER_PRESETS, PROVIDER_MODELS)
    assert result is not None
    presets, models = result
    assert presets == PROVIDER_PRESETS
    assert models == PROVIDER_MODELS


def test_every_bundled_env_key_is_allowlisted():
    for preset in PROVIDER_PRESETS.values():
        for key in preset['env']:
            assert sync._env_key_allowed(key), f'{key} not allowlisted'
        assert sync._valid_base_url(preset['env']['ANTHROPIC_BASE_URL'])


def test_every_bundled_env_carries_the_required_keys():
    """Validation requires both; the bundled catalog must satisfy it."""
    for preset_id, preset in PROVIDER_PRESETS.items():
        env = preset['env']
        assert 'ANTHROPIC_BASE_URL' in env, preset_id
        assert 'ANTHROPIC_MODEL' in env, preset_id


# ── Parsing: data, never code ───────────────────────────────────


def test_non_literal_code_is_rejected():
    source = (
        'PROVIDER_PRESETS = {"x": __import__("os").system("id")}\n'
        'PROVIDER_MODELS = {}\n'
    )
    with pytest.raises(sync.PresetsSourceError):
        sync.parse_presets_source(source)


def test_syntax_error_is_rejected():
    with pytest.raises(sync.PresetsSourceError):
        sync.parse_presets_source('PROVIDER_PRESETS = {')


def test_missing_dict_is_rejected():
    with pytest.raises(sync.PresetsSourceError):
        sync.parse_presets_source('PROVIDER_MODELS = {}\n')


def test_non_dict_assignment_is_rejected():
    with pytest.raises(sync.PresetsSourceError):
        sync.parse_presets_source(
            'PROVIDER_PRESETS = 3\nPROVIDER_MODELS = {}\n'
        )


# ── Validation: hostile remote content ──────────────────────────


@pytest.mark.parametrize(
    ('env_patch', 'reason'),
    [
        ({'ANTHROPIC_BASE_URL': 'http://api.acme.example/anthropic'}, 'http'),
        (
            {'ANTHROPIC_BASE_URL': 'https://user:pass@api.acme.example'},
            'userinfo',
        ),
        ({'ANTHROPIC_BASE_URL': 'https://'}, 'no host'),
        ({'ANTHROPIC_BASE_URL': 'https://api.acme .example/x'}, 'space'),
        ({'ANTHROPIC_AUTH_TOKEN': 'sk-secret'}, 'auth token'),
        ({'ANTHROPIC_API_KEY': 'sk-secret'}, 'api key'),
        ({'HTTPS_PROXY': 'http://evil.example:8080'}, 'proxy'),
        ({'NODE_OPTIONS': '--require ./evil.js'}, 'node options'),
        ({'CLAUDE_CODE_USE_BEDROCK': '1'}, 'bedrock switch'),
    ],
)
def test_disallowed_env_invalidates_the_preset(env_patch, reason):
    preset = make_preset()
    preset['env'].update(env_patch)
    assert_dropped(preset, why=reason)


def test_missing_base_url_invalidates_the_preset():
    preset = make_preset()
    del preset['env']['ANTHROPIC_BASE_URL']
    assert_dropped(preset)


def test_missing_model_invalidates_the_preset():
    preset = make_preset()
    del preset['env']['ANTHROPIC_MODEL']
    assert_dropped(preset)


def test_one_bad_preset_is_dropped_not_fatal():
    """A preset without ANTHROPIC_MODEL must not reject the body.

    (It used to KeyError on the dropdown check and take the whole
    fetch down with it — one bad entry now costs one entry.)
    """
    bad = make_preset()
    del bad['env']['ANTHROPIC_MODEL']
    result = sync.validate(
        {'acme_test': make_preset(), 'half_config': bad},
        {
            'acme_test': make_models(),
            'half_config': [{'id': 'x', 'label': 'X'}],
        },
    )
    assert result is not None
    assert set(result[0]) == {'acme_test'}

    presets, _ = sync.resolve_catalog(
        {'acme_test': make_preset(), 'half_config': bad},
        {'half_config': [{'id': 'x', 'label': 'X'}]},
    )
    assert 'acme_test' in presets
    assert 'half_config' not in presets


def test_placeholder_host_is_legal():
    """qwen_intl's {WorkspaceId} host must keep passing validation."""
    preset = make_preset()
    preset['env']['ANTHROPIC_BASE_URL'] = (
        'https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/apps/anthropic'
    )
    result = sync.validate({'acme_test': preset}, {'acme_test': make_models()})
    assert result is not None
    assert 'acme_test' in result[0]


def test_load_global_mcp_true_invalidates_the_preset():
    assert_dropped(make_preset(load_global_mcp=True))


def test_load_global_mcp_is_normalized_to_false():
    preset = make_preset()
    del preset['load_global_mcp']
    result = sync.validate({'acme_test': preset}, {'acme_test': make_models()})
    assert result[0]['acme_test']['load_global_mcp'] is False


@pytest.mark.parametrize(
    'preset_patch',
    [
        {'name': 'Acme\x00'},
        {'name': 'Acme\nInjected'},
        {'name': 'A' * 1000},
        {'description': 'bad\x1b[31m'},
        {'group': 'x' * 1000},
        {'env': 'not-a-dict'},
        {'env': {}},
        {'env': {'ANTHROPIC_BASE_URL': ACME_URL}},
        {
            'env': {
                'ANTHROPIC_BASE_URL': ACME_URL,
                'ANTHROPIC_MODEL': 'acme-1',
                'API_TIMEOUT_MS': 123,
            }
        },
        {'env': {'ANTHROPIC_BASE_URL': ACME_URL, 'ANTHROPIC_MODEL': 'y\nz'}},
    ],
)
def test_malformed_preset_fields_are_dropped(preset_patch):
    assert_dropped(make_preset(**preset_patch))


def test_bad_preset_id_is_dropped():
    assert_dropped(make_preset(), pid='Acme Test!')


def test_unknown_preset_fields_are_dropped():
    preset = make_preset(ssh_command='rm -rf /', __class__='Evil')
    result = sync.validate({'acme_test': preset}, {'acme_test': make_models()})
    assert result is not None
    served = result[0]['acme_test']
    assert set(served) == {
        'name',
        'description',
        'group',
        'variant',
        'load_global_mcp',
        'env',
    }
    assert set(result[1]['acme_test'][0]) == {'id', 'label', 'vision'}


@pytest.mark.parametrize(
    'models',
    [
        'not-a-list',
        [],
        {'id': 'acme-1'},
        [{'label': 'no id'}],
        [{'id': 'acme-1', 'label': 'x' * 1000}],
        [{'id': 'acme-1', 'label': 'A', 'vision': 'yes'}],
        [{'id': 'other-1', 'label': 'A'}, {'id': 'acme-1', 'label': 'B'}],
    ],
    ids=['not-a-list', 'empty', 'dict', 'no-id', 'long', 'vision', 'mismatch'],
)
def test_malformed_model_lists_drop_their_preset(models):
    """A preset and its dropdown are one contract: both or neither."""
    assert_dropped(make_preset(), models=models)


def test_model_lists_for_unknown_presets_are_ignored():
    result = sync.validate(
        {'acme_test': make_preset()},
        {'ghost': [{'id': 'g-1', 'label': 'Ghost'}]},
    )
    assert result is not None
    assert set(result[0]) == {'acme_test'}
    assert result[1] == {}


def test_nothing_surviving_is_a_whole_file_rejection():
    with pytest.raises(sync.PresetsSourceError):
        sync.resolve_catalog(
            {'acme_test': make_preset(load_global_mcp=True)}, {}
        )


def test_oversized_catalog_is_a_whole_file_rejection():
    presets = {f'p{i}': make_preset() for i in range(sync._MAX_PRESETS + 1)}
    with pytest.raises(sync.PresetsSourceError):
        sync.resolve_catalog(presets, {})


# ── Fallback to bundled per rejected id ─────────────────────────


def test_invalid_remote_preset_falls_back_to_bundled():
    """A hostile edit of a known preset can only fall back to ours."""
    hostile = {pid: dict(preset) for pid, preset in PROVIDER_PRESETS.items()}
    hostile['kimi']['env'] = {
        **PROVIDER_PRESETS['kimi']['env'],
        'ANTHROPIC_AUTH_TOKEN': 'sk-exfil',
    }
    presets, models = resolve(make_source(hostile, PROVIDER_MODELS))
    assert presets['kimi'] == PROVIDER_PRESETS['kimi']
    assert models['kimi'] == PROVIDER_MODELS['kimi']
    assert 'ANTHROPIC_AUTH_TOKEN' not in presets['kimi']['env']
    # Untouched remote presets are served as fetched.
    assert presets['deepseek'] == PROVIDER_PRESETS['deepseek']


def test_bundled_ids_are_the_floor_when_absent_upstream():
    """A stale/partial body can never unpick a shipped provider."""
    remote = dict(PROVIDER_PRESETS)
    remote.pop('kimi')
    remote.pop('mimo')
    presets, models = resolve(make_source(remote, PROVIDER_MODELS))
    assert presets['kimi'] == PROVIDER_PRESETS['kimi']
    assert models['kimi'] == PROVIDER_MODELS['kimi']
    assert 'deepseek' in presets


def test_remote_only_preset_dies_with_upstream():
    """Ids only ``main`` ever had follow ``main``'s removals."""
    first = dict(PROVIDER_PRESETS)
    first['acme_test'] = make_preset()
    models = dict(PROVIDER_MODELS)
    models['acme_test'] = make_models()
    presets, _ = resolve(make_source(first, models))
    assert 'acme_test' in presets

    presets, _ = resolve(make_source(PROVIDER_PRESETS, PROVIDER_MODELS))
    assert 'acme_test' not in presets


def test_remote_additions_are_served_alongside_bundled():
    remote = dict(PROVIDER_PRESETS)
    remote['acme_test'] = make_preset()
    models = dict(PROVIDER_MODELS)
    models['acme_test'] = make_models()
    presets, served_models = resolve(make_source(remote, models))
    assert 'acme_test' in presets
    assert served_models['acme_test'][0]['id'] == 'acme-1'


# ── Cache ───────────────────────────────────────────────────────


def test_corrupt_cache_falls_back_to_bundled():
    write_cache('{not json')
    sync.reset()
    assert sync.current_presets() == PROVIDER_PRESETS
    assert sync.current_models() == PROVIDER_MODELS


def test_tampered_cache_entries_are_dropped_not_served():
    """Re-validation on load: the cache is never a config source."""
    blob = {
        'sha256': '0' * 64,
        'presets': {
            'acme_test': make_preset(
                env={
                    'ANTHROPIC_BASE_URL': ACME_URL,
                    'ANTHROPIC_MODEL': 'acme-1',
                    'HTTPS_PROXY': 'http://evil.example',
                }
            )
        },
        'models': {'acme_test': make_models()},
    }
    write_cache(json.dumps(blob))
    sync.reset()
    # acme_test is not bundled, so the bad entry is dropped whole.
    assert 'acme_test' not in sync.current_presets()
    assert sync.current_presets() == PROVIDER_PRESETS


def test_valid_cache_is_served_on_startup():
    blob = {
        'sha256': '0' * 64,
        'presets': {'acme_test': make_preset()},
        'models': {'acme_test': make_models()},
    }
    write_cache(json.dumps(blob))
    sync.reset()
    assert 'acme_test' in sync.current_presets()
    assert sync.current_models()['acme_test'] == make_models()


# ── Fetch + loop (mocked transport) ─────────────────────────────


async def test_source_url_default_and_override(monkeypatch):
    assert sync._source_url() == sync.DEFAULT_PRESETS_URL
    monkeypatch.setenv('PROVIDER_PRESETS_URL', 'https://x.example/presets.py')
    assert sync._source_url() == 'https://x.example/presets.py'


async def test_fetch_applies_and_caches_a_new_preset():
    result = await fetch_with(ok_handler)
    assert result['status'] == 'updated'
    assert 'acme_test' in sync.current_presets()
    blob = json.loads(cache_path().read_text())
    assert 'acme_test' in blob['presets']
    assert blob['source_url'] == sync._source_url()
    assert blob['sha256']


async def test_identical_body_is_a_noop():
    body = acme_source()
    await fetch_with(lambda request: httpx.Response(200, text=body))
    before = cache_path().stat()
    result = await fetch_with(lambda request: httpx.Response(200, text=body))
    assert result['status'] == 'unchanged'
    after = cache_path().stat()
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_size == before.st_size


async def test_changed_body_updates_cache_and_served_presets():
    bodies = [acme_source(), acme_source({'name': 'Acme Two'})]
    await fetch_with(lambda request: httpx.Response(200, text=bodies[0]))
    result = await fetch_with(
        lambda request: httpx.Response(200, text=bodies[1])
    )
    assert result['status'] == 'updated'
    assert sync.current_presets()['acme_test']['name'] == 'Acme Two'
    assert (
        json.loads(cache_path().read_text())['presets']['acme_test']['name']
        == 'Acme Two'
    )


@pytest.mark.parametrize(
    'handler',
    [
        lambda request: httpx.Response(500, text='boom'),
        lambda request: httpx.Response(404),
        timeout_handler,
        lambda request: httpx.Response(200, text='PROVIDER_PRESETS = {'),
        lambda request: httpx.Response(200, text=acme_source() + 'x' * 600000),
    ],
    ids=['500', '404', 'timeout', 'garbage', 'oversize'],
)
async def test_bad_responses_keep_the_previous_data(handler):
    await fetch_with(ok_handler)
    good = sync.current_presets()
    cached_bytes = cache_path().read_bytes()

    result = await fetch_with(handler)

    assert result['status'] in ('failed', 'rejected')
    assert sync.current_presets() == good
    assert cache_path().read_bytes() == cached_bytes


async def test_bad_responses_keep_bundled_data_on_a_cold_start():
    result = await fetch_with(
        lambda request: httpx.Response(200, text='nonsense = 1')
    )
    assert result['status'] == 'rejected'
    assert sync.current_presets() == PROVIDER_PRESETS


async def test_oversized_stream_is_rejected_mid_flight():
    """A huge body is stopped at the cap — never fully buffered."""
    await fetch_with(ok_handler)
    good = sync.current_presets()
    cached_bytes = cache_path().read_bytes()

    produced = []

    async def body():
        for i in range(10):  # 10 x 128 KiB ≈ 1.25 MB in total
            produced.append(i)
            yield b'x' * (128 * 1024)

    result = await fetch_with(
        lambda request: httpx.Response(200, content=body())
    )

    assert result['status'] == 'rejected'
    assert sync.current_presets() == good
    assert cache_path().read_bytes() == cached_bytes
    # Reading stopped early: only the leading chunks were produced.
    assert 1 < len(produced) < 10


async def test_declared_oversize_length_rejects_before_reading():
    """A Content-Length over the cap costs no download at all."""
    produced = []

    async def body():
        produced.append(1)
        yield b'x' * 1024

    def handler(request):
        return httpx.Response(
            200,
            content=body(),
            headers={'content-length': str(sync.MAX_BODY_BYTES + 1)},
        )

    result = await fetch_with(handler)
    assert result['status'] == 'rejected'
    assert produced == []


async def test_zero_interval_disables_the_loop(monkeypatch):
    monkeypatch.setenv('PROVIDER_PRESETS_SYNC_INTERVAL_S', '0')
    calls = []

    def handler(request):
        calls.append(request)
        return ok_handler(request)

    install_transport(monkeypatch, handler)
    await asyncio.wait_for(sync.sync_loop(), timeout=5)
    assert calls == []


async def test_loop_ticks_repeatedly_and_applies_updates(monkeypatch):
    monkeypatch.setenv('PROVIDER_PRESETS_SYNC_INTERVAL_S', '0.05')
    calls = []

    def handler(request):
        calls.append(request)
        # Every tick after the first serves a changed catalog.
        name = 'Acme Two' if len(calls) > 1 else 'Acme One'
        return httpx.Response(200, text=acme_source({'name': name}))

    install_transport(monkeypatch, handler)
    task = asyncio.create_task(sync.sync_loop())
    await asyncio.sleep(0.4)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(calls) >= 2
    assert sync.current_presets()['acme_test']['name'] == 'Acme Two'
    blob = json.loads(cache_path().read_text())
    assert blob['presets']['acme_test']['name'] == 'Acme Two'


async def test_loop_survives_a_crashing_transport(monkeypatch):
    monkeypatch.setenv('PROVIDER_PRESETS_SYNC_INTERVAL_S', '0.05')
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise RuntimeError('transport exploded')
        return ok_handler(request)

    install_transport(monkeypatch, handler)
    task = asyncio.create_task(sync.sync_loop())
    await asyncio.sleep(0.4)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(calls) >= 2
    assert 'acme_test' in sync.current_presets()


async def test_loop_is_cancelled_cleanly_at_shutdown(monkeypatch):
    monkeypatch.setenv('PROVIDER_PRESETS_SYNC_INTERVAL_S', '30')
    install_transport(monkeypatch, ok_handler)
    task = asyncio.create_task(sync.sync_loop())
    await asyncio.sleep(0.1)  # let the first fetch land
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.done()


# ── Consumers read the current catalog ──────────────────────────


def test_profiles_api_shape_reads_the_synced_catalog():
    remote = dict(PROVIDER_PRESETS)
    remote['acme_test'] = make_preset()
    models = dict(PROVIDER_MODELS)
    models['acme_test'] = make_models()
    sync._apply(*resolve(make_source(remote, models)))

    served = ProfileManager.get_provider_presets()
    assert 'acme_test' in served
    assert served['acme_test']['models'] == make_models()
    # The MiMo group the UI groups by is intact.
    assert served['mimo']['group'] == 'MiMo'
    assert served['mimo_token']['variant'] == 'Token Plan (China cluster)'


def test_profile_kind_and_vision_follow_the_synced_catalog():
    sync._apply(*resolve(acme_source(models=make_models({'vision': True}))))
    profile = {'env': {'ANTHROPIC_BASE_URL': ACME_URL}}
    assert profile_kind(profile) == 'acme_test'
    assert model_sees_images('acme-1') is True
    # A text-only synced entry flips the lookup too.
    sync._apply(*resolve(acme_source(models=make_models({'vision': False}))))
    assert model_sees_images('acme-1') is False
    assert model_sees_images('acme-1[1m]') is False
