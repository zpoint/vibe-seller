"""The image-model catalog served to the confirm card, kept current.

Three inputs, one served list (:func:`current_models`):

1. **Catalog** — the curated family list in ``app/vision_models.py``.
   The bundled copy ships with the release; the ``vision_catalog`` sync
   job also fetches the copy on ``main`` and parses it as DATA
   (:func:`ast.literal_eval` — never imported or executed), so a model
   added on main reaches a running install without a release.
2. **Prices** — kie.ai's public pricing API
   (``POST /client/v1/model-pricing/page``, no key). Each tier names
   its own pricing row (``price_key`` + ``price_terms``); a tier whose
   row can't be matched to exactly one per-image price keeps its
   bundled price. Nothing is guessed from fuzzy similarity.
3. **Availability** — kie.ai's ``GET /api/v1/models`` (needs the user's
   key). A family whose slug kie.ai no longer lists is hidden, so the
   card never offers a model that fails on submit.

The job only runs when a kie.ai key is configured (see
``app/sync/jobs.py``): without one no image can be generated, so there
is nothing worth refreshing. Every step fails soft — the previous (or
bundled) data keeps being served — and the request path never touches
the network: it reads the in-memory state, loaded once from the cache
file and swapped wholesale by a sync.

**Trust.** The catalog source is the project's own ``main`` — the same
principal that ships releases. The body is still treated as hostile:
literal-only parse, a per-field schema (:func:`validate`), an allowlist
of reference-field names, a deny list on ``input`` param names, size
caps, and a price ceiling. A family that fails validation falls back to
its bundled copy; a body with no valid family is rejected.
"""

from __future__ import annotations

import ast
import contextlib
import dataclasses
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import parse_qs, urlsplit

from app import vision_models
from app.config import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)


_MAX_FAMILIES = 30
_MAX_TIERS = 6
_MAX_TERMS = 4
_MAX_EXTRA = 12
_MAX_RETIRED = 200
# No image here costs anywhere near this; a parse that yields more is a
# unit mistake (per-token, per-batch) or a hostile body.
MAX_USD = 2.0
# A live price further than this factor from the bundled one is more
# likely a mismatched row than a real repricing: keep the bundled price
# and log it so a maintainer refreshes the catalog.
PRICE_DRIFT_FACTOR = 4.0

_ID_RE = re.compile(r'[a-z0-9][a-z0-9.-]{0,39}')
_SUFFIX_RE = re.compile(r'[a-z0-9.]{0,12}')
_SLUG_RE = re.compile(r'[a-z0-9][a-z0-9._/-]{0,79}')
_PARAM_RE = re.compile(r'[a-z][a-z0-9_]{0,31}')
# Space-separated words; a blank term would match every row.
_TERM_RE = re.compile(r'[a-z0-9.]+(?: [a-z0-9.]+)*')
_CONTROL_RE = re.compile(r'[\x00-\x1f\x7f]')
_TOKEN_RE = re.compile(r'[a-z0-9.]+')

#: Reference-image input names this client knows how to fill.
REF_FIELDS = frozenset({'image_input', 'image_urls', 'input_urls', 'image_url'})
#: ``input`` params a catalog entry may never set statically: the prompt
#: and the references are the user's, filled per request.
_RESERVED_PARAMS = REF_FIELDS | {'prompt', 'callback_url', 'callbackurl'}


@dataclasses.dataclass(frozen=True)
class ImageModel:
    """One selectable model tier, served through kie.ai ``jobs/createTask``.

    ``id`` is OUR stable contract (what the agent/frontend/tool pass and
    what we validate); ``slug`` is kie.ai's exact ``model`` string.
    ``ref_field`` / ``ref_array`` are the name and cardinality of the
    reference-image input; ``extra`` is the static ``input`` the model's
    schema needs (aspect ratio, resolution, ...). ``price_key`` /
    ``price_terms`` locate this tier's row in kie.ai's pricing API.
    """

    id: str
    slug: str
    provider: str
    label: str
    ref_field: str
    ref_array: bool
    usd: float
    extra: dict = dataclasses.field(default_factory=dict)
    price_key: str = ''
    price_terms: tuple[str, ...] = ()


class CatalogSourceError(ValueError):
    """The fetched body is not a parseable image-model catalog."""


# ── Validation ───────────────────────────────────────────────────


def _text_ok(value: object, max_len: int, *, allow_empty=False) -> bool:
    if not isinstance(value, str):
        return False
    if not value and not allow_empty:
        return False
    return len(value) <= max_len and not _CONTROL_RE.search(value)


def _usd_ok(value: object) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 < value <= MAX_USD
    )


def _validate_extra(extra: object) -> dict | None:
    if not isinstance(extra, dict) or len(extra) > _MAX_EXTRA:
        return None
    out = {}
    for key, value in extra.items():
        if not isinstance(key, str) or not _PARAM_RE.fullmatch(key):
            return None
        if key.lower() in _RESERVED_PARAMS:
            return None
        if isinstance(value, bool | int):
            out[key] = value
        elif _text_ok(value, 40):
            out[key] = value
        else:
            return None
    return out


def _validate_tier(tier: object) -> dict | None:
    if not isinstance(tier, dict):
        return None
    suffix = tier.get('suffix', '')
    if not isinstance(suffix, str) or not _SUFFIX_RE.fullmatch(suffix):
        return None
    if not _text_ok(tier.get('label', ''), 30, allow_empty=True):
        return None
    if not _usd_ok(tier.get('usd')):
        return None
    extra = _validate_extra(tier.get('extra', {}))
    if extra is None:
        return None
    terms = tier.get('price_terms', [])
    if not isinstance(terms, list | tuple) or len(terms) > _MAX_TERMS:
        return None
    if not all(
        isinstance(t, str) and len(t) <= 32 and _TERM_RE.fullmatch(t)
        for t in terms
    ):
        return None
    return {
        'suffix': suffix,
        'label': tier.get('label', ''),
        'usd': float(tier['usd']),
        'extra': extra,
        'price_terms': list(terms),
    }


def validate_family(family: object) -> dict | None:
    """One family, normalized — or ``None`` if any field is off-schema."""
    if not isinstance(family, dict):
        return None
    fid = family.get('id')
    if not isinstance(fid, str) or not _ID_RE.fullmatch(fid):
        return None
    slug = family.get('slug')
    if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):
        return None
    price_key = family.get('price_key', slug)
    if not isinstance(price_key, str) or not _SLUG_RE.fullmatch(price_key):
        return None
    if not _text_ok(family.get('provider'), 40):
        return None
    if not _text_ok(family.get('label'), 60):
        return None
    ref_field = family.get('ref_field')
    ref_array = family.get('ref_array')
    if ref_field not in REF_FIELDS or not isinstance(ref_array, bool):
        return None
    if ref_field == 'image_url' and ref_array:
        return None
    extra = _validate_extra(family.get('extra', {}))
    if extra is None:
        return None
    tiers_in = family.get('tiers')
    if not isinstance(tiers_in, list) or not 1 <= len(tiers_in) <= _MAX_TIERS:
        return None
    tiers = [_validate_tier(t) for t in tiers_in]
    if any(t is None for t in tiers):
        return None
    suffixes = [t['suffix'] for t in tiers]
    # An empty suffix makes the model id the family id: only valid for a
    # lone tier, or two tiers would collide.
    if len(set(suffixes)) != len(suffixes) or (
        '' in suffixes and len(tiers) > 1
    ):
        return None
    return {
        'id': fid,
        'slug': slug,
        'provider': family['provider'],
        'label': family['label'],
        'ref_field': ref_field,
        'ref_array': ref_array,
        'extra': extra,
        'price_key': price_key,
        'tiers': tiers,
    }


def _bundled_families() -> list[dict]:
    out = [validate_family(f) for f in vision_models.IMAGE_FAMILIES]
    # The bundled file is ours; an invalid entry is a bug the unit tests
    # catch, never something to serve.
    return [f for f in out if f is not None]


def resolve_catalog(families: object, retired: object) -> tuple[list, dict]:
    """Validate a fetched catalog against the schema.

    A family that fails validation falls back to the bundled copy of the
    same id (or is dropped if there is none); duplicate ids keep the
    first. Ids the body omits are NOT re-added from bundled: dropping a
    superseded model on main is how curation reaches installs, and the
    retired map keeps old ids resolving. A body with no valid family
    raises :class:`CatalogSourceError`.
    """
    if not isinstance(families, list) or not families:
        raise CatalogSourceError('IMAGE_FAMILIES is not a non-empty list')
    if len(families) > _MAX_FAMILIES:
        raise CatalogSourceError(f'more than {_MAX_FAMILIES} families')
    bundled = {f['id']: f for f in _bundled_families()}
    out: list[dict] = []
    seen: set[str] = set()
    seen_models: set[str] = set()
    for raw in families:
        fam = validate_family(raw)
        if fam is None:
            rid = raw.get('id') if isinstance(raw, dict) else None
            fam = bundled.get(rid) if isinstance(rid, str) else None
            logger.warning(
                'vision catalog: invalid family %r — %s',
                rid,
                'using bundled copy' if fam else 'dropped',
            )
        if fam is None or fam['id'] in seen:
            continue
        # Two families can flatten to one model id (``flux-2`` tier
        # ``1k`` and a lone-tier ``flux-2-1k``): the card would show two
        # rows with one id and bill the first for both. Keep the first.
        tier_ids = {m.id for m in flatten([fam])}
        if tier_ids & seen_models:
            logger.warning(
                'vision catalog: family %r reuses model ids %s — dropped',
                fam['id'],
                sorted(tier_ids & seen_models),
            )
            continue
        seen.add(fam['id'])
        seen_models |= tier_ids
        out.append(fam)
    if not out:
        raise CatalogSourceError('no valid family in body')
    ids = {m.id for m in flatten(out)}
    retired_out: dict[str, str] = {}
    if isinstance(retired, dict) and len(retired) <= _MAX_RETIRED:
        for old, new in retired.items():
            if (
                isinstance(old, str)
                and _ID_RE.fullmatch(old)
                and old not in ids
                and new in ids
            ):
                retired_out[old] = new
    return out, retired_out


def parse_catalog_source(text: str) -> tuple[object, object]:
    """``(IMAGE_FAMILIES, RETIRED_MODEL_IDS)`` read from module source.

    Only top-level literal assignments are read; nothing is executed.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        raise CatalogSourceError(f'not python: {e}') from e
    found: dict[str, object] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in ('IMAGE_FAMILIES', 'RETIRED_MODEL_IDS')
        ):
            try:
                found[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError as e:
                raise CatalogSourceError(
                    f'{node.targets[0].id} is not a literal'
                ) from e
    if 'IMAGE_FAMILIES' not in found:
        raise CatalogSourceError('IMAGE_FAMILIES not found')
    return found['IMAGE_FAMILIES'], found.get('RETIRED_MODEL_IDS', {})


def flatten(families: list[dict]) -> list[ImageModel]:
    """One :class:`ImageModel` per tier, in catalog order."""
    return [
        ImageModel(
            id=f'{fam["id"]}-{tier["suffix"]}' if tier['suffix'] else fam['id'],
            slug=fam['slug'],
            provider=fam['provider'],
            label=(
                f'{fam["label"]} · {tier["label"]}'
                if tier['label']
                else fam['label']
            ),
            ref_field=fam['ref_field'],
            ref_array=fam['ref_array'],
            usd=tier['usd'],
            extra={**fam['extra'], **tier['extra']},
            price_key=fam['price_key'],
            price_terms=tuple(tier['price_terms']),
        )
        for fam in families
        for tier in fam['tiers']
    ]


# ── Price matching ───────────────────────────────────────────────


def _tokens(text: str) -> list[str]:
    # '.' stays inside a token ('2.5', '1.0s') but not at its edges, so
    # 'image to image, 4K.' still yields '4k'.
    out = (t.strip('.') for t in _TOKEN_RE.findall(text.lower()))
    return [t for t in out if t]


def _has_run(tokens: list[str], run: list[str]) -> bool:
    """Whether ``run`` occurs as a contiguous token sequence."""
    n = len(run)
    return any(tokens[i : i + n] == run for i in range(len(tokens) - n + 1))


def record_key(anchor: str) -> str:
    """A pricing row's model key: the anchor's ``model=`` value, else its
    URL path (``https://kie.ai/nano-banana-pro`` → ``nano-banana-pro``)."""
    parts = urlsplit(anchor or '')
    model = parse_qs(parts.query).get('model')
    if model:
        return model[0]
    return parts.path.strip('/')


def match_prices(
    models: list[ImageModel], records: list[dict]
) -> dict[str, float]:
    """``{model_id: usd}`` for every tier with exactly one live price.

    A row is a candidate when it is an ``image`` row priced ``per
    image``, its key equals the tier's ``price_key``, and every one of
    the tier's ``price_terms`` appears in its description. Several
    candidates are fine only if they agree on the price. Anything else
    — no row, conflicting rows, an implausible number — leaves the tier
    on its bundled price.
    """
    by_key: dict[str, list[tuple[list[str], str]]] = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        if rec.get('interfaceType') != 'image':
            continue
        if str(rec.get('creditUnit', '')).strip().lower() != 'per image':
            continue
        key = record_key(str(rec.get('anchor') or ''))
        by_key.setdefault(key, []).append((
            _tokens(str(rec.get('modelDescription') or '')),
            rec.get('usdPrice'),
        ))
    out: dict[str, float] = {}
    for m in models:
        runs = [_tokens(t) for t in m.price_terms]
        prices = set()
        for tokens, usd in by_key.get(m.price_key, []):
            if all(_has_run(tokens, r) for r in runs):
                try:
                    prices.add(round(float(usd), 6))
                except (TypeError, ValueError):
                    prices.add(math.nan)
        if len(prices) != 1:
            if prices:
                logger.warning(
                    'vision pricing: %s matched conflicting rows %s',
                    m.id,
                    sorted(prices),
                )
            continue
        price = prices.pop()
        if not _usd_ok(price):
            continue
        if not (
            m.usd / PRICE_DRIFT_FACTOR <= price <= m.usd * PRICE_DRIFT_FACTOR
        ):
            logger.warning(
                'vision pricing: %s live $%s is far from bundled $%s; '
                'keeping bundled',
                m.id,
                price,
                m.usd,
            )
            continue
        out[m.id] = price
    return out


# ── Served state ─────────────────────────────────────────────────


def price_ref(m: ImageModel) -> str:
    """How a tier's live price was found. A stored price applies only
    while the tier still names the same row: when main edits a tier's
    ``price_key`` / ``price_terms`` (e.g. to fix a mismatched row), the
    price matched under the old terms stops applying at once."""
    return '|'.join((m.price_key, *m.price_terms))


@dataclasses.dataclass(frozen=True)
class _State:
    """What the last syncs learned. Everything served is DERIVED from it
    by :func:`_derive` against THIS release's validator and bundled
    catalog — so an upgrade re-judges a cached body instead of serving
    the previous release's verdict on it."""

    #: main's IMAGE_FAMILIES / RETIRED_MODEL_IDS as fetched (raw literal
    #: data), or None = serve bundled.
    raw_families: list | None = None
    raw_retired: dict | None = None
    catalog_sha: str | None = None
    #: :func:`bundled_fingerprint` of the release that fetched the raw
    #: body. A different one means an upgrade since: its bundled catalog
    #: is newer than that body, so the body is not served until main is
    #: fetched again (a body stuck on 404 must not outlive releases).
    fetched_under: str | None = None
    #: model id -> (live usd, price_ref it was matched under)
    prices: dict = dataclasses.field(default_factory=dict)
    #: kie slugs kie.ai did NOT list on the last good check. A deny-list,
    #: not an allow-list: a model added after that check is visible.
    hidden: frozenset = frozenset()


@dataclasses.dataclass(frozen=True)
class _Served:
    families: list
    retired: dict
    models: list  # every tier, live price applied
    visible: list  # minus hidden slugs; never empty


def bundled_fingerprint() -> str:
    """Identity of THIS release's bundled catalog."""
    blob = repr((vision_models.IMAGE_FAMILIES, vision_models.RETIRED_MODEL_IDS))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _resolved(state: _State) -> tuple[list, dict]:
    if (
        state.raw_families is not None
        and state.fetched_under == bundled_fingerprint()
    ):
        try:
            return resolve_catalog(state.raw_families, state.raw_retired)
        except CatalogSourceError:
            pass
    return _bundled_families(), dict(vision_models.RETIRED_MODEL_IDS)


def _derive(state: _State) -> _Served:
    families, retired = _resolved(state)
    models = []
    for m in flatten(families):
        live = state.prices.get(m.id)
        if live is not None and live[1] == price_ref(m) and _usd_ok(live[0]):
            m = dataclasses.replace(m, usd=live[0])
        models.append(m)
    visible = [m for m in models if m.slug not in state.hidden]
    return _Served(families, retired, models, visible or models)


_state: _State | None = None
_served: _Served | None = None


def _cache_path() -> Path:
    return VIBE_SELLER_DIR / 'cache' / 'vision_catalog.json'


def _load_cache() -> _State:
    """The persisted state; any malformed part is dropped (bundled /
    catalog price / visible wins), never trusted. The catalog part is
    re-validated by ``_derive`` on every load."""
    try:
        blob = json.loads(_cache_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return _State()
    if not isinstance(blob, dict):
        return _State()
    state = _State()
    cat = blob.get('catalog')
    if isinstance(cat, dict) and isinstance(cat.get('sha256'), str):
        state = dataclasses.replace(
            state,
            raw_families=cat.get('families'),
            raw_retired=cat.get('retired'),
            catalog_sha=cat['sha256'],
            fetched_under=cat.get('fetched_under')
            if isinstance(cat.get('fetched_under'), str)
            else None,
        )
    prices = blob.get('prices')
    if isinstance(prices, dict):
        state = dataclasses.replace(
            state,
            prices={
                k: (float(v[0]), v[1])
                for k, v in prices.items()
                if isinstance(v, list)
                and len(v) == 2
                and _usd_ok(v[0])
                and isinstance(v[1], str)
            },
        )
    hidden = blob.get('hidden')
    if isinstance(hidden, list) and all(isinstance(s, str) for s in hidden):
        state = dataclasses.replace(state, hidden=frozenset(hidden))
    return state


def _write_cache(state: _State) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = {
        'catalog': {
            'sha256': state.catalog_sha,
            'families': state.raw_families,
            'retired': state.raw_retired,
            'fetched_under': state.fetched_under,
        }
        if state.catalog_sha
        else None,
        'prices': {k: list(v) for k, v in state.prices.items()},
        'hidden': sorted(state.hidden),
    }
    # A unique tmp per write: two servers sharing this home (main + a
    # demo port) may sync at once, and a shared tmp name made one
    # rename fail.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(blob, fh, indent=2)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _current() -> _State:
    global _state
    if _state is None:
        _state = _load_cache()
    return _state


def _served_now() -> _Served:
    global _served
    if _served is None:
        _served = _derive(_current())
    return _served


def _commit(**changes) -> None:
    """Persist a new state, then serve it — in that order, so a failed
    write leaves memory and disk agreeing on the old state."""
    global _state, _served
    new = dataclasses.replace(_current(), **changes)
    _write_cache(new)
    _state = new
    _served = _derive(new)


def reset() -> None:
    """Drop the in-memory state (tests / manual recovery)."""
    global _state, _served
    _state = None
    _served = None


def all_models() -> list[ImageModel]:
    """Every catalog tier with its served price, hidden ones included —
    the lookup set for an id someone already chose (a confirmed card, a
    timed-out job being resumed), which must never silently change."""
    return _served_now().models


def current_models() -> list[ImageModel]:
    """The selectable tiers: catalog order, live prices, minus models
    kie.ai no longer lists. Never empty — if availability would hide
    everything, the availability data is what's wrong."""
    return _served_now().visible


def retired_successor(model_id: str) -> str | None:
    return _served_now().retired.get(model_id)
