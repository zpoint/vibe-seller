"""Per-model token prices and context windows for the task usage meter.

The data is a filtered slice of https://models.dev (the open model
catalog opencode reads): for each provider we can talk to, every model's
USD-per-million-token rates and its context window. It is served
cache-or-bundled, like the provider presets:

* ``~/.vibe-seller/cache/model_pricing.json`` — written by the
  ``model_pricing`` sync job (:mod:`app.ai.model_pricing_sync`);
* :mod:`app.ai.model_pricing_snapshot` — the slice this release ships,
  used offline and until the first sync lands.

The math is the one every coding tool uses for third-party models
(opencode, pi, Roo/Cline): the provider reports token counts, we
multiply each bucket by its own rate. The CLI's ``total_cost_usd`` is
NOT used — it prices every model as if it were Claude, and it is
process-cumulative, so adding it per ``result`` double-counts.

A provider preset maps to ONE models.dev provider. Subscription plans
(Kimi Code, the Coding / Token Plans) are separate models.dev providers
whose rates are all zero, so a plan run is reported as ``included``
rather than given a made-up price.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import math
import os
from pathlib import Path
import re

from app.ai.model_pricing_snapshot import SNAPSHOT
from app.config import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)

#: Provider preset id (``app.ai.provider_presets``) → models.dev
#: provider id. ``default`` is the no-base-URL profile: Claude on
#: api.anthropic.com. Hosts that serve both pay-as-you-go and plan keys
#: (MiniMax, GLM) map to the pay-as-you-go provider — the key, not the
#: host, decides the billing, so the figure is an estimate either way.
PRESET_PROVIDERS: dict[str, str] = {
    'default': 'anthropic',
    'kimi': 'kimi-code-plan-global',
    'kimi_paygo': 'moonshotai-cn',
    'kimi_paygo_intl': 'moonshotai',
    'minimax': 'minimax-cn',
    'minimax_intl': 'minimax',
    'glm': 'zhipuai',
    'glm_intl': 'zai',
    'deepseek': 'deepseek',
    'qwen': 'alibaba-cn',
    'qwen_intl': 'alibaba',
    'qwen_coding': 'alibaba-coding-plan-cn',
    'qwen_coding_intl': 'alibaba-coding-plan',
    'qwen_token': 'alibaba-token-plan-cn',
    'qwen_token_intl': 'alibaba-token-plan',
    'mimo': 'xiaomi',
    'mimo_token': 'xiaomi-token-plan-cn',
    'mimo_token_sgp': 'xiaomi-token-plan-sgp',
    'mimo_token_ams': 'xiaomi-token-plan-ams',
}

#: The providers the catalog keeps (everything else in models.dev is
#: dropped at sync time).
TRACKED_PROVIDERS: frozenset[str] = frozenset(PRESET_PROVIDERS.values())

#: How a run's cost was arrived at — sent to the UI with the figure.
BASIS_CATALOG = 'catalog'  # priced from the provider's per-token rates
BASIS_INCLUDED = 'included'  # every rate is zero: plan / free model
BASIS_UNPRICED = 'unpriced'  # model not in the catalog: tokens only

_RATE_KEYS = ('input', 'output', 'cache_read', 'cache_write')
#: One-hour cache writes cost this many times the input rate.
ONE_HOUR_CACHE_WRITE_MULTIPLIER = 2.0
_DATE_SUFFIX = re.compile(r'-\d{8}$')
_BRACKET_SUFFIX = re.compile(r'\[[^\]]*\]$')


class PricingSourceError(ValueError):
    """A catalog body that must not be served."""


@dataclass(frozen=True)
class Tier:
    """Rates that apply once the prompt exceeds ``size`` tokens."""

    size: int
    rates: dict[str, float]


@dataclass(frozen=True)
class ModelPrice:
    """One model's rates (USD per 1M tokens) and context window."""

    rates: dict[str, float] | None
    tiers: tuple[Tier, ...] = ()
    context: int | None = None

    @property
    def basis(self) -> str:
        if self.rates is None:
            return BASIS_UNPRICED
        if not any(self.rates.values()) and not any(
            any(t.rates.values()) for t in self.tiers
        ):
            return BASIS_INCLUDED
        return BASIS_CATALOG


UNPRICED = ModelPrice(rates=None)


# ── Validation / normalisation of a models.dev body ──────────────


def _rate(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    value = float(value)
    if not math.isfinite(value) or value < 0 or value > 10_000:
        return None
    return value


def _rates(raw: object) -> dict[str, float] | None:
    if not isinstance(raw, dict):
        return None
    inp, out = _rate(raw.get('input')), _rate(raw.get('output'))
    if inp is None or out is None:
        return None
    rates = {'input': inp, 'output': out}
    for key in ('cache_read', 'cache_write'):
        r = _rate(raw.get(key))
        # A provider that does not list a cache rate bills those tokens
        # as plain input (that is what an uncached prompt costs).
        rates[key] = r if r is not None else inp
    return rates


def _normalize_model(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    limit = raw.get('limit') if isinstance(raw.get('limit'), dict) else {}
    ctx = limit.get('context')
    context = ctx if isinstance(ctx, int) and 0 < ctx < 100_000_000 else None
    cost = raw.get('cost')
    rates = _rates(cost)
    tiers = []
    if rates is not None and isinstance(cost.get('tiers'), list):
        for t in cost['tiers']:
            tier = t.get('tier') if isinstance(t, dict) else None
            size = tier.get('size') if isinstance(tier, dict) else None
            if (
                not isinstance(tier, dict)
                or tier.get('type') != 'context'
                or not isinstance(size, int)
                or size <= 0
            ):
                continue
            tier_rates = _rates(t)
            if tier_rates is not None:
                tiers.append({'size': size, **tier_rates})
    if rates is None and context is None:
        return None
    entry: dict = {'context': context, 'cost': rates}
    if tiers:
        entry['tiers'] = sorted(tiers, key=lambda x: x['size'])
    return entry


def normalize_catalog(body: object) -> dict:
    """models.dev ``api.json`` → ``{provider: {model: entry}}``.

    Keeps only :data:`TRACKED_PROVIDERS` and, per model, the context
    window and validated rates. Raises :class:`PricingSourceError` when
    the body does not look like models.dev (fewer than half of the
    tracked providers present) — a changed API must not blank the
    meter.
    """
    if not isinstance(body, dict):
        raise PricingSourceError('catalog is not an object')
    out: dict[str, dict] = {}
    for pid in sorted(TRACKED_PROVIDERS):
        provider = body.get(pid)
        models = provider.get('models') if isinstance(provider, dict) else None
        if not isinstance(models, dict):
            continue
        kept = {}
        for mid, raw in models.items():
            if not isinstance(mid, str) or not mid or len(mid) > 200:
                continue
            entry = _normalize_model(raw)
            if entry is not None:
                kept[mid] = entry
        if kept:
            out[pid] = kept
    if len(out) * 2 < len(TRACKED_PROVIDERS):
        raise PricingSourceError(
            f'only {len(out)} of {len(TRACKED_PROVIDERS)} tracked '
            'providers present'
        )
    return out


def slice_catalog(body: object) -> dict:
    """The part of a models.dev body we keep, in models.dev's own shape.

    Tracked providers only; per model only ``limit.context`` and
    ``cost``. This is what the cache file and the bundled snapshot
    hold, so both go back through :func:`normalize_catalog` — one
    validator for every source.
    """
    normalize_catalog(body)  # raises on a body we would not serve
    out: dict[str, dict] = {}
    for pid in sorted(TRACKED_PROVIDERS):
        provider = body.get(pid)
        models = provider.get('models') if isinstance(provider, dict) else None
        if not isinstance(models, dict):
            continue
        kept = {}
        for mid, raw in models.items():
            if not isinstance(raw, dict):
                continue
            entry = {}
            if isinstance(raw.get('limit'), dict):
                entry['limit'] = {'context': raw['limit'].get('context')}
            if isinstance(raw.get('cost'), dict):
                entry['cost'] = raw['cost']
            if entry:
                kept[mid] = entry
        if kept:
            out[pid] = {'models': kept}
    return out


# ── Served state ─────────────────────────────────────────────────


def cache_path() -> Path:
    return VIBE_SELLER_DIR / 'cache' / 'model_pricing.json'


def _bundled() -> dict:
    return normalize_catalog(SNAPSHOT)


_current: dict | None = None


def _load_cache() -> dict | None:
    try:
        blob = json.loads(cache_path().read_text(encoding='utf-8'))
        return normalize_catalog(blob)
    except (OSError, ValueError):
        return None


def current() -> dict:
    """The catalog currently served (cache-or-bundled)."""
    global _current
    if _current is None:
        _current = _load_cache() or _bundled()
    return _current


def write_cache(sliced: dict) -> None:
    """Persist a :func:`slice_catalog` result atomically."""
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f'{path.name}.tmp'
    tmp.write_text(json.dumps(sliced, sort_keys=True), encoding='utf-8')
    os.replace(tmp, path)


def apply(catalog: dict) -> None:
    """Swap in a freshly validated catalog."""
    global _current
    _current = catalog


def reset() -> None:
    """Drop the in-memory catalog (tests / manual recovery)."""
    global _current
    _current = None


# ── Lookup ───────────────────────────────────────────────────────


def _model_keys(model: str) -> list[str]:
    """Candidate catalog ids for a model id the provider reported."""
    m = model.strip()
    m = m.rsplit('/', 1)[-1]  # 'anthropic/claude-x' → 'claude-x'
    m = _BRACKET_SUFFIX.sub('', m)  # 'glm-5.3[1m]' → 'glm-5.3'
    keys = [m]
    undated = _DATE_SUFFIX.sub('', m)
    if undated != m:
        keys.append(undated)
    return keys


def _find(models: dict, model: str) -> dict | None:
    lowered = {k.lower(): v for k, v in models.items()}
    for key in _model_keys(model):
        hit = models.get(key) or lowered.get(key.lower())
        if hit is not None:
            return hit
    return None


def _to_price(entry: dict, *, priced: bool) -> ModelPrice:
    rates = entry.get('cost') if priced else None
    tiers = (
        tuple(
            Tier(size=t['size'], rates={k: t[k] for k in _RATE_KEYS})
            for t in entry.get('tiers', [])
        )
        if rates
        else ()
    )
    return ModelPrice(rates=rates, tiers=tiers, context=entry.get('context'))


def provider_for_preset(preset_id: str | None) -> str | None:
    """The models.dev provider for a preset id (``None`` = custom)."""
    return PRESET_PROVIDERS.get(preset_id or '')


def price_for(provider: str | None, model: str | None) -> ModelPrice:
    """Rates + context window for ``model`` served by ``provider``.

    Rates come only from the run's own provider: the same model costs
    different amounts on different hosts, and a plan is free where
    pay-as-you-go is not. The context window is a property of the
    model, so an unknown provider (a custom profile) still gets one
    from any provider that lists the model.
    """
    if not model:
        return UNPRICED
    catalog = current()
    if provider and provider in catalog:
        hit = _find(catalog[provider], model)
        if hit is not None:
            return _to_price(hit, priced=True)
    for models in catalog.values():
        hit = _find(models, model)
        if hit is not None and hit.get('context'):
            return ModelPrice(rates=None, context=hit['context'])
    return UNPRICED


def cost_usd(
    price: ModelPrice,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    cache_write_1h_tokens: int = 0,
) -> float | None:
    """USD for one API call's token counts; ``None`` when unpriced.

    A context tier (long-context pricing) applies to the WHOLE call
    once the prompt — fresh input plus cache reads and writes — is
    over the tier's size, the way providers bill it.

    ``cache_write_1h_tokens`` is the part of ``cache_write_tokens``
    written with a one-hour TTL (``usage.cache_creation.
    ephemeral_1h_input_tokens``). Claude Code caches with that TTL, and
    Anthropic bills it at 2x input — models.dev's ``cache_write`` is the
    five-minute rate (1.25x), so it would under-price those writes.
    """
    if price.rates is None:
        return None
    rates = price.rates
    prompt = input_tokens + cache_read_tokens + cache_write_tokens
    for tier in price.tiers:
        if prompt > tier.size:
            rates = tier.rates
    one_hour = min(cache_write_1h_tokens, cache_write_tokens)
    return (
        input_tokens * rates['input']
        + output_tokens * rates['output']
        + cache_read_tokens * rates['cache_read']
        + (cache_write_tokens - one_hour) * rates['cache_write']
        + one_hour * rates['input'] * ONE_HOUR_CACHE_WRITE_MULTIPLIER
    ) / 1_000_000
