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

import os
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


# Helpers whose every page carries its language IN THE URL, so English
# copy is what renders whatever the session prefers. noon's portals are
# addressed as `…noon.partners/en/…` (login included); seller central is
# not, which is what this test exists for.
_URL_PINNED_LANGUAGE = {'bh_noon_catalog.py', 'bh_noon_signin.py'}


def test_url_pinned_helpers_really_pin_the_language():
    for name in _URL_PINNED_LANGUAGE:
        (path,) = [p for p in _HELPERS if p.name == name]
        urls = re.findall(r'https://[^\s\'"]+', path.read_text('utf-8'))
        assert urls and all('/en/' in u for u in urls), (
            f'{name} loads a page without /en/ in its URL'
        )


@pytest.mark.parametrize('path', _HELPERS, ids=lambda p: p.name)
def test_no_english_only_ui_text_match(path):
    if path.name in _URL_PINNED_LANGUAGE:
        pytest.skip('language is pinned by the /en/ URL')
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


def _disabled_rows_fn():
    """Exec just `_disabled_rows` (and its token set) out of the helper."""
    src = (
        _SCRIPTS / 'amazon-listing' / 'scripts' / 'bh_upload_flatfile.py'
    ).read_text(encoding='utf-8')
    start = src.index('_DISABLED_TOKENS = ')
    end = src.index('\n\n\n_bad = ', start)
    ns = {}
    exec(src[start:end], ns)  # noqa: S102 - our own source, under test
    return ns['_disabled_rows'], src


@pytest.mark.parametrize(
    'head, rows, bad',
    [
        (
            ['item_sku', 'update_delete'],
            [['A-1', 'partialupdate'], ['A-2', ''], ['A-3', 'Delete']],
            [('A-3', 'delete')],
        ),
        (
            ['contribution_sku#1.value', '::record_action'],
            [
                ['B-1', 'Create or Replace (Full Update)'],
                ['B-2', 'Edit (Partial Update)'],
            ],
            [('B-1', 'create or replace (full update)')],
        ),
        (['item_sku', 'update_delete'], [['C-1', 'partialupdate']], []),
    ],
)
def test_upload_refuses_full_update_and_delete_rows(tmp_path, head, rows, bad):
    """A hand-written upload file never went through `fill`.

    So the upload helper reads the operation column itself and refuses a
    full-update or delete row before anything is staged.
    """
    fn, src = _disabled_rows_fn()
    f = tmp_path / 'up.txt'
    lines = [['settings=x'], ['label'], head] + rows
    f.write_text('\n'.join('\t'.join(r) for r in lines), encoding='utf-8')
    assert fn(str(f)) == bad
    assert src.index('REFUSED: the upload file') < src.index(
        'DOM.setFileInputFiles'
    )


def test_upload_helper_checks_the_INTENDED_marketplace_too():
    """Two ids agreeing is not enough — they can agree on the wrong one.

    Observed live: an AE rebuild was uploaded on `sellercentral.amazon.ae`
    while the session was still on SA, with an SA-stamped template. File
    stamp == live `ue_mid`, so a two-way check passed, and the feed
    listed on SA. The marketplace the CALLER meant is the third party,
    and the host is how they said it.
    """
    src = (
        _SCRIPTS / 'amazon-listing' / 'scripts' / 'bh_upload_flatfile.py'
    ).read_text(encoding='utf-8')
    assert '_intended_marketplace' in src, 'intent is never derived'
    assert 'SC_MARKETPLACE' in src, 'no way to state the intent outright'
    # All three must be compared together, before anything is staged.
    check = src.index('_seen = {')
    assert src.index('MARKETPLACE MISMATCH') > check
    assert check < src.index('DOM.setFileInputFiles')
    for part in (
        "'the file is stamped for'",
        "'this session is on'",
    ):
        assert part in src, f'{part} missing from the comparison'


def test_upload_helper_reads_submit_state_structurally():
    """Readiness = the Submit button enabling, not an English string."""
    src = (
        _SCRIPTS / 'amazon-listing' / 'scripts' / 'bh_upload_flatfile.py'
    ).read_text(encoding='utf-8')
    assert 'automatically detected' not in src, (
        'readiness is back on an English-only page string'
    )
    assert "hasAttribute('disabled')" in src


def test_upload_helper_names_amazons_bad_json_alert_as_a_session_problem():
    """Amazon's BAD_JSON alert is not a slow page or a bad file.

    Observed live: Submit never enabled, the alert read BAD_JSON_0, the
    helper advised a longer wait, and the next load went to sign-in.
    The alert has to send the agent to sign in, not to edit the file.
    """
    src = (
        _SCRIPTS / 'amazon-listing' / 'scripts' / 'bh_upload_flatfile.py'
    ).read_text(encoding='utf-8')
    branch = src.index("'BAD_JSON' in a")
    assert branch < src.index('file staged but Submit never enabled')
    assert 'bh_signin.py' in src[branch : branch + 400]


def _marker_dirs_fn(path, marker_dir):
    """Exec just `_marker_dirs` out of a helper's source."""
    src = path.read_text(encoding='utf-8')
    start = src.index('def _marker_dirs():')
    end = src.index('\n\n\n', start)
    ns = {'os': os, 'MARKER_DIR': marker_dir}
    exec(src[start:end], ns)  # noqa: S102 - our own source, under test
    return ns['_marker_dirs']


@pytest.mark.parametrize(
    'name', ['bh_upload_flatfile.py', 'bh_download_template.py']
)
def test_gate_markers_always_reach_the_task_workspace(
    name, tmp_path, monkeypatch
):
    """A caller-supplied MARKER_DIR must not be able to blind the gate.

    Observed live: an agent passed its scratch dir as MARKER_DIR, all
    seven upload markers of a run landed in /tmp, the completion gate's
    "every uploaded batch is verdicted" check saw none of them, and the
    accepted-spec library never filed a spec. The workspace is derived
    the way the app derives it, and always written.
    """
    home = tmp_path / 'home'
    ws = home / 'tasks' / 'abc12345-0000-0000-0000-000000000000'
    ws.mkdir(parents=True)
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    monkeypatch.setenv('VIBE_HOME', str(home))
    monkeypatch.setenv('VIBE_TASK_ID', ws.name)
    path = _SCRIPTS / 'amazon-listing' / 'scripts' / name
    dirs = _marker_dirs_fn(path, str(scratch))()
    assert dirs[0] == str(ws), 'task workspace is not written first'
    assert str(scratch) in dirs  # MARKER_DIR is still honoured
    # And the write sites actually use it, not MARKER_DIR directly.
    src = path.read_text(encoding='utf-8')
    assert 'for d in' in src and '_marker_dirs()' in src
    assert "os.path.join(MARKER_DIR, 'UPLOAD" not in src
    assert "os.path.join(MARKER_DIR, f'UPLOAD" not in src


def test_marker_dirs_does_not_duplicate_the_workspace(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    ws = home / 'tasks' / 'abc12345-0000-0000-0000-000000000000'
    ws.mkdir(parents=True)
    monkeypatch.setenv('VIBE_HOME', str(home))
    monkeypatch.setenv('VIBE_TASK_ID', ws.name)
    path = _SCRIPTS / 'amazon-listing' / 'scripts' / 'bh_upload_flatfile.py'
    assert _marker_dirs_fn(path, str(ws))() == [str(ws)]


def test_fetched_row_is_the_batch_own_row():
    """The status text is read from the batch's own table row.

    A fixed window of cells around the batch id spilled into the next
    row, and an agent read another batch's filename as its own upload.
    """
    src = (
        _SCRIPTS / 'amazon-listing' / 'scripts' / 'bh_fetch_report.py'
    ).read_text(encoding='utf-8')
    first = src.index('row = js(')
    own_row = src.index('/ROW|TR/.test(r.tagName', first)
    window = src.index('seq.slice(', first)
    assert own_row < window, 'the neighbour-spilling window runs first'
    # A queued row's Cancel buttons are controls, not its status.
    assert 'function ctl(c,r)' in src and '!ctl(c,r)' in src


def test_fetched_report_is_tagged_with_its_batch():
    """The one moment a report is provably batch N's is when it downloads."""
    src = (
        _SCRIPTS / 'amazon-listing' / 'scripts' / 'bh_fetch_report.py'
    ).read_text(encoding='utf-8')
    assert '__batch{BATCH}' in src, 'report is not tagged with its batch'
    assert 'shutil.copy2(report, tagged)' in src
    up = (
        _SCRIPTS / 'amazon-listing' / 'scripts' / 'bh_upload_flatfile.py'
    ).read_text(encoding='utf-8')
    assert "'uploaded_at': time.time()" in up, 'marker has no upload time'


def _listing_status_src():
    return (
        _SCRIPTS / 'amazon-listing' / 'scripts' / 'bh_listing_status.py'
    ).read_text(encoding='utf-8')


def test_listing_status_proves_the_marketplace_before_reporting_rows():
    """A `.ae` URL renders SA's inventory under an SA session.

    So no row may be reported until the page's `ue_mid` equals the
    marketplace the caller meant -- and an unreadable `ue_mid` refuses
    too, exactly like the upload helper.
    """
    src = _listing_status_src()
    assert 'window.ue_mid' in src
    assert 'MARKETPLACE MISMATCH' in src
    assert 'if not live:' in src, 'no refusal when ue_mid is unreadable'
    report = src.index("out['rows'] = ")
    assert src.index('MARKETPLACE MISMATCH') < report
    assert src.index('if not live:') < report


def test_listing_status_stops_on_a_signed_out_session():
    """A lapsed session must end with a RESULT, not the wrapper's alarm.

    Observed live: every search redirected to the sign-in page, each read
    as zero rows, and the per-SKU retries ran past 120s with no output, so
    the agent abandoned the helper and hand-drove the inventory grid.
    """
    src = _listing_status_src()
    check = src.index("'/ap/signin' in href")
    assert "'/ap/mfa' in href" in src
    # Checked right after each load, before any polling for rows.
    assert src.index('wait_for_load()') < check < src.index('_ROWS_JS) or')


@pytest.mark.parametrize(
    'name', ['bh_fetch_report.py', 'bh_upload_flatfile.py']
)
def test_helpers_name_a_signed_out_session(name):
    """A sign-in page is not "batch not found" or a marketplace problem.

    The sign-in page sets ue_mid, so a lapsed session read as the right
    marketplace with the batch missing, and the agent was told to switch
    the account switcher.
    """
    src = (_SCRIPTS / 'amazon-listing' / 'scripts' / name).read_text(
        encoding='utf-8'
    )
    check = src.index("'/ap/signin' in _href")
    assert "'/ap/mfa' in _href" in src
    assert src.index("out['marketplace'] = js(") < check
    for later in ('row = js(', 'MARKETPLACE MISMATCH'):
        if later in src:
            assert check < src.index(later)


def test_listing_status_reads_the_page_by_structure():
    """Rows are the page's own `data-sku` containers, never page text."""
    src = _listing_status_src()
    assert "querySelectorAll('[data-sku]')" in src
    # The dead surface is named only to say why it is not used.
    navigations = re.findall(r'new_tab\(\s*f?[\'"]([^\'"]*)', src)
    assert navigations and not any('skucentral' in n for n in navigations)


def test_listing_skill_verifies_through_the_helper():
    """The DoD's verify_by is what the reviewer follows -- it must name
    the helper, not the skucentral page that renders empty (19 calls in
    one live run went to it)."""
    skill = (_SCRIPTS / 'amazon-listing' / 'SKILL.md').read_text(
        encoding='utf-8'
    )
    verify = skill[skill.index('verify_by:') : skill.index('\n---', 1)]
    assert 'bh_listing_status.py' in verify
    assert 'Open Manage Inventory ON THE TARGET MARKETPLACE' not in verify
    manifest = (_SCRIPTS / 'MANIFEST.txt').read_text(encoding='utf-8')
    assert 'amazon-listing/scripts/bh_listing_status.py' in manifest


def test_relist_reads_current_asins_through_the_helper():
    """Discovery, not just verification: a relist agent that had to learn
    a family's SKU -> ASIN by hand thrashed on the collapsed parent row
    and then used ASINs remembered from an earlier task."""
    skill = (_SCRIPTS / 'amazon-listing' / 'SKILL.md').read_text(
        encoding='utf-8'
    )
    step = skill[skill.index('1. **Get the source ASINs') :]
    step = step[: step.index('\n2. ')]
    assert 'bh_listing_status.py' in step
    assert 'TARGET' in step, 'the target side must be read before planning'


def _inline_dict(path, name):
    """A module-level dict literal out of a stdin-fed helper's source."""
    import ast  # noqa: PLC0415 - only this check parses sources

    tree = ast.parse(path.read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, 'id', None) == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f'{path.name} has no {name} table')


@pytest.mark.parametrize(
    'name', ['bh_upload_flatfile.py', 'bh_listing_status.py']
)
def test_helper_marketplace_tables_match_the_canonical_map(name):
    """The helpers cannot import marketplace_ids, so each carries a copy.

    A shorter copy in the status helper refused every marketplace it left
    out (.com.br, .nl, .se, .pl, .com.be, .com.tr, .ie) before reading a
    single row. Every country the canonical map knows must resolve, via
    the helper's own two tables, to the canonical id.
    """
    import importlib.util  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location(
        'marketplace_ids',
        _SCRIPTS / 'amazon-listing' / 'scripts' / 'marketplace_ids.py',
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    path = _SCRIPTS / 'amazon-listing' / 'scripts' / name
    hosts = _inline_dict(path, '_HOST_MARKETPLACES')
    countries = _inline_dict(path, '_COUNTRY_HOSTS')
    for cc, mkt in mod.MARKETPLACE_IDS.items():
        assert cc in countries, f'{name}: no host for {cc}'
        assert hosts[countries[cc]] == mkt, f'{name}: {cc} resolves wrong'
    assert set(hosts.values()) == set(mod.MARKETPLACE_IDS.values())


def test_verify_by_names_a_path_that_exists_in_a_task_workspace():
    """verify_by is followed from the task workspace, where the skill is
    under .claude/skills/ -- a bare `scripts/…` path fails before the
    helper runs."""
    skill = (_SCRIPTS / 'amazon-listing' / 'SKILL.md').read_text(
        encoding='utf-8'
    )
    verify = skill[skill.index('verify_by:') : skill.index('\n---', 1)]
    rel = '.claude/skills/amazon-listing/scripts/bh_listing_status.py'
    assert rel in verify
    assert (_SCRIPTS / rel.split('.claude/skills/', 1)[1]).is_file()


def test_skill_sequences_a_fresh_mint_across_two_marketplaces():
    """'New ASINs' + 'same children on both sites' read as a conflict to
    one agent, which re-pinned the family's OLD ASINs. The skill must
    spell the sequence: mint on one, read what it minted, pin the other."""
    skill = (_SCRIPTS / 'amazon-listing' / 'SKILL.md').read_text(
        encoding='utf-8'
    )
    sec = skill[skill.index('is not a\ncontradiction') :]
    sec = sec[: sec.index('Make the match **proactively**')]
    for needle in ('mint_new_asin', 'bh_listing_status.py', 'Never\nmint'):
        assert needle in sec, needle


def _signin_src():
    return (_SCRIPTS / 'amazon-shared' / 'scripts' / 'bh_signin.py').read_text(
        encoding='utf-8'
    )


def test_signin_helper_never_types_a_credential():
    """It only presses what Ziniao filled -- no value is ever written."""
    src = _signin_src()
    assert '.value=' not in src.replace(' ', '')
    assert 'type_text' not in src and 'insertText' not in src


def test_signin_helper_finishes_inside_the_wrapper_alarm():
    """The wrapper kills a call at 120s; the loop must answer before it."""
    src = _signin_src()
    budget = int(re.search(r"'SIGNIN_BUDGET', '(\d+)'", src).group(1))
    assert budget + 15 < 120
    assert "RESULT '" in src


def test_signin_helper_stops_on_a_broken_env_and_on_a_passkey():
    """Both are documented hand-offs (SKILL.md §2, §2c), never guessed."""
    src = _signin_src()
    assert 'navigator.webdriver===true' in src and "'broken_env'" in src
    assert "'passkey'" in src
    skill = (_SCRIPTS / 'amazon-shared' / 'SKILL.md').read_text(
        encoding='utf-8'
    )
    assert 'amazon-shared/scripts/bh_signin.py' in skill


def test_fetch_report_tells_pending_from_finished_by_the_count_cell():
    """A queued batch is pending, not a reason to doubt the helper.

    The old reason text told the agent a byte-identical row might be a
    stale render, so every honest "still queued" sent it to the status
    page by hand. A finished batch -- failed included -- shows x/y.
    """
    src = (
        _SCRIPTS / 'amazon-listing' / 'scripts' / 'bh_fetch_report.py'
    ).read_text(encoding='utf-8')
    start = src.index("out['pending'] = not any(")
    expr = src[
        start + len("out['pending'] = ") : src.index('\n        )', start) + 10
    ]
    pending = lambda row: eval(expr, {'re': re, 'row': row})  # noqa: E731, S307
    assert pending('f.txt | sheet | 1 | N/A | queued')
    assert pending(
        'f.txt | 2026/10/19 | 1 | N/A | queued'
    )  # a date is no count
    assert not pending('f.txt | sheet | 1 | 5/5 | done')
    assert not pending('f.txt | sheet | 1 | 0 / 4 | failed')
    assert 'stale render' not in src


def _switch_src():
    return (
        _SCRIPTS / 'amazon-shared' / 'scripts' / 'bh_switch_marketplace.py'
    ).read_text(encoding='utf-8')


def test_switch_helper_matches_by_structure_not_words():
    """Row by the localized region name, confirm by data-test -- no copy.

    Observed live: an agent driving the switcher by hand never found the
    confirm button (a kat-button) and spent ~45 calls on one switch.
    """
    src = _switch_src()
    assert 'Intl.DisplayNames' in src
    assert 'kat-button[data-test=confirm-selection]' in src
    assert 'data-test")==="current-account"' in src


def test_switch_helper_verifies_and_refuses_pending_rows():
    src = _switch_src()
    # The answer is the session's ue_mid afterwards, never the click.
    assert "out['ok'] = out['marketplace'] == mid" in src
    assert 'not an active marketplace' in src  # pending row refused
    assert "'/ap/signin' in href" in src


def test_mismatch_answers_name_the_switch_helper():
    for name in (
        'bh_listing_status.py',
        'bh_fetch_report.py',
        'bh_upload_flatfile.py',
    ):
        src = (_SCRIPTS / 'amazon-listing' / 'scripts' / name).read_text(
            encoding='utf-8'
        )
        assert 'bh_switch_marketplace.py' in src, name


def test_downloaded_template_is_named_for_its_marketplace():
    """An AE template must not overwrite the SA one beside it.

    Amazon names the file after the product type alone (e.g. KETTLE.xlsm);
    observed live, an AE download replaced the SA template the agent was
    about to fill.
    """
    src = (
        _SCRIPTS / 'amazon-listing' / 'scripts' / 'bh_download_template.py'
    ).read_text(encoding='utf-8')
    tag = src.index("tagged = f'{stem}__{mkt")
    assert src.index('os.replace(template, tagged)') > tag
    # Renamed before the result and the upload marker name the file.
    assert tag < src.index("out['template'] = template")


def test_switch_helper_reads_the_session_off_an_ordinary_page():
    """On the switcher page ue_mid follows the URL host, not the session.

    Read from there, the helper said "already on AE" for a session still
    on SA and reported a switch it never made -- observed live.
    """
    src = _switch_src()
    reader = src.index('def _session_marketplace():')
    assert "new_tab(f'https://{HOST}/home')" in src[reader : reader + 900]
    first_read = src.index("out['marketplace'] = _session_marketplace()")
    assert first_read < src.index("new_tab(f'https://{HOST}/account-switcher/")
    assert src.count("out['marketplace'] = _session_marketplace()") == 2


@pytest.mark.parametrize(
    'rel',
    [
        'amazon-listing/scripts/bh_fetch_report.py',
        'amazon-listing/scripts/bh_listing_status.py',
        'amazon-listing/scripts/bh_upload_flatfile.py',
        'amazon-listing/scripts/bh_download_template.py',
        'amazon-shared/scripts/bh_signin.py',
        'amazon-shared/scripts/bh_switch_marketplace.py',
    ],
)
def test_helpers_name_a_missing_parameter(rel):
    """A missing env var is a RESULT line, never a KeyError traceback."""
    src = (_SCRIPTS / rel).read_text(encoding='utf-8')
    assert "os.environ['" not in src
    start = src.index('def _env(name):')
    ns = {'os': os, 'json': __import__('json')}
    exec(src[start : src.index('\n\n\n', start)], ns)  # noqa: S102
    with pytest.raises(SystemExit):
        ns['_env']('VIBE_TEST_SURELY_UNSET')
