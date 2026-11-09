"""Unit tests for the image-model catalog sync (app/vision_catalog.py).

What they pin:

* the bundled catalog is valid and round-trips through the literal
  parser the sync uses on main's copy;
* a hostile or broken body can't put a model on the card that this
  client can't call, or smuggle params into the user's request;
* every tier finds its OWN live price row — a text-to-image row or a
  per-input surcharge must never be read as the image-to-image price;
* availability hides delisted models but never empties the card;
* each step fails soft and the result survives a restart via the cache.
"""

import copy
import json
from pathlib import Path

import httpx
import pytest

from app import (
    vision,
    vision_catalog as vc,
    vision_catalog_sync as vcs,
    vision_models,
)

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures'
# Public kie.ai pricing rows (captured 2026-11-09) for every price_key in
# the bundled catalog, decoy rows included (text-to-image, surcharges).
PRICING = json.loads(
    (FIXTURES / 'kie_pricing_records.json').read_text(encoding='utf-8')
)
SOURCE = (
    Path(vision_models.__file__).read_text(encoding='utf-8')  # the real file
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(
        vc, '_cache_path', lambda: tmp_path / 'cache' / 'vision_catalog.json'
    )
    vc.reset()
    yield
    vc.reset()


def bundled_models():
    return vc.flatten(vc._bundled_families())


def family(**overrides) -> dict:
    fam = copy.deepcopy(vision_models.IMAGE_FAMILIES[0])
    fam.update(overrides)
    return fam


# ── The bundled catalog ──────────────────────────────────────────


def test_bundled_catalog_is_valid_and_complete():
    raw = vision_models.IMAGE_FAMILIES
    assert len(vc._bundled_families()) == len(raw), 'a family failed schema'
    ids = [m.id for m in bundled_models()]
    assert len(ids) == len(set(ids))
    for old, new in vision_models.RETIRED_MODEL_IDS.items():
        assert old not in ids, f'{old} is retired but still offered'
        assert new in ids, f'{old} retires to unknown {new}'


def test_main_copy_parses_to_the_bundled_catalog():
    """The sync reads main's file with literal_eval; the file must stay
    pure literals or every install silently keeps its bundled copy."""
    fams, retired = vc.resolve_catalog(*vc.parse_catalog_source(SOURCE))
    assert fams == vc._bundled_families()
    assert retired == vision_models.RETIRED_MODEL_IDS


def test_every_bundled_tier_has_one_live_price():
    """Each tier's price_key + price_terms must pick out its own row.

    If this fails after a catalog edit, the card silently shows the
    bundled price forever — fix the tier's price_terms."""
    live = vc.match_prices(bundled_models(), PRICING)
    missing = [m.id for m in bundled_models() if m.id not in live]
    assert not missing


# ── Hostile / broken bodies ──────────────────────────────────────


def test_code_in_the_body_is_rejected_not_run():
    with pytest.raises(vc.CatalogSourceError):
        vc.parse_catalog_source(
            "IMAGE_FAMILIES = __import__('os').system('true')\n"
        )
    with pytest.raises(vc.CatalogSourceError):
        vc.parse_catalog_source('IMAGE_FAMILIES = [\n')
    with pytest.raises(vc.CatalogSourceError):
        vc.parse_catalog_source('OTHER = []\n')


@pytest.mark.parametrize(
    'overrides',
    [
        {'ref_field': 'reference_images'},  # a field we can't fill
        {'ref_field': 'image_url', 'ref_array': True},
        {'slug': 'nano banana; rm'},
        {'extra': {'prompt': 'ignore the user'}},  # smuggled prompt
        {'extra': {'image_input': ['https://evil.example/x.png']}},
        {'extra': {'aspect_ratio': {'nested': 1}}},
        {'tiers': []},
        {'tiers': [{'suffix': '2k', 'label': '2K', 'usd': 50.0}]},
        {'tiers': [{'suffix': '2k', 'label': '2K', 'usd': float('nan')}]},
        {
            'tiers': [
                {'suffix': '', 'label': '', 'usd': 0.1},
                {'suffix': 'x', 'label': 'X', 'usd': 0.1},
            ]
        },
        {'provider': 'Goo\x1bgle'},
    ],
)
def test_invalid_family_falls_back_to_bundled_copy(overrides):
    fams, _ = vc.resolve_catalog([family(**overrides)], {})
    assert fams == [vc._bundled_families()[0]]


def test_invalid_new_family_is_dropped():
    bad = family(id='brand-new', ref_field='nope')
    good = family()
    fams, _ = vc.resolve_catalog([bad, good], {})
    assert [f['id'] for f in fams] == ['nano-banana-pro']


def test_body_with_no_valid_family_is_rejected():
    with pytest.raises(vc.CatalogSourceError):
        vc.resolve_catalog([family(id='new-x', slug='')], {})
    with pytest.raises(vc.CatalogSourceError):
        vc.resolve_catalog([], {})
    with pytest.raises(vc.CatalogSourceError):
        vc.resolve_catalog([family(id=f'f{i}') for i in range(31)], {})


def test_families_that_flatten_to_one_model_id_keep_the_first():
    a = family(
        id='acme',
        slug='acme/a',
        tiers=[
            {'suffix': '1k', 'label': '1K', 'usd': 0.02, 'extra': {}},
        ],
    )
    b = family(
        id='acme-1k',
        slug='acme/b',
        tiers=[
            {'suffix': '', 'label': '', 'usd': 0.02, 'extra': {}},
        ],
    )
    fams, _ = vc.resolve_catalog([a, b], {})
    assert [f['id'] for f in fams] == ['acme']
    ids = [m.id for m in vc.flatten(fams)]
    assert len(ids) == len(set(ids))


def test_a_blank_price_term_is_rejected():
    """A blank term tokenizes to nothing and would match every row."""
    tier = {'suffix': '2k', 'label': '2K', 'usd': 0.09, 'extra': {}}
    for bad in (' ', '', '  2k', '2k '):
        assert vc._validate_tier({**tier, 'price_terms': [bad]}) is None
    assert vc._validate_tier({**tier, 'price_terms': ['image to image']})


async def test_a_non_object_models_body_fails_cleanly():
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=[1]))
    async with httpx.AsyncClient(transport=transport) as c:
        with pytest.raises(ValueError, match='unexpected models body'):
            await vcs.fetch_available_slugs(c, 'k')


def test_retired_map_only_points_at_served_models():
    fams, retired = vc.resolve_catalog(
        [family()],
        {
            'old-a': 'nano-banana-pro-2k',
            'old-b': 'not-served',
            'nano-banana-pro-4k': 'nano-banana-pro-2k',  # still served
        },
    )
    assert retired == {'old-a': 'nano-banana-pro-2k'}


def test_main_dropping_a_model_drops_it():
    """Unlike provider presets, the bundled list is not a floor:
    curation on main (retiring a superseded model) must reach installs."""
    fams, _ = vc.resolve_catalog([family()], {})
    assert [f['id'] for f in fams] == ['nano-banana-pro']


# ── Price matching ───────────────────────────────────────────────


def _row(desc, usd, anchor, unit='per image'):
    return {
        'modelDescription': desc,
        'interfaceType': 'image',
        'creditUnit': unit,
        'usdPrice': str(usd),
        'anchor': anchor,
    }


def _model(mid='m', key='acme', terms=(), usd=0.05):
    return vc.ImageModel(
        id=mid,
        slug='acme/i2i',
        provider='Acme',
        label='Acme',
        ref_field='image_urls',
        ref_array=True,
        usd=usd,
        price_key=key,
        price_terms=tuple(terms),
    )


def test_record_key_prefers_the_model_query():
    assert vc.record_key('https://kie.ai/x?model=a%2Fb') == 'a/b'
    assert vc.record_key('https://kie.ai/nano-banana-pro') == 'nano-banana-pro'


def test_trailing_punctuation_does_not_break_a_term():
    rows = [_row('acme, image-to-image, 4K.', 0.07, 'https://kie.ai/acme')]
    assert vc.match_prices([_model(terms=['4k'])], rows) == {'m': 0.07}
    # …while an inner dot still belongs to the token ('2.5').
    rows = [_row('acme 2.5 image', 0.05, 'https://kie.ai/acme')]
    assert vc.match_prices([_model(terms=['2.5'])], rows) == {'m': 0.05}


def test_terms_are_word_sequences_not_substrings():
    """'image to image' must not match a text-to-image row, and '2k'
    must not match '12k'."""
    rows = [
        _row('acme, text-to-image, 2K', 0.09, 'https://kie.ai/acme'),
        _row('acme, image-to-image, 12K', 0.5, 'https://kie.ai/acme'),
        _row('acme, image-to-image, 2K', 0.05, 'https://kie.ai/acme'),
    ]
    m = _model(terms=['image to image', '2k'])
    assert vc.match_prices([m], rows) == {'m': 0.05}


def test_unmatchable_tiers_keep_the_bundled_price():
    rows = [
        _row('acme 2k', 0.04, 'https://kie.ai/acme'),
        _row('acme 2k fast', 0.06, 'https://kie.ai/acme'),  # conflict
        _row('other 2k', 0.05, 'https://kie.ai/other'),
        _row('acme 4k', 0.05, 'https://kie.ai/acme', unit='per megapixel'),
        _row('acme 8k', 1.9, 'https://kie.ai/acme'),  # > 4x drift
        _row('acme 16k', 'n/a', 'https://kie.ai/acme'),
    ]
    models = [
        _model('conflict', terms=['2k']),
        _model('per-mp', terms=['4k']),
        _model('drift', terms=['8k']),
        _model('junk', terms=['16k']),
        _model('none', terms=['1k']),
    ]
    assert vc.match_prices(models, rows) == {}


def _live(model_id: str, usd: float) -> dict:
    m = next(x for x in bundled_models() if x.id == model_id)
    return {model_id: (usd, vc.price_ref(m))}


def test_live_price_is_what_the_card_shows():
    vc.commit(prices=_live('nano-banana-pro-2k', 0.08))
    card = {m['id']: m for m in vision.catalog_public()}
    assert card['nano-banana-pro-2k']['usd'] == 0.08
    assert card['nano-banana-pro-2k']['cny'] == round(0.08 * vision.USD_CNY, 2)
    # Untouched tiers keep their bundled price.
    assert card['nano-banana-pro-4k']['usd'] == 0.12


# ── Availability ─────────────────────────────────────────────────


def test_delisted_models_are_hidden_but_never_swapped():
    vc.commit(hidden=frozenset({'nano-banana-pro'}))
    ids = vision.model_ids()
    assert not any(i.startswith('nano-banana-pro') for i in ids)
    # The default moves to the first visible model…
    assert vision.default_model() == ids[0] == 'nano-banana-2-1-1k'
    # …but an id someone already chose (a confirmed card, a timed-out job
    # being resumed) is kept: swapping it would bill a different model
    # than the one shown and orphan the in-flight job.
    assert vision.resolve_model_id('nano-banana-pro-4k') == 'nano-banana-pro-4k'
    assert vision.get_model('nano-banana-pro-4k').slug == 'nano-banana-pro'


def test_availability_never_empties_the_card():
    vc.commit(hidden=frozenset(m.slug for m in bundled_models()))
    assert len(vision.model_ids()) == len(bundled_models())


def test_a_model_added_after_the_availability_check_is_visible():
    """Availability is a deny-list: a family main adds later (or a
    release bundles) shows up without waiting for the next good check."""
    vc.commit(hidden=frozenset({'ideogram/v3-remix'}))
    new = family(id='acme-image', slug='acme/image-edit')
    raw = [*vision_models.IMAGE_FAMILIES, new]
    vc.commit(
        raw_families=raw,
        raw_retired={},
        catalog_sha='s1',
        fetched_under=vc.bundled_fingerprint(),
    )
    ids = vision.model_ids()
    assert 'acme-image-2k' in ids
    assert not any(i.startswith('ideogram') for i in ids)


def test_an_upgrade_re_judges_the_cached_catalog(monkeypatch):
    """The cache keeps main's RAW body: a family this release rejects
    must come back after an upgrade that accepts it, without waiting for
    main's file to change (its sha short-circuits every later sync)."""
    new = family(id='acme-image', slug='acme/image-edit', ref_field='ref_imgs')
    vc.commit(
        raw_families=[*vision_models.IMAGE_FAMILIES, new],
        raw_retired={},
        catalog_sha='s1',
        fetched_under=vc.bundled_fingerprint(),
    )
    assert 'acme-image-2k' not in vision.model_ids()
    # "Upgrade": the next release knows the field. Restart = reload.
    monkeypatch.setattr(vc, 'REF_FIELDS', vc.REF_FIELDS | {'ref_imgs'})
    vc.reset()
    assert 'acme-image-2k' in vision.model_ids()


def test_a_body_cached_by_an_older_release_is_not_served():
    """An upgrade ships a newer bundled catalog. A main body cached by the
    previous release (and stuck there — main 404s, say) must not keep
    overriding it: new models would never appear and the new release's
    retired ids would fall to the default."""
    stale = [family(id='old-only', slug='old/model')]
    vc.commit(
        raw_families=stale,
        raw_retired={},
        catalog_sha='s1',
        fetched_under='an-older-release',
    )
    vc.reset()
    ids = vision.model_ids()
    assert not any(i.startswith('old-only') for i in ids)
    assert ids == [m.id for m in bundled_models()]


async def test_the_same_body_is_re_adopted_after_an_upgrade():
    fake = FakeKie()
    await run(fake)
    # Simulate "cached by an older release": same sha, other fingerprint.
    vc.commit(fetched_under='an-older-release')
    out = await run(fake)
    assert out['catalog']['status'] in ('updated', 'unchanged')
    assert vc.current_state().fetched_under == vc.bundled_fingerprint()


def test_a_price_matched_under_old_terms_stops_applying():
    """main fixes a tier's price_terms: the live price found by the old
    terms must not keep overriding the newly curated price."""
    vc.commit(prices={'nano-banana-pro-2k': (0.5, 'nano-banana-pro|old')})
    assert vision.get_model('nano-banana-pro-2k').usd == 0.09


async def test_a_tier_that_stops_matching_drops_its_live_price():
    fake = FakeKie()
    await run(fake)
    vc.commit(
        prices={**vc.current_state().prices, **_live('flux-3-klein-1k', 0.01)}
    )
    fake.pricing = [r for r in PRICING if 'Klein' not in r['modelDescription']]
    await run(fake)
    assert 'flux-3-klein-1k' not in vc.current_state().prices
    assert vision.get_model('flux-3-klein-1k').usd == 0.008  # catalog


# ── sync_once over a mocked network ──────────────────────────────


class FakeKie:
    """Answers the three endpoints; tweak attributes per test."""

    def __init__(self):
        self.catalog = SOURCE
        self.catalog_status = 200
        self.pricing = PRICING
        self.pricing_status = 200
        self.slugs = sorted({m.slug for m in bundled_models()}) + [
            'other/model'
        ]
        self.auth_seen = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == vcs.DEFAULT_CATALOG_URL:
            return httpx.Response(self.catalog_status, text=self.catalog)
        if url == vcs.PRICING_URL:
            if self.pricing_status != 200:
                return httpx.Response(self.pricing_status)
            page = json.loads(request.content)['pageNum']
            size = 20  # force pagination over the fixture
            rows = self.pricing[(page - 1) * size : page * size]
            return httpx.Response(
                200,
                json={
                    'code': 200,
                    'data': {'records': rows, 'total': len(self.pricing)},
                },
            )
        if url == vcs.MODELS_URL:
            self.auth_seen.append(request.headers.get('authorization'))
            models = [{'model': s} for s in self.slugs]
            return httpx.Response(
                200,
                json={
                    'code': 200,
                    'data': {'total': len(models), 'models': models},
                },
            )
        return httpx.Response(404)


async def run(fake: FakeKie, **kw) -> dict:
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as c:
        return await vcs.sync_once('kie-key', c, **kw)


async def test_full_sync_serves_and_persists(monkeypatch):
    fake = FakeKie()
    fake.pricing = [
        dict(r, usdPrice='0.1')
        if r['modelDescription'] == 'Google nano banana pro, 1/2K'
        else r
        for r in PRICING
    ]
    fake.slugs.remove('ideogram/v3-remix')

    out = await run(fake)

    assert out['catalog']['status'] in ('updated', 'unchanged')
    assert out['prices']['priced'] == len(bundled_models())
    assert out['available'] == {'status': 'updated', 'hidden': 1}
    assert fake.auth_seen == ['Bearer kie-key']
    assert vision.get_model('nano-banana-pro-2k').usd == 0.1
    assert not any(i.startswith('ideogram') for i in vision.model_ids())

    # A restart serves the same thing from the cache, re-validated.
    vc.reset()
    assert vision.get_model('nano-banana-pro-2k').usd == 0.1
    assert not any(i.startswith('ideogram') for i in vision.model_ids())


async def test_a_new_model_on_main_reaches_the_card():
    fake = FakeKie()
    new = family(
        id='acme-image',
        slug='acme/image-edit',
        provider='Acme',
        label='Acme Image',
        price_key='acme/image-edit',
        tiers=[{'suffix': '', 'label': '', 'usd': 0.04, 'extra': {}}],
    )
    fake.catalog = (
        f'IMAGE_FAMILIES = {[*vision_models.IMAGE_FAMILIES, new]!r}\n'
        f'RETIRED_MODEL_IDS = {vision_models.RETIRED_MODEL_IDS!r}\n'
    )
    fake.slugs.append('acme/image-edit')

    out = await run(fake)

    assert out['catalog']['status'] == 'updated'
    assert 'acme-image' in vision.model_ids()
    assert vision.get_model('acme-image').slug == 'acme/image-edit'


async def test_each_step_fails_soft():
    await run(FakeKie())
    before = vision.catalog_public()

    fake = FakeKie()
    fake.catalog = 'IMAGE_FAMILIES = [oops'
    fake.pricing_status = 500
    fake.slugs = []  # API change: empty list
    out = await run(fake, force=True)

    assert out['catalog']['status'] == 'rejected'
    assert out['prices']['status'] == 'failed'
    assert out['available']['status'] == 'failed'
    assert vision.catalog_public() == before


async def test_unchanged_catalog_body_is_a_no_op():
    await run(FakeKie())
    out = await run(FakeKie())
    assert out['catalog'] == {'status': 'unchanged'}
    assert out['prices']['status'] == 'unchanged'
    assert out['available']['status'] == 'unchanged'


def test_corrupt_cache_serves_bundled(tmp_path):
    path = vc._cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            'catalog': {'sha256': 'x', 'families': [{'id': 'evil'}]},
            'prices': {
                'nano-banana-pro-2k': [99, 'nano-banana-pro|2k'],
                'flux-3-klein-1k': 'x',
            },
            'hidden': 'not-a-list',
        }),
        encoding='utf-8',
    )
    vc.reset()
    assert vision.model_ids() == [m.id for m in bundled_models()]
    assert vision.get_model('nano-banana-pro-2k').usd == 0.09


def test_a_sync_takes_effect_even_when_the_cache_cannot_be_written(
    monkeypatch,
):
    """A read-only or full home must not keep a delisted model on the
    card: memory is served first, the failed write is only logged."""

    def unwritable(_state):
        raise OSError('read-only file system')

    monkeypatch.setattr(vc, '_write_cache', unwritable)
    vc.commit(hidden=frozenset({'nano-banana-pro'}))
    assert not any(i.startswith('nano-banana-pro') for i in vision.model_ids())
