"""Auto-sync of the provider-preset catalog from the project's main.

``app/ai/provider_presets.py`` ships the preset catalog as literal
dicts. This module keeps that file as the ONE source of truth (the
provenance comments live there and must not be lost to a JSON mirror)
and periodically pulls the copy on ``main`` so a running install picks
up new providers without a release.

Fetch → parse → validate → cache → serve:

* **Fetch** ``raw.githubusercontent.com/.../app/ai/provider_presets.py``
  (``PROVIDER_PRESETS_URL`` overrides), capped at
  :data:`MAX_BODY_BYTES` (enforced while streaming, not after), on the
  central sync scheduler's ``presets`` job (app/sync/jobs.py).
* **Parse** with :mod:`ast` + :func:`ast.literal_eval`, reading ONLY
  the top-level ``PROVIDER_PRESETS`` / ``PROVIDER_MODELS``
  assignments. The fetched file is data, never code: it is never
  executed or imported.
* **Validate** every field against a schema (see :func:`validate`).
  The remote body configures API endpoints, so it is treated as
  hostile: unknown fields are dropped, env keys are allowlisted,
  ``ANTHROPIC_BASE_URL`` must be ``https`` (and both it and
  ``ANTHROPIC_MODEL`` are required), and ``load_global_mcp`` can
  never turn on. A preset that fails validation is dropped — falling back
  to the bundled copy of the same id where one exists — and a body
  that parses but yields no valid preset is rejected wholesale. The
  bundled catalog is the FLOOR: an id the body omits is served from
  bundled, so a stale or partial ``main`` can never unpick a provider
  this release shipped (see :func:`resolve_catalog`).
* **Cache** the validated result at
  ``$VIBE_SELLER_DIR/cache/provider_presets.json`` (atomic write),
  keyed by the sha256 of the raw fetched bytes, so an unchanged body
  is a no-op. The response's ETag is stored beside it and sent back as
  ``If-None-Match``, so an unchanged upstream costs a 304 instead of a
  download (the sha256 comparison stays as the fallback for servers
  without an ETag). The cache is re-validated on load: a corrupt or
  hand-edited cache is a miss, never a source of config.
* **Serve** through :func:`current_presets` / :func:`current_models`,
  which back ``GET /api/profiles/presets``, the telemetry
  ``profile_kind`` lookup and the ``model_sees_images`` vision
  lookup. The request path never touches the network.

**Residual trust assumption.** The sync source is the project's own
``main`` branch — the same trust as a release artifact: whoever can
push there can also ship code, so this adds no new principal. What
keeps a compromised/edited *body* from doing harm anyway: the
literal-only parse (no code execution), the env-key allowlist (no
``ANTHROPIC_AUTH_TOKEN`` / ``ANTHROPIC_API_KEY`` exfiltration hook,
no ``HTTPS_PROXY`` / ``NODE_OPTIONS`` smuggling, no Bedrock/Vertex
retargeting), ``https``-only base URLs with no userinfo, forced
``load_global_mcp=False``, and the fact that presets only ever seed
NEWLY created profiles — a synced preset never retargets a profile
that already exists.
"""

import ast
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import httpx

from app.ai.provider_presets import PROVIDER_MODELS, PROVIDER_PRESETS
from app.env_options import Options
from app.sync import state as sync_state
from app.workspace.manager import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)

# The catalog as it stands on main. Same file this package bundles.
DEFAULT_PRESETS_URL = (
    'https://raw.githubusercontent.com/zpoint/vibe-seller/main'
    '/app/ai/provider_presets.py'
)

# Fetched body ceiling, enforced WHILE STREAMING (and via
# Content-Length before a byte is read) and checked before parsing:
# an oversized body is a hostile or mistaken source, buffering it
# into memory is free RAM for whoever wrote it, and ast on a multi-MB
# adversarial tree is free CPU.
MAX_BODY_BYTES = 512 * 1024

HTTP_TIMEOUT_S = 15.0

# Per-field length caps. Everything here ends up on a config page or
# in an env var; nothing legitimate is anywhere near these.
_MAX_ID = 40
_MAX_LABEL = 200
_MAX_ETAG = 200
_MAX_DESC = 500
_MAX_ENV_VALUE = 2000
_MAX_PRESETS = 100
_MAX_MODELS = 50

_PRESET_ID_RE = re.compile(r'^[a-z0-9_]{1,40}$')
_CONTROL_RE = re.compile(r'[\x00-\x1f\x7f]')

# RFC 7232 entity-tag: ``[W/] DQUOTE *etagc DQUOTE`` where ``etagc``
# is visible ASCII except the quote itself. No ``^``/``$`` — matched
# with ``fullmatch`` so a trailing newline can't slip through the way
# ``$`` would allow it.
_ETAG_RE = re.compile(r'(W/)?"[\x21\x23-\x7e]*"')

# Env keys a preset may set. Everything else is a rejection — in
# particular every key that could carry a credential
# (ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY), redirect traffic
# (HTTPS_PROXY / NODE_OPTIONS) or switch provider backends
# (CLAUDE_CODE_USE_BEDROCK / VERTEX).
_ENV_KEYS = frozenset({
    'ANTHROPIC_BASE_URL',
    'ANTHROPIC_MODEL',
    'CLAUDE_CODE_SUBAGENT_MODEL',
    'API_TIMEOUT_MS',
    'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC',
    'ENABLE_TOOL_SEARCH',
    'CLAUDE_CODE_EFFORT_LEVEL',
    'CLAUDE_CODE_AUTO_COMPACT_WINDOW',
    'CLAUDE_CODE_MAX_CONTEXT_TOKENS',
})
_TIER_MODEL_RE = re.compile(r'^ANTHROPIC_DEFAULT_[A-Z]+_MODEL$')


class PresetsSourceError(ValueError):
    """The fetched body is not a parseable provider-presets module."""


def _env_key_allowed(key: str) -> bool:
    """Whether ``key`` is an env var a preset may set."""
    return key in _ENV_KEYS or bool(_TIER_MODEL_RE.match(key))


def _text_ok(value: object, max_len: int) -> bool:
    """Whether ``value`` is a non-empty, single-field-length string.

    Control characters are rejected everywhere: these strings land in
    env vars, log lines and a UI, and no legitimate name, label or
    endpoint value carries one.
    """
    return (
        isinstance(value, str)
        and 0 < len(value) <= max_len
        and not _CONTROL_RE.search(value)
    )


def _sane_etag(value: object) -> str | None:
    """The ETag to store/send verbatim, or ``None`` if unusable.

    RFC 7232's entity-tag and nothing else: ``[W/] "*etagc*"`` with
    ``etagc = %x21 / %x23-7E`` (visible ASCII minus the quote), via
    :data:`_ETAG_RE`, plus the length cap. The grammar is ASCII-only
    on purpose: httpx DECODES response headers leniently (ascii, then
    utf-8, then latin-1) but ASCII-ENCODES request headers strictly,
    so a non-ASCII validator that got stored would raise on the very
    next ``If-None-Match`` — and, with the bad etag never cleared,
    wedge every later fetch. Anything outside the grammar is treated
    as absent: not stored, never sent.
    """
    if not isinstance(value, str) or len(value) > _MAX_ETAG:
        return None
    return value if _ETAG_RE.fullmatch(value) else None


def parse_presets_source(text: str) -> tuple[dict, dict]:
    """Parse provider-presets source into ``(presets, models)``.

    Only the top-level literal assignments to ``PROVIDER_PRESETS``
    and ``PROVIDER_MODELS`` are read — via :func:`ast.literal_eval`,
    never ``exec``/``import`` — so the fetched body cannot run code.
    Raises :class:`PresetsSourceError` on syntax errors, non-literal
    values, missing dicts, or the wrong shape.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError) as e:
        raise PresetsSourceError(f'unparseable source: {e}') from e

    found: dict[str, dict] = {}
    for node in tree.body:
        name = None
        value_node = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                name, value_node = target.id, node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            name, value_node = node.target.id, node.value
        if name not in ('PROVIDER_PRESETS', 'PROVIDER_MODELS'):
            continue
        try:
            value = ast.literal_eval(value_node)
        except (ValueError, TypeError, MemoryError, RecursionError) as e:
            raise PresetsSourceError(f'{name} is not a literal: {e}') from e
        if not isinstance(value, dict):
            raise PresetsSourceError(f'{name} must be a dict')
        found[name] = value

    if 'PROVIDER_PRESETS' not in found or 'PROVIDER_MODELS' not in found:
        raise PresetsSourceError(
            'source must define PROVIDER_PRESETS and PROVIDER_MODELS'
        )
    return found['PROVIDER_PRESETS'], found['PROVIDER_MODELS']


def _valid_base_url(url: str) -> bool:
    """Whether ``url`` is a plausible Anthropic-compatible endpoint.

    ``https`` only, a host, no credentials-in-URL, no whitespace.
    A ``{Placeholder}`` host (``qwen_intl``'s ``{WorkspaceId}``) is
    deliberately legal — the UI blocks saving until the user replaces
    it, so the catalog must be able to carry it.
    """
    if not _text_ok(url, _MAX_ENV_VALUE):
        return False
    if any(ch.isspace() for ch in url):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return (
        parts.scheme == 'https'
        and bool(parts.netloc)
        and '@' not in parts.netloc
    )


def _validate_env(env: object) -> dict | None:
    """Validate a preset ``env`` mapping; ``None`` when invalid.

    Every key must be allowlisted and every value a plain string:
    one disallowed key (an auth token, a proxy, a backend switch)
    invalidates the whole preset. ``ANTHROPIC_BASE_URL`` and
    ``ANTHROPIC_MODEL`` are REQUIRED — the model id is what the
    preset's dropdown is checked against, so a preset without one can
    only be half a config.
    """
    if not isinstance(env, dict) or not env:
        return None
    out: dict[str, str] = {}
    for key, value in env.items():
        if not _env_key_allowed(key):
            return None
        if not _text_ok(value, _MAX_ENV_VALUE):
            return None
        out[key] = value
    if not _valid_base_url(out.get('ANTHROPIC_BASE_URL', '')):
        return None
    if not _text_ok(out.get('ANTHROPIC_MODEL'), _MAX_ENV_VALUE):
        return None
    return out


def _validate_models(entries: object, expected_first_id: str) -> list | None:
    """Validate one preset's model dropdown; ``None`` when invalid.

    Unknown fields are dropped. The first entry must be the preset's
    own ``ANTHROPIC_MODEL`` — the contract the catalog comments pin —
    so a model list that disagrees with its preset is rejected with
    the preset rather than served incoherently.
    """
    if not isinstance(entries, list) or not entries:
        return None
    if len(entries) > _MAX_MODELS:
        return None
    out = []
    for entry in entries:
        if not isinstance(entry, dict):
            return None
        model_id = entry.get('id')
        label = entry.get('label')
        if not _text_ok(model_id, _MAX_LABEL):
            return None
        if not _text_ok(label, _MAX_LABEL):
            return None
        item = {'id': model_id, 'label': label}
        context = entry.get('context')
        if context is not None:
            if not _text_ok(context, _MAX_LABEL):
                return None
            item['context'] = context
        if 'vision' in entry:
            if not isinstance(entry['vision'], bool):
                return None
            item['vision'] = entry['vision']
        out.append(item)
    if out[0]['id'] != expected_first_id:
        return None
    return out


def _validate_preset(preset_id: object, preset: object) -> dict | None:
    """Validate one preset; ``None`` when it must not be served.

    Unknown fields are DROPPED (never passed through to the UI) and
    ``load_global_mcp`` is normalized to ``False`` — remote content
    must never turn on loading the user's global MCP servers.
    """
    if not isinstance(preset_id, str) or not _PRESET_ID_RE.match(preset_id):
        return None
    if not isinstance(preset, dict):
        return None
    if not _text_ok(preset.get('name'), _MAX_LABEL):
        return None
    if preset.get('load_global_mcp', False) is not False:
        return None
    env = _validate_env(preset.get('env'))
    if env is None:
        return None

    out: dict = {'name': preset['name'], 'load_global_mcp': False, 'env': env}
    for field, cap in (
        ('description', _MAX_DESC),
        ('group', _MAX_LABEL),
        ('variant', _MAX_LABEL),
    ):
        value = preset.get(field)
        if value is None:
            continue
        if not _text_ok(value, cap):
            return None
        out[field] = value
    return out


def validate(presets: object, models: object) -> tuple[dict, dict] | None:
    """Validate a whole parsed catalog; ``None`` = reject the body.

    Per-preset failure drops just that preset (the caller may fall
    back to the bundled copy of the same id). Model lists for unknown
    preset ids are ignored. A catalog with no surviving preset, or
    with more entries than :data:`_MAX_PRESETS`, is rejected whole —
    the caller keeps whatever it was already serving.
    """
    if not isinstance(presets, dict) or not isinstance(models, dict):
        return None
    if len(presets) > _MAX_PRESETS:
        return None

    out_presets: dict[str, dict] = {}
    out_models: dict[str, list] = {}
    for preset_id, preset in presets.items():
        validated = _validate_preset(preset_id, preset)
        if validated is None:
            continue
        entries = models.get(preset_id)
        if entries is not None:
            # A preset and its dropdown are one contract (the first
            # entry IS the preset's ANTHROPIC_MODEL); a mismatched
            # pair falls back to the bundled copy together.
            dropdown = _validate_models(
                entries, validated['env']['ANTHROPIC_MODEL']
            )
            if dropdown is None:
                continue
            out_models[preset_id] = dropdown
        out_presets[preset_id] = validated

    if not out_presets:
        return None
    return out_presets, out_models


def resolve_catalog(presets: object, models: object) -> tuple[dict, dict]:
    """Validate a fetched catalog over the bundled one as a floor.

    Two rules, in order:

    * A fetched preset that FAILS validation is dropped — and, when
      the bundled catalog has that id, the BUNDLED version is used
      instead, so a hostile edit can only ever fall back to what the
      release shipped.
    * A bundled id the body omits is served from bundled too. The
      bundled catalog is the floor: whatever a release shipped can
      never disappear because ``main`` was stale, partial or pruned
      at fetch time. Ids that exist ONLY upstream (a preset a synced
      install picked up earlier) do live and die with ``main``, so
      upstream removals still reach new profiles.

    Raises :class:`PresetsSourceError` when the body must be rejected
    wholesale (nothing valid survives) — the caller keeps whatever it
    was already serving rather than resetting to the floor.
    """
    result = validate(presets, models)
    if result is None:
        raise PresetsSourceError('no valid preset in source')
    out_presets, out_models = result
    for preset_id in PROVIDER_PRESETS:
        if preset_id in out_presets:
            continue
        # Missing or rejected upstream, known to this release.
        out_presets[preset_id] = dict(PROVIDER_PRESETS[preset_id])
        out_models[preset_id] = list(PROVIDER_MODELS.get(preset_id, []))
    return out_presets, out_models


# ── Cache ────────────────────────────────────────────────────────


def _cache_path() -> Path:
    return VIBE_SELLER_DIR / 'cache' / 'provider_presets.json'


def _read_cache_blob() -> dict:
    """The cache file's catalog fields, or ``{}``."""
    try:
        blob = json.loads(_cache_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return blob if isinstance(blob, dict) else {}


# The fetch bookkeeping (body sha256, response validator, source URL)
# lives in the ``sync_state`` row of the ``presets`` job — beside every
# other sync job's state — and NOT in the cache file, which is catalog
# data and nothing else. Behind two module-level coroutines so a unit
# test can substitute an in-memory store without a database.
async def _load_bookkeeping() -> dict:
    return await sync_state.load_bookkeeping('presets')


async def _save_bookkeeping(**fields) -> dict:
    return await sync_state.save_bookkeeping('presets', **fields)


def _load_cache() -> tuple[dict, dict] | None:
    """Cached catalog, re-validated; ``None`` on any doubt.

    The cache is re-validated on every load: a corrupt, truncated or
    hand-edited file is treated as a miss (bundled data wins), never
    as a source of endpoint config.
    """
    blob = _read_cache_blob()
    if not blob:
        return None
    try:
        return resolve_catalog(blob['presets'], blob['models'])
    except (KeyError, TypeError, PresetsSourceError):
        return None


def _write_cache(
    sha256: str,
    source_url: str,
    presets: dict,
    models: dict,
    etag: str | None = None,
) -> None:
    """Persist the validated catalog atomically (tmp + rename).

    ``sha256`` / ``source_url`` / ``etag`` are bookkeeping and go to
    ``sync_state``; the file keeps the catalog alone.
    """
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'presets': presets,
        'models': models,
    }
    tmp = path.parent / f'{path.name}.tmp'
    tmp.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    os.replace(tmp, path)


# ── Current (served) data ────────────────────────────────────────

# ``(presets, models)`` currently served, or ``None`` before first
# access. Never mutated in place — a sync swaps in a fresh tuple, so
# a reader mid-iteration sees one consistent catalog.
_current: tuple[dict, dict] | None = None
# Whether ``_current`` reflects the cache file on disk (set after a
# cache load or a cache write). An unchanged body is only a true
# no-op when it is.
_current_matches_cache = False


def _bundled() -> tuple[dict, dict]:
    return PROVIDER_PRESETS, PROVIDER_MODELS


def _ensure_current() -> tuple[dict, dict]:
    global _current, _current_matches_cache
    if _current is None:
        cached = _load_cache()
        if cached is not None:
            _current = cached
            _current_matches_cache = True
        else:
            _current = _bundled()
    return _current


def current_presets() -> dict:
    """The provider presets currently served (cache-or-bundled)."""
    return _ensure_current()[0]


def current_models() -> dict:
    """The model dropdowns currently served (cache-or-bundled)."""
    return _ensure_current()[1]


def _apply(presets: dict, models: dict) -> None:
    """Swap in a freshly validated catalog for the accessors."""
    global _current, _current_matches_cache
    _current = (presets, models)
    _current_matches_cache = True


def reset() -> None:
    """Drop the in-memory catalog (tests / manual recovery)."""
    global _current, _current_matches_cache
    _current = None
    _current_matches_cache = False


# ── Fetch ────────────────────────────────────────────────────────


def _source_url() -> str:
    return Options.PROVIDER_PRESETS_URL.get().strip() or DEFAULT_PRESETS_URL


# Injectable so tests can hand fetch_once a MockTransport-backed
# client without patching httpx globally.
_client_factory = httpx.AsyncClient


def _declared_length(resp: httpx.Response) -> int | None:
    """The response's own Content-Length, or ``None`` if absent/odd."""
    try:
        value = int(resp.headers.get('content-length', ''))
    except ValueError:
        return None
    return value if value >= 0 else None


async def _read_capped(resp: httpx.Response) -> bytes | None:
    """Read at most :data:`MAX_BODY_BYTES` of the response body.

    Streams and stops the moment the cap is crossed (the response is
    closed by the caller's context manager, ending the download) — a
    hostile body never gets fully buffered. ``None`` = too big.
    """
    raw = bytearray()
    async for chunk in resp.aiter_bytes():
        raw.extend(chunk)
        if len(raw) > MAX_BODY_BYTES:
            return None
    return bytes(raw)


async def _fetch_with(
    client: httpx.AsyncClient, url: str, *, force: bool = False
) -> dict:
    meta = await _load_bookkeeping()
    cached_etag = _sane_etag(meta.get('etag'))
    # A conditional GET only when the cached validator applies to
    # what is being served: the cache holds a sane etag, it IS what
    # the accessors serve, and it was fetched from THIS url. Anything
    # else gets a full body — that is what repairs a corrupt cache or
    # retargets a changed source. ``force`` (a manual sync run) also
    # takes the full body: the point of pressing the button is to stop
    # trusting what we already have.
    conditional = (
        not force
        and cached_etag is not None
        and _current_matches_cache
        and meta.get('source_url') == url
    )
    headers = {'If-None-Match': cached_etag} if conditional else {}

    async with client.stream(
        'GET', url, timeout=HTTP_TIMEOUT_S, headers=headers
    ) as resp:
        if resp.status_code == 304:
            if not conditional:
                # A 304 we never asked for: no body to trust and
                # nothing to compare it against. Keep what we serve.
                return {
                    'status': 'failed',
                    'error': 'HTTP 304 without If-None-Match',
                }
            # "Not modified" is the answer: nothing read, nothing
            # rewritten, cache mtime untouched.
            return {
                'status': 'unchanged',
                'sha256': meta.get('sha256'),
                'etag': meta.get('etag'),
            }

        if resp.status_code != 200:
            return {'status': 'failed', 'error': f'HTTP {resp.status_code}'}

        declared = _declared_length(resp)
        if declared is not None and declared > MAX_BODY_BYTES:
            return {
                'status': 'rejected',
                'error': f'body over {MAX_BODY_BYTES} bytes',
            }

        raw = await _read_capped(resp)
        if raw is None:
            return {
                'status': 'rejected',
                'error': f'body over {MAX_BODY_BYTES} bytes',
            }
        etag = _sane_etag(resp.headers.get('etag'))

    sha = hashlib.sha256(raw).hexdigest()

    if meta.get('sha256') == sha and _current_matches_cache:
        # Identical body, already served and cached: no rewrite —
        # except for fetch metadata drift (see _refresh_cached_meta).
        if etag != meta.get('etag') or url != meta.get('source_url'):
            await _save_bookkeeping(etag=etag or None, source_url=url)
            return {
                'status': 'unchanged',
                'sha256': sha,
                'etag': etag,
                'meta_refreshed': True,
            }
        return {'status': 'unchanged', 'sha256': sha, 'etag': etag}

    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError as e:
        return {'status': 'rejected', 'error': f'not utf-8: {e}'}

    try:
        presets, models = resolve_catalog(*parse_presets_source(text))
    except PresetsSourceError as e:
        return {'status': 'rejected', 'error': str(e)}

    await asyncio.to_thread(_write_cache, sha, url, presets, models, etag=etag)
    await _save_bookkeeping(sha256=sha, etag=etag or None, source_url=url)
    _apply(presets, models)
    logger.info(
        'Provider presets updated from %s (%d presets)',
        url,
        len(presets),
    )
    return {
        'status': 'updated',
        'sha256': sha,
        'etag': etag,
        'presets': sorted(presets),
    }


async def fetch_once(
    client: httpx.AsyncClient | None = None, *, force: bool = False
) -> dict:
    """One fetch → validate → cache → serve cycle.

    ``force`` skips the conditional GET (a manual sync run wants a
    real body, not a 304). Never raises: every failure keeps the
    currently served data and is reported in the returned
    ``{'status': ...}`` dict (``updated`` / ``unchanged`` /
    ``rejected`` / ``failed``).
    """
    url = _source_url()
    try:
        if client is not None:
            result = await _fetch_with(client, url, force=force)
        else:
            async with _client_factory() as own:
                result = await _fetch_with(own, url, force=force)
    except Exception as e:
        # A sync must never crash the caller or the loop.
        logger.warning('Provider presets sync failed: %s', e)
        return {'status': 'failed', 'error': str(e)}
    if result.get('status') in ('failed', 'rejected'):
        # Loud by design: a silently-ignored bad body looks exactly
        # like "sync works but never updates anything".
        logger.warning(
            'Provider presets sync %s: %s',
            result.get('status'),
            result.get('error'),
        )
    return result


# The ``while True: fetch; sleep(48h)`` loop that used to live here is
# gone. How often the catalog is pulled is no longer a per-module env
# var: it is the ``presets`` job of the central sync scheduler
# (app/sync/jobs.py), whose next run comes from the ``sync_state``
# table.
