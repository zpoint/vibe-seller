"""The curated image-model catalog, as literal data.

This file is the ONE source of truth for which kie.ai image models the
confirm card offers. It is literal data on purpose: the
``vision_catalog`` sync job fetches this file from ``main`` and parses
it with :func:`ast.literal_eval` (never imports or executes it), so a
running install picks up a newly added model without a release. Keep it
to plain literals — no names, calls, or comprehensions.

Curation rules (do not pull kie.ai's whole list in):

* image-to-image / editing models only — the tool always sends the
  user's reference images;
* the current flagship of each major vendor plus at most one cheaper
  lane, and no option a sibling strictly dominates (same vendor, newer
  and no more expensive);
* a flat per-image price on kie.ai — token-priced models (GPT Image 2.6
  Sunburst/Flare) are left out because the card must show a price
  before the user confirms.

Field contract (enforced by ``app.vision_catalog.validate``):

* ``id`` — OUR stable family id; a tier's model id is
  ``f'{id}-{suffix}'`` (or just ``id`` for a lone tier with an empty
  suffix). Ids are a contract with agents and saved prompts: when one is
  dropped, map it in ``RETIRED_MODEL_IDS``.
* ``slug`` — kie.ai's exact ``model`` string for ``jobs/createTask``.
* ``ref_field`` / ``ref_array`` — name and cardinality of the reference
  image input on that model's schema.
* ``extra`` (family) + tier ``extra`` — static ``input`` params, merged
  tier-over-family. Fill every param the model's schema marks required.
* ``usd`` — the bundled per-image price; the live price from kie.ai's
  public pricing API replaces it at serve time when it can be matched.
* ``price_key`` (optional, defaults to ``slug``) + tier ``price_terms``
  — how to find this tier's row in that pricing API. ``price_key`` is
  the ``model=`` query value of the row's ``anchor`` URL, or the URL's
  path when there is no query. Each term is a word sequence that must
  appear in the row's ``modelDescription``.

Slugs, input params and prices verified against docs.kie.ai and the
kie.ai pricing API on 2026-11-09. The FIRST tier of the FIRST family is
the default model.
"""

IMAGE_FAMILIES = [
    {
        'id': 'nano-banana-pro',
        'slug': 'nano-banana-pro',
        'provider': 'Google',
        'label': 'Nano Banana Pro',
        'ref_field': 'image_input',
        'ref_array': True,
        'extra': {'aspect_ratio': '1:1', 'output_format': 'png'},
        'tiers': [
            {
                'suffix': '2k',
                'label': '2K',
                'usd': 0.09,
                'extra': {'resolution': '2K'},
                'price_terms': ['2k'],
            },
            {
                'suffix': '4k',
                'label': '4K',
                'usd': 0.12,
                'extra': {'resolution': '4K'},
                'price_terms': ['4k'],
            },
        ],
    },
    {
        'id': 'nano-banana-2-1',
        'slug': 'nano-banana-2-1',
        'provider': 'Google',
        'label': 'Nano Banana 2.1',
        'ref_field': 'image_input',
        'ref_array': True,
        'extra': {'aspect_ratio': '1:1', 'output_format': 'png'},
        'tiers': [
            {
                'suffix': '1k',
                'label': '1K',
                'usd': 0.02,
                'extra': {'resolution': '1K'},
                'price_terms': ['1k'],
            },
            {
                'suffix': '2k',
                'label': '2K',
                'usd': 0.03,
                'extra': {'resolution': '2K'},
                'price_terms': ['2k'],
            },
            {
                'suffix': '4k',
                'label': '4K',
                'usd': 0.045,
                'extra': {'resolution': '4K'},
                'price_terms': ['4k'],
            },
        ],
    },
    {
        'id': 'gpt-image-2-5',
        'slug': 'gpt-image-2-5-flare-image-to-image',
        'provider': 'OpenAI',
        'label': 'GPT Image 2.5',
        'ref_field': 'input_urls',
        'ref_array': True,
        'extra': {'aspect_ratio': '1:1'},
        'price_key': 'gpt-image-2-5',
        'tiers': [
            {
                'suffix': '1k',
                'label': '1K',
                'usd': 0.03,
                'extra': {'resolution': '1K'},
                'price_terms': ['flare', 'image to image', '1k'],
            },
            {
                'suffix': '2k',
                'label': '2K',
                'usd': 0.05,
                'extra': {'resolution': '2K'},
                'price_terms': ['flare', 'image to image', '2k'],
            },
            {
                'suffix': '4k',
                'label': '4K',
                'usd': 0.08,
                'extra': {'resolution': '4K'},
                'price_terms': ['flare', 'image to image', '4k'],
            },
        ],
    },
    {
        'id': 'gpt-image-2-6-economy',
        'slug': 'gpt-image-2-6/economy-image-to-image',
        'provider': 'OpenAI',
        'label': 'GPT Image 2.6 Economy',
        'ref_field': 'input_urls',
        'ref_array': True,
        'extra': {'aspect_ratio': '1:1', 'output_format': 'png'},
        'tiers': [
            {
                'suffix': '',
                'label': '1K',
                'usd': 0.025,
                'extra': {},
                'price_terms': ['image to image', '1k'],
            },
        ],
    },
    {
        'id': 'seedream-5-pro',
        'slug': 'seedream/5-pro-image-to-image',
        'provider': 'ByteDance',
        'label': 'Seedream 5 Pro',
        'ref_field': 'image_urls',
        'ref_array': True,
        'extra': {'aspect_ratio': '1:1', 'output_format': 'png'},
        # The i2i output rows carry no ``model=`` query; the per-input
        # row that does is a reference-image surcharge, not the price.
        'price_key': 'seedream-5-0-pro',
        'tiers': [
            {
                # kie's ``quality``: basic renders 1K, high renders 2K.
                'suffix': 'basic',
                'label': '1K',
                'usd': 0.035,
                'extra': {'quality': 'basic'},
                'price_terms': ['image to image', '1k'],
            },
            {
                'suffix': 'high',
                'label': '2K',
                'usd': 0.07,
                'extra': {'quality': 'high'},
                'price_terms': ['image to image', '2k'],
            },
        ],
    },
    {
        'id': 'seedream-5-flash',
        'slug': 'seedream/5-flash-image-to-image',
        'provider': 'ByteDance',
        'label': 'Seedream 5 Flash',
        'ref_field': 'image_urls',
        'ref_array': True,
        'extra': {'aspect_ratio': '1:1', 'output_format': 'png'},
        'tiers': [
            {
                'suffix': '2k',
                'label': '2K',
                'usd': 0.0162,
                'extra': {'size': '2K'},
                'price_terms': ['image to image', '2k'],
            },
        ],
    },
    {
        'id': 'flux-2-pro',
        'slug': 'flux-2/pro-image-to-image',
        'provider': 'Black Forest Labs',
        'label': 'Flux-2 Pro',
        'ref_field': 'input_urls',
        'ref_array': True,
        'extra': {'aspect_ratio': '1:1'},
        'tiers': [
            {
                'suffix': '1k',
                'label': '1K',
                'usd': 0.025,
                'extra': {'resolution': '1K'},
                'price_terms': ['1k'],
            },
            {
                'suffix': '2k',
                'label': '2K',
                'usd': 0.035,
                'extra': {'resolution': '2K'},
                'price_terms': ['2k'],
            },
        ],
    },
    {
        'id': 'flux-3-klein',
        'slug': 'flux-3/klein-edit-image',
        'provider': 'Black Forest Labs',
        'label': 'FLUX 3 Klein',
        'ref_field': 'image_urls',
        'ref_array': True,
        'extra': {'aspect_ratio': '1:1', 'output_format': 'png'},
        'tiers': [
            {
                'suffix': '1k',
                'label': '1K',
                'usd': 0.008,
                'extra': {'resolution': '1K'},
                'price_terms': ['1k'],
            },
            {
                'suffix': '2k',
                'label': '2K',
                'usd': 0.0125,
                'extra': {'resolution': '2K'},
                'price_terms': ['2k'],
            },
            {
                'suffix': '4k',
                'label': '4K',
                'usd': 0.0325,
                'extra': {'resolution': '4K'},
                'price_terms': ['4k'],
            },
        ],
    },
    {
        'id': 'qwen3-pro',
        'slug': 'qwen3/pro-image-to-image',
        'provider': 'Qwen',
        'label': 'Qwen Image 3.0 Pro',
        'ref_field': 'image_urls',
        'ref_array': True,
        # ``image_size`` is the aspect ratio here (defaults to 16:9).
        'extra': {'image_size': '1:1', 'output_format': 'png'},
        'tiers': [
            {
                'suffix': '1k',
                'label': '1K',
                'usd': 0.032,
                'extra': {'resolution': '1K'},
                'price_terms': ['output', '1k'],
            },
            {
                'suffix': '2k',
                'label': '2K',
                'usd': 0.06,
                'extra': {'resolution': '2K'},
                'price_terms': ['output', '2k'],
            },
        ],
    },
    {
        'id': 'grok-imagine-2',
        'slug': 'grok-imagine-image-2-0/image-edit',
        'provider': 'xAI',
        'label': 'Grok Imagine Image 2.0',
        'ref_field': 'image_urls',
        'ref_array': True,
        'extra': {'aspect_ratio': '1:1'},
        'tiers': [
            {
                'suffix': '',
                'label': '',
                'usd': 0.02,
                'extra': {},
                'price_terms': ['image edit'],
            },
        ],
    },
    {
        'id': 'ideogram-v3-remix',
        'slug': 'ideogram/v3-remix',
        'provider': 'Ideogram',
        'label': 'Ideogram V3 Remix',
        'ref_field': 'image_url',
        'ref_array': False,
        'extra': {},
        'tiers': [
            {
                'suffix': 'turbo',
                'label': 'Turbo',
                'usd': 0.0175,
                'extra': {'rendering_speed': 'TURBO'},
                'price_terms': ['turbo'],
            },
            {
                'suffix': 'balanced',
                'label': 'Balanced',
                'usd': 0.035,
                'extra': {'rendering_speed': 'BALANCED'},
                'price_terms': ['balanced'],
            },
            {
                'suffix': 'quality',
                'label': 'Quality',
                'usd': 0.05,
                'extra': {'rendering_speed': 'QUALITY'},
                'price_terms': ['quality'],
            },
        ],
    },
]

# Model ids that left the catalog -> the id that replaces them. An agent,
# a skill, or a saved schedule prompt may still name an old id; it keeps
# resolving to its successor instead of silently falling to the default.
RETIRED_MODEL_IDS = {
    'nano-banana-2-1k': 'nano-banana-2-1-1k',
    'nano-banana-2-2k': 'nano-banana-2-1-2k',
    'nano-banana-2-4k': 'nano-banana-2-1-4k',
    'nano-banana-edit': 'nano-banana-2-1-1k',
    'gpt-image-2-1k': 'gpt-image-2-5-1k',
    'gpt-image-2-2k': 'gpt-image-2-5-2k',
    'gpt-image-2-4k': 'gpt-image-2-5-4k',
    'gpt-image-1.5-medium': 'gpt-image-2-6-economy',
    'seedream-4.5': 'seedream-5-flash-2k',
    'flux-2-flex-1k': 'flux-2-pro-1k',
    'flux-2-flex-2k': 'flux-2-pro-2k',
    'qwen-image-edit': 'qwen3-pro-1k',
}
