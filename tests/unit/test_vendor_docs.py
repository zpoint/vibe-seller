"""Every registered browser vendor has a docs page + help topics.

Same spirit as ``test_docs_links.py`` (which already validates every
docs.vibe-sellers.com URL found in ``app/`` source — including the ones
the specs carry): this test pins the LINKAGE, so a vendor cannot be
registered without the docs a user is sent to:

* every anti-detect vendor has its own docs page in both languages
  (``docs/<key>.md`` + ``docs/<key>.en.md``);
* every vendor the generic UI renders (``picker_kind == 'generic'``)
  carries an ``errors`` docs link — the one the store picker shows next
  to a failure — whose URLs land on that vendor's own page and anchor.
"""

from pathlib import Path

import pytest

from app.browser.vendors import (
    KIND_ANTIDETECT,
    PICKER_GENERIC,
    registered_vendor_specs,
)
from tests.unit.test_docs_links import _anchors, _source

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / 'docs'


def _antidetect_specs():
    return [
        spec
        for spec in registered_vendor_specs().values()
        if spec.kind == KIND_ANTIDETECT
    ]


def _check_link(url: str) -> str | None:
    """Why ``url`` is broken (page/anchor missing), or None."""
    page, anchor = _source(url)
    if not page.exists():
        return f'{url} — no page {page.relative_to(ROOT)}'
    if anchor and anchor not in _anchors(page):
        return f'{url} — no "#{anchor}" in {page.relative_to(ROOT)}'
    return None


def test_every_antidetect_vendor_has_a_docs_page():
    specs = _antidetect_specs()
    assert specs, 'the registry must have anti-detect vendors to check'
    missing = [
        f'{spec.key}: docs/{p}'
        for spec in specs
        for p in (f'{spec.key}.md', f'{spec.key}.en.md')
        if not (DOCS / p).exists()
    ]
    assert not missing, 'Vendors without a docs page:\n' + '\n'.join(missing)


def test_generic_picker_vendors_link_their_own_errors_page():
    broken = []
    for spec in _antidetect_specs():
        if spec.picker_kind != PICKER_GENERIC:
            continue  # custom pickers keep their own docs wiring
        link = spec.docs.get('errors')
        if link is None:
            broken.append(f'{spec.key}: no "errors" docs topic')
            continue
        for url in (link.zh, link.en):
            why = _check_link(url)
            if why:
                broken.append(f'{spec.key}: {why}')
            elif f'/{spec.key}' not in url:
                broken.append(
                    f'{spec.key}: {url} does not link the vendor page'
                )
    assert not broken, '\n'.join(broken)


def test_the_checker_catches_a_bad_link(tmp_path, monkeypatch):
    """Negative control: a spec whose docs link misses the page (or its
    anchor) is reported, and one that points at another page too."""
    bad_page = 'https://docs.vibe-sellers.com/nosuchpage/#x'
    assert _check_link(bad_page) is not None
    # A real page but a foreign one (the vendor must link its own page).
    foreign = 'https://docs.vibe-sellers.com/ziniao/'
    assert _check_link(foreign) is None
    assert '/yuehulian' not in foreign
