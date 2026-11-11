"""Unit tests for the catalog write gate (3-tier contract).

Two layers:

- the original deterministic stub contract (``is_near_empty`` /
  ``find_wrongly_stubbed``) — pins the bug that made the
  catalog-navigation e2e flake: the sync agent stubbing a file that
  actually has a describable topic;
- the tier gate (``validate_catalog_write``): accumulation
  (L1 ⊆ L2 ⊆ L3), completeness (every tier file exactly once — names
  set the REQUIREMENT default, a row naming an existing file is the
  writer's declaration and is always honoured), isolation (no other
  store, no machine files) and format
  (header, 3 columns, path prefixes; Relevance tags are free-form).

Every gate test builds a workspace on disk, writes a VALID catalog,
then mutates exactly one thing — the failing case must name that
thing, the unmutated form must pass.
"""

import importlib.resources

import pytest

from app.workspace.catalog_validation import (
    STUB_SUMMARY,
    find_wrongly_stubbed,
    is_machine_file_name,
    is_near_empty,
    is_run_output_name,
    parse_catalog,
    reject_wrong_stubs,
    validate_catalog_write,
)

pytestmark = pytest.mark.unit


class TestIsNearEmpty:
    def test_empty_string(self):
        assert is_near_empty('') is True

    def test_heading_only(self):
        assert is_near_empty('# Logistics for my-store\n\n') is True

    def test_frontmatter_only(self):
        assert is_near_empty('---\nbrowser: chrome\n---\n\n# Store\n') is True

    def test_one_word_marker(self):
        assert is_near_empty('# Notes\n\nTODO\n') is True

    def test_describable_paragraph_is_substantive(self):
        # The catalog fixture file — must NOT be near-empty.
        content = (
            '# Catalog Verification\n\n'
            'This knowledge file exists to verify the catalog '
            'lookup flow end to end. Its topic is the verification '
            'secret that a catalog-driven task should return.\n\n'
            'SECRET: VERIFY-CATALOG-XYZ-d6152e339dd8\n'
        )
        assert is_near_empty(content) is False

    def test_short_sentence_is_substantive(self):
        # A real sentence (>= 5 body words) has a topic to summarize.
        assert (
            is_near_empty(
                '# Store A Notes\n\nStore A uses premium pricing on widgets.\n'
            )
            is False
        )

    def test_three_word_fragment_is_near_empty(self):
        # Conservative threshold: a 3-word fragment is too thin to force
        # a summary, so stubbing it stays legitimate (no redo loop).
        assert is_near_empty('# Store A Notes\n\nStore A specific.\n') is True


class TestFindWronglyStubbed:
    def _catalog(self, *rows: str) -> str:
        header = '| File | Relevance | Summary |\n|---|---|---|\n'
        return header + ''.join(rows)

    def test_substantive_file_stubbed_is_flagged(self):
        catalog = self._catalog(
            f'| knowledge/amazon/topic.md | | {STUB_SUMMARY} |\n'
        )
        files = {
            'knowledge/amazon/topic.md': (
                '# Topic\n\nA real paragraph describing the topic here.\n'
            )
        }
        bad = find_wrongly_stubbed(catalog, files.get)
        assert bad == ['knowledge/amazon/topic.md']

    def test_empty_file_stubbed_is_ok(self):
        catalog = self._catalog(
            f'| stores/s/logistics.md | | {STUB_SUMMARY} |\n'
        )
        files = {'stores/s/logistics.md': '# Logistics for s\n\n'}
        assert find_wrongly_stubbed(catalog, files.get) == []

    def test_summarized_substantive_file_is_ok(self):
        catalog = self._catalog(
            '| knowledge/amazon/topic.md | amazon | Bid tuning playbook |\n'
        )
        files = {
            'knowledge/amazon/topic.md': '# Topic\n\nLots of real content.\n'
        }
        assert find_wrongly_stubbed(catalog, files.get) == []

    def test_unreadable_file_is_skipped(self):
        catalog = self._catalog(
            f'| knowledge/amazon/missing.md | | {STUB_SUMMARY} |\n'
        )
        assert find_wrongly_stubbed(catalog, lambda _p: None) == []

    def test_header_and_separator_rows_ignored(self):
        # Only the data row matters; header/separator must not blow up.
        catalog = self._catalog(
            f'| knowledge/x/topic.md | | {STUB_SUMMARY} |\n'
        )
        files = {'knowledge/x/topic.md': 'plain body with several words here'}
        assert find_wrongly_stubbed(catalog, files.get) == [
            'knowledge/x/topic.md'
        ]


class TestRejectWrongStubs:
    """The function the write boundary (router) calls to enforce."""

    def _catalog(self, row: str) -> str:
        return '| File | Relevance | Summary |\n|---|---|---|\n' + row

    def test_raises_for_substantive_stub(self, tmp_path):
        f = tmp_path / 'topic.md'
        f.write_text('# T\n\nA real paragraph with several words here.\n')
        catalog = self._catalog(f'| knowledge/topic.md | | {STUB_SUMMARY} |\n')
        with pytest.raises(ValueError, match='real content'):
            reject_wrong_stubs(catalog, lambda _rel: f)

    def test_ok_for_empty_stub(self, tmp_path):
        f = tmp_path / 'empty.md'
        f.write_text('# Empty\n\n')
        catalog = self._catalog(f'| stores/s/empty.md | | {STUB_SUMMARY} |\n')
        reject_wrong_stubs(catalog, lambda _rel: f)  # no raise

    def test_ok_when_unreadable(self):
        def boom(_rel):
            raise FileNotFoundError(_rel)

        catalog = self._catalog(f'| knowledge/x.md | | {STUB_SUMMARY} |\n')
        reject_wrong_stubs(catalog, boom)  # no raise — can't disprove


# ── Tier-gate fixtures ─────────────────────────────────


L1_CATALOG = (
    '| File | Relevance | Summary |\n'
    '|---|---|---|\n'
    '| common/sites-amazon.md | amazon | Seller Central URLs |\n'
    '| common/ziniao.md | ziniao, browser | Ziniao browser notes |\n'
)

# Every catalog below is VALID for the workspace _ws() builds; each
# gate test mutates exactly one aspect of it.
L2_CATALOG = (
    '| File | Relevance | Summary |\n'
    '|---|---|---|\n'
    '| knowledge/project/common/sites-amazon.md | amazon | '
    'Seller Central URLs |\n'
    '| knowledge/project/common/ziniao.md | ziniao, browser | '
    'Ziniao browser notes |\n'
    '| knowledge/notes.md | amazon | Shared pricing notes |\n'
    f'| knowledge/empty.md | | {STUB_SUMMARY} |\n'
)

L3_CATALOG = (
    L2_CATALOG + '| stores/acme-x/STORE.md | | Store profile |\n'
    '| stores/acme-x/notes.md | amazon | Store-specific notes |\n'
)


def _ws(tmp_path):
    """Build a representative 2-tier workspace: L1 shipped, one L2
    note, one real note, two stores (acme-x cataloged, beta-y not)."""
    l1 = tmp_path / 'knowledge' / 'project' / 'common'
    l1.mkdir(parents=True)
    (tmp_path / 'knowledge' / 'project' / 'CATALOG.md').write_text(L1_CATALOG)
    (tmp_path / 'knowledge' / 'notes.md').write_text(
        '# Notes\n\nShared pricing notes for widgets across stores.\n'
    )
    (tmp_path / 'knowledge' / 'empty.md').write_text('# Empty\n\n')
    acme = tmp_path / 'stores' / 'acme-x'
    acme.mkdir(parents=True)
    (acme / 'STORE.md').write_text('# Store\n\nThe acme profile.\n')
    (acme / 'notes.md').write_text(
        '# Notes\n\nAcme-specific pricing behaviour.\n'
    )
    (acme / 'metadata.json').write_text('{"platforms": {"amazon": ["US"]}}')
    beta = tmp_path / 'stores' / 'beta-y'
    beta.mkdir(parents=True)
    (beta / 'notes.md').write_text('# Notes\n\nBeta-only knowledge.\n')
    return tmp_path


class _Gate:
    """Thin wrapper so each test states path + content only."""

    def __init__(self, root):
        self.root = root

    def __call__(self, path, content):
        validate_catalog_write(
            path,
            content,
            root=self.root,
            resolve_file=lambda rel: self.root / rel,
        )


@pytest.fixture
def ws(tmp_path):
    root = _ws(tmp_path)
    return root, _Gate(root)


def _rejects(gate, path, content, *, naming):
    """Assert the write is rejected AND the message names the cause."""
    with pytest.raises(ValueError) as ei:
        gate(path, content)
    assert naming in str(ei.value), (
        f'rejection did not name {naming!r}:\n{ei.value}'
    )


# ── Format ─────────────────────────────────────────────


class TestParseCatalog:
    def test_missing_header_rejected(self, ws):
        _root, gate = ws
        no_header = '| a/b.md | amazon | x |\n|---|---|---|\n'
        _rejects(gate, 'knowledge/CATALOG.md', no_header, naming='header row')

    def test_four_column_row_rejected(self, ws):
        _root, gate = ws
        wide = (
            '| File | Relevance | Summary |\n|---|---|---|\n'
            '| knowledge/notes.md | amazon | x | extra |\n'
        )
        _rejects(gate, 'knowledge/CATALOG.md', wide, naming='4 cells')

    def test_heading_and_comment_lines_ignored(self, ws):
        """Real catalogs open with a title/HTML comment — still valid."""
        _root, gate = ws
        content = '# Store Catalog — acme-x\n<!-- comment -->\n\n' + L3_CATALOG
        gate('stores/acme-x/CATALOG.md', content)  # no raise

    def test_the_shipped_knowledge_catalog_parses(self):
        """The repo's own catalog is the reference tiers are copied from.

        Every writer is told to copy its rows verbatim, so a row of it
        that does not parse is refused by every store's sync — and a
        maintainer's stray ``|`` in a summary is exactly that.
        """
        text = (
            importlib.resources.files('app') / 'knowledge' / 'CATALOG.md'
        ).read_text(encoding='utf-8')
        assert parse_catalog(text), 'the shipped catalog lists no rows'


class TestFormatPrefixes:
    def test_l2_row_with_store_path_rejected(self, ws):
        _root, gate = ws
        content = L2_CATALOG + '| stores/acme-x/notes.md | | leaked |\n'
        _rejects(
            gate,
            'knowledge/CATALOG.md',
            content,
            naming='must start with knowledge/',
        )

    def test_valid_l2_passes(self, ws):
        _root, gate = ws
        gate('knowledge/CATALOG.md', L2_CATALOG)  # no raise


class TestRelevanceTokens:
    def test_tag_no_store_or_l1_uses_is_accepted(self, ws):
        """Tags are the model's judgement. A tag for a platform no store
        sells on yet makes no read mandatory, so it must not cost the
        whole catalog regen — a standing L2 carrying such a tag was
        refused nightly when the gate checked tags against a set."""
        _root, gate = ws
        content = L2_CATALOG.replace(
            '| knowledge/notes.md | amazon |',
            '| knowledge/notes.md | widgetmart |',
        )
        gate('knowledge/CATALOG.md', content)  # no raise

    def test_l3_must_copy_an_unfamiliar_l2_tag_verbatim(self, ws):
        """With tags free-form, L3's copy of an L2 row is always held to
        the L2 Relevance — there is no 'unknown tag' carve-out."""
        root, gate = ws
        swap = (
            '| knowledge/notes.md | amazon |',
            '| knowledge/notes.md | widgetmart |',
        )
        (root / 'knowledge' / 'CATALOG.md').write_text(
            L2_CATALOG.replace(*swap)
        )
        l3 = L3_CATALOG.replace(*swap)
        gate('stores/acme-x/CATALOG.md', l3)  # verbatim copy passes
        _rejects(
            gate,
            'stores/acme-x/CATALOG.md',
            L3_CATALOG,
            naming='Relevance must stay "widgetmart"',
        )

    def test_l1_tokens_inherit_to_l2_rows(self, ws):
        """L1 ships non-platform tags (ziniao, browser); copying its
        rows verbatim must stay legal."""
        _root, gate = ws
        gate('knowledge/CATALOG.md', L2_CATALOG)  # no raise


# ── Accumulation ───────────────────────────────────────


class TestL2Accumulation:
    def test_dropped_l1_row_rejected(self, ws):
        _root, gate = ws
        content = L2_CATALOG.replace(
            '| knowledge/project/common/ziniao.md | ziniao, browser | '
            'Ziniao browser notes |\n',
            '',
        )
        _rejects(
            gate,
            'knowledge/CATALOG.md',
            content,
            naming='missing L1 row: knowledge/project/common/ziniao.md',
        )

    def test_changed_l1_relevance_rejected(self, ws):
        _root, gate = ws
        content = L2_CATALOG.replace(
            '| knowledge/project/common/sites-amazon.md | amazon |',
            '| knowledge/project/common/sites-amazon.md | |',
        )
        _rejects(
            gate,
            'knowledge/CATALOG.md',
            content,
            naming='Relevance must stay "amazon"',
        )

    def test_full_l1_copy_passes(self, ws):
        _root, gate = ws
        gate('knowledge/CATALOG.md', L2_CATALOG)  # no raise


class TestL3Accumulation:
    def test_dropped_l2_row_rejected(self, ws):
        _root, gate = ws
        # L2 must stand on disk for accumulation to be demanded.
        (ws[0] / 'knowledge' / 'CATALOG.md').write_text(L2_CATALOG)
        content = L3_CATALOG.replace(
            '| knowledge/notes.md | amazon | Shared pricing notes |\n', ''
        )
        _rejects(
            gate,
            'stores/acme-x/CATALOG.md',
            content,
            naming='missing L2 row: knowledge/notes.md',
        )

    def test_changed_l2_relevance_rejected(self, ws):
        _root, gate = ws
        (ws[0] / 'knowledge' / 'CATALOG.md').write_text(L2_CATALOG)
        content = L3_CATALOG.replace(
            '| knowledge/notes.md | amazon | Shared pricing notes |',
            '| knowledge/notes.md | | Shared pricing notes |',
        )
        _rejects(
            gate,
            'stores/acme-x/CATALOG.md',
            content,
            naming='Relevance must stay "amazon"',
        )

    def test_no_standing_l2_skips_accumulation(self, ws):
        """A fresh install with no L2 yet: L3 must still be writable
        (the two-phase fanout writes L2 first, but the gate must not
        demand a file that does not exist)."""
        _root, gate = ws
        gate('stores/acme-x/CATALOG.md', L3_CATALOG)  # no raise

    def test_an_unparseable_l2_is_no_reference(self, ws):
        """One 4-cell row in the shared catalog is a row of SOMEONE
        ELSE's file: quoting it refused every store's clean L3 write
        forever, and told the writer to fix a table it was ordered to
        copy verbatim. An unparseable reference behaves like an absent
        one — the write is judged on what it does owe."""
        _root, gate = ws
        (ws[0] / 'knowledge' / 'CATALOG.md').write_text(
            L2_CATALOG + '| knowledge/a.md | amazon | extra | cell |\n'
        )
        gate('stores/acme-x/CATALOG.md', L3_CATALOG)  # no raise
        # Only the copy-from-L2 rule goes: what the store owes its own
        # files is still refused.
        _rejects(
            gate,
            'stores/acme-x/CATALOG.md',
            L3_CATALOG.replace(
                '| stores/acme-x/notes.md | amazon | Store-specific notes |\n',
                '',
            ),
            naming='missing row(s)',
        )

    def test_full_l2_copy_passes(self, ws):
        _root, gate = ws
        (ws[0] / 'knowledge' / 'CATALOG.md').write_text(L2_CATALOG)
        gate('stores/acme-x/CATALOG.md', L3_CATALOG)  # no raise


# ── Completeness ───────────────────────────────────────


class TestL2Completeness:
    def test_missing_file_row_rejected(self, ws):
        _root, gate = ws
        content = L2_CATALOG.replace(
            '| knowledge/notes.md | amazon | Shared pricing notes |\n', ''
        )
        _rejects(gate, 'knowledge/CATALOG.md', content, naming='missing row')

    def test_row_for_nonexistent_file_rejected(self, ws):
        _root, gate = ws
        content = L2_CATALOG + ('| knowledge/ghost.md | | does not exist |\n')
        _rejects(
            gate,
            'knowledge/CATALOG.md',
            content,
            naming='does not exist: knowledge/ghost.md',
        )

    def test_duplicate_row_rejected(self, ws):
        _root, gate = ws
        dup = '| knowledge/notes.md | amazon | Shared pricing notes |\n'
        content = L2_CATALOG + dup
        _rejects(gate, 'knowledge/CATALOG.md', content, naming='duplicate')


class TestL3Completeness:
    def test_missing_store_file_rejected(self, ws):
        _root, gate = ws
        content = L3_CATALOG.replace(
            '| stores/acme-x/notes.md | amazon | Store-specific notes |\n',
            '',
        )
        _rejects(
            gate,
            'stores/acme-x/CATALOG.md',
            content,
            naming='stores/acme-x/notes.md',
        )

    def test_new_store_file_forces_a_row(self, ws):
        """Today's stub is tomorrow's reference: a newly written store
        knowledge file must appear in the next catalog."""
        root, gate = ws
        gate('stores/acme-x/CATALOG.md', L3_CATALOG)  # valid before
        (root / 'stores' / 'acme-x' / 'browser-tips.md').write_text(
            '# Tips\n\nThe kat sidebar needs coordinate clicks.\n'
        )
        _rejects(
            gate,
            'stores/acme-x/CATALOG.md',
            L3_CATALOG,
            naming='browser-tips.md',
        )

    def test_run_output_names_do_not_force_rows(self, ws):
        """A name that looks like run output exempts a file from
        REQUIRING a row — the sync agent is not chased into every
        leaked report. It forbids nothing: the next test shows the
        same file's row being honoured."""
        root, gate = ws
        (root / 'stores' / 'acme-x' / 'WIDGET_PLAN_2026-01-02.md').write_text(
            '# plan\n\noutput of some task\n'
        )
        gate('stores/acme-x/CATALOG.md', L3_CATALOG)  # no raise

    def test_row_for_output_named_file_is_the_writers_call(self, ws):
        """The writer declares a file knowledge by giving it a row.

        The user's own ``PROMO_RULES.md`` is an ALL_CAPS name carrying
        durable rules; the old name heuristic made it permanently
        uncatalogable — the server guessing from a filename what only
        the writer can say. A row naming an existing store file is
        honoured whatever its name shape.
        """
        root, gate = ws
        f = root / 'stores' / 'acme-x' / 'PROMO_RULES.md'
        f.write_text('# Promo rules\n\nBlack Friday cap is 15%.\n')
        content = L3_CATALOG + (
            '| stores/acme-x/PROMO_RULES.md | | User promo rules |\n'
        )
        gate('stores/acme-x/CATALOG.md', content)  # no raise — declared


# ── Isolation ──────────────────────────────────────────


class TestL3Isolation:
    def test_other_store_row_rejected(self, ws):
        _root, gate = ws
        content = L3_CATALOG + ('| stores/beta-y/notes.md | | other store |\n')
        _rejects(
            gate,
            'stores/acme-x/CATALOG.md',
            content,
            naming='stores/beta-y/notes.md',
        )

    def test_machine_file_row_rejected(self, ws):
        """The one name-based refusal left: machine files are server
        state, not prose a reader is pointed at. (A row naming a
        run-output NAMED file is now honoured — see
        test_row_for_output_named_file_is_the_writers_call.)"""
        root, gate = ws
        (root / 'stores' / 'acme-x' / 'metadata.json').write_text('{}')
        content = L3_CATALOG + (
            '| stores/acme-x/metadata.json | | server state |\n'
        )
        _rejects(
            gate,
            'stores/acme-x/CATALOG.md',
            content,
            naming='machine file must not be cataloged',
        )

    def test_ghost_row_still_rejected_with_new_name(self, ws):
        """The existence rule does not bend for output-shaped names:
        a row that names NO file is refused whatever it is called."""
        _root, gate = ws
        content = L3_CATALOG + (
            '| stores/acme-x/AD_AUDIT_2099-01-01.md | | audit |\n'
        )
        _rejects(
            gate,
            'stores/acme-x/CATALOG.md',
            content,
            naming='does not exist',
        )

    def test_stub_check_runs_inside_gate(self, ws):
        """The original stub contract is part of the gate: stubbing a
        substantive file is rejected even when every row is present."""
        root, gate = ws
        (root / 'stores' / 'acme-x' / 'notes.md').write_text(
            '# Notes\n\nAcme needs coordinate clicks on the sidebar '
            'and a VAT ID on every invoice.\n'
        )
        content = L3_CATALOG.replace(
            '| stores/acme-x/notes.md | amazon | Store-specific notes |',
            f'| stores/acme-x/notes.md | amazon | {STUB_SUMMARY} |',
        )
        _rejects(
            gate,
            'stores/acme-x/CATALOG.md',
            content,
            naming='must be summarized',
        )


class TestRunOutputNames:
    """Names only set the requirement DEFAULT (no row forced); they
    never forbid a row. The user's own PROMO_RULES.md is knowledge —
    an ALL_CAPS name cannot overrule the writer's declaration."""

    @pytest.mark.parametrize(
        ('name', 'exempt_from_forcing_a_row'),
        [
            ('notes.md', False),
            ('STORE.md', False),
            ('browser-tips.md', False),
            ('fbn_quirks.md', False),  # underscore, but lowercase
            ('notes_OLD.md', False),  # caps run, no underscore join
            ('WIDGET_PLAN.md', True),
            ('AD_AUDIT_REPORT.md', True),
            ('Q4_PLAN.md', True),
            ('PROMO_RULES.md', True),  # ALL_CAPS name, knowledge file
            ('notes_2026-01-02.md', True),
            ('notes-20260102.md', True),
            ('metadata.json', False),  # machine, not run-output
        ],
    )
    def test_run_output_shape(self, name, exempt_from_forcing_a_row):
        assert is_run_output_name(name) is exempt_from_forcing_a_row


class TestMachineFileNames:
    """Machine files are the ONE name rule the server still refuses
    a row for: the server/tools wrote them, no reader reads them."""

    @pytest.mark.parametrize(
        ('name', 'expected'),
        [
            ('metadata.json', True),
            ('anything.lock', True),
            ('anything.cache', True),
            ('notes.md', False),
            ('PROMO_RULES.md', False),  # knowledge, whatever the shape
            ('data.csv', False),
        ],
    )
    def test_machine_shape(self, name, expected):
        assert is_machine_file_name(name) is expected


class TestGateDispatch:
    """Only tier catalogs are gated; anything else passes through
    (knowledge/project/CATALOG.md is refused later, as read-only L1)."""

    def test_l1_path_is_noop(self, ws):
        _root, gate = ws
        gate('knowledge/project/CATALOG.md', 'anything at all')  # no raise

    def test_nested_catalog_is_noop(self, ws):
        _root, gate = ws
        gate('stores/acme-x/sub/CATALOG.md', '| broken |')  # no raise
