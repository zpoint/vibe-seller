"""Invariants for the seller-central browser helpers (``bh_*.py``).

Two bug classes these pin, both observed live on a real store:

* **The console language follows the SESSION, not the subdomain.** A
  Chinese seller-central session renders 提交商品 / （自动检测）/
  下载处理一览 where the English docs say "Submit products" /
  "(automatically detected)" / "Download Processing Summary". An
  English-only regex over ``innerText`` / a kat-button ``label`` then
  reports "not detected" on a page a human submits in one click — which
  is exactly what happened: the upload helper reported
  ``detected: false`` on every attempt, the agent concluded the upload
  entry point was broken, hand-drove onto the wrong page, and blamed a
  leftover banner. So: a helper may not gate a decision on an
  English-only UI-text match. Prefer structure (a button flipping
  ``disabled`` → enabled); where a label is unavoidable, carry the
  non-Latin variants.

* **The URL subdomain does not decide the marketplace.** A ``.ae`` page
  happily renders under an SA session, so an AE-stamped flat file can
  land in SA's upload history — and did. Both sides are machine-readable
  ids (the file's ``primaryMarketplaceId`` stamp, the page's ``ue_mid``),
  so the upload helper must compare them rather than trust the host.

The scan is deliberately source-level: these scripts are fed to
``browser-use`` on stdin and execute browser globals at import, so they
cannot be imported in a unit test.
"""

from pathlib import Path
import re

import pytest

pytestmark = pytest.mark.unit

_SCRIPTS = Path(__file__).resolve().parents[2] / 'app' / 'skills_v2'
_HELPERS = sorted(_SCRIPTS.glob('*/scripts/bh_*.py'))

# A JS regex literal used as `/…/i.test(<expr>)` inside an embedded
# snippet, with the tested expression captured — what a pattern is
# matched AGAINST decides whether it is UI copy at all.
_JS_TEST = re.compile(
    r'/(?P<pat>(?:\\.|[^/\\\n])+)/i?\.test\((?P<expr>[A-Za-z_$][\w$]*)'
)
# Expressions that hold an Amazon API TOKEN, not user-facing copy: an
# `icon="download"` attribute reads the same in every console language,
# so matching English there is correct. The name is the contract — a
# helper that wants this exemption must read into one of these, which
# makes the claim visible in review instead of implied by a regex.
_API_TOKEN_EXPRS = {'apiToken'}
# A `_click_text('…')` pattern argument (the download helper's clicker).
_CLICK_TEXT = re.compile(r"_click_text\(\s*'([^']+)'")

# Patterns that are NOT user-visible copy, so they need no translation:
#   ^Amazon\.      — the store-picker labels ("Amazon.sa"), Latin in
#                    every console language because they are the brand.
#   ROW|TR         — tag names, walking up to a row element.
#   {pattern}      — an f-string placeholder; the values that fill it
#                    are asserted separately below.
_STRUCTURAL = {
    r'^Amazon\\.',
    'ROW|TR',
    '{pattern}',
}

_NON_LATIN = re.compile(r'[　-鿿؀-ۿ]')


# Adjacent Python string literals implicitly concatenate, so one JS
# snippet is usually split across several source lines — and a regex
# literal can straddle the seam (`… /submit products/i` on one line,
# `.test(e.innerText…)` on the next). Stitch the seams shut before
# scanning, or the scan misses exactly the matches that broke live.
_SEAM = re.compile(r'[\'"]\s*\n\s*[fr]?[\'"]')


def _ui_text_patterns(src):
    """Yield every pattern the source matches page text against."""
    for m in _JS_TEST.finditer(_SEAM.sub('', src)):
        if m.group('expr') in _API_TOKEN_EXPRS:
            continue
        yield m.group('pat')
    for m in _CLICK_TEXT.finditer(src):
        yield m.group(1)


def test_helpers_exist():
    assert _HELPERS, 'no bh_*.py helpers found — did the tree move?'


@pytest.mark.parametrize('path', _HELPERS, ids=lambda p: p.name)
def test_no_english_only_ui_text_match(path):
    src = path.read_text(encoding='utf-8')
    offenders = [
        pat
        for pat in _ui_text_patterns(src)
        if pat not in _STRUCTURAL
        and re.search(r'[a-z]', pat, re.I)
        and not _NON_LATIN.search(pat)
    ]
    assert not offenders, (
        f'{path.name} matches seller-central UI text in English only: '
        f'{offenders}. The console language follows the SESSION — a ZH '
        'session renders 提交商品 / 下载处理一览 and this match silently '
        'misses. Read the state structurally, or add the ZH/AR variants.'
    )


def test_click_text_patterns_are_multilingual():
    """The download helper clicks by label — every label needs ZH."""
    src = (
        _SCRIPTS / 'amazon-listing' / 'scripts' / 'bh_download_template.py'
    ).read_text(encoding='utf-8')
    pats = _CLICK_TEXT.findall(src)
    assert pats, '_click_text call sites vanished — update this test'
    for pat in pats:
        assert _NON_LATIN.search(pat), f'{pat!r} has no ZH variant'


def test_upload_helper_compares_marketplace_ids():
    """The upload refuses to land a file on the wrong storefront.

    The check must use the two machine-readable ids, never the host: a
    `.ae` URL under an SA session is how an AE relist ended up in SA's
    upload history.
    """
    src = (
        _SCRIPTS / 'amazon-listing' / 'scripts' / 'bh_upload_flatfile.py'
    ).read_text(encoding='utf-8')
    assert 'primaryMarketplaceId' in src, 'file stamp never read'
    assert 'ue_mid' in src, 'live marketplace never read'
    # The guard has to fire BEFORE the file is staged/submitted.
    guard = src.index('MARKETPLACE MISMATCH')
    stage = src.index('DOM.setFileInputFiles')
    assert guard < stage, 'marketplace check runs after staging'
    # ...and it must fail CLOSED. If either id is unreadable the helper
    # cannot prove where the feed lands, and "probably the right one" is
    # precisely what put a file in the wrong marketplace's history.
    for missing in (
        "if not out['file_marketplace']:",
        "if not out['marketplace']:",
    ):
        assert missing in src, f'no refusal branch for {missing}'
        assert src.index(missing) < stage, 'proof check runs after staging'


def test_upload_helper_reads_submit_state_structurally():
    """Readiness = the Submit button enabling, not an English string."""
    src = (
        _SCRIPTS / 'amazon-listing' / 'scripts' / 'bh_upload_flatfile.py'
    ).read_text(encoding='utf-8')
    assert 'automatically detected' not in src, (
        'readiness is back on an English-only page string'
    )
    assert "hasAttribute('disabled')" in src
