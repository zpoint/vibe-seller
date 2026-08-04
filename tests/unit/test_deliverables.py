"""The deliverable manifest's invariants, one test per bug it closes.

Every case here corresponds to something that actually happened on a
live run, so a regression is not hypothetical:

* a header-only CSV shipped as a delivered file inside a COMPLETED run,
* a store with no FBA failed for a report it cannot produce,
* a run attempted 9 files of 19 and reported "9 of 9",
* a fee report absent on day 3 of the following month read as a failure
  when it is simply not published yet.
"""

from __future__ import annotations

import datetime as dt
import json
import types
import zipfile

import pytest

from app.deliverables.manifest import (
    Deliverable,
    DeliverableKind,
    derive_manifest,
    month_before,
    store_capabilities,
)
from app.deliverables.verify import (
    DeliverableStatus,
    data_rows,
    verify_workspace,
)

pytestmark = pytest.mark.unit

# The header of a real monthly-storage export, truncated. What matters is
# that it is well-formed and carries no data row — the exact shape an
# unpublished month downloads as.
STORAGE_HEADER = 'service_month,sku,warehouse_code,charged_amount\n'


def _store(platform_countries: dict, capabilities: dict | None = None):
    """A stand-in for the ORM row; the manifest only reads two fields."""
    return types.SimpleNamespace(
        platform_countries=json.dumps(platform_countries),
        capabilities=json.dumps(capabilities or {}),
    )


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


class TestRowCounting:
    """A byte count cannot tell these apart; a row count must."""

    def test_header_only_csv_has_zero_rows(self, tmp_path):
        f = tmp_path / 'storage.csv'
        _write(f, STORAGE_HEADER)
        assert f.stat().st_size > 0  # this is what fooled the size check
        assert data_rows(f) == 0

    def test_one_data_row_counts_as_one(self, tmp_path):
        f = tmp_path / 'storage.csv'
        _write(f, STORAGE_HEADER + '2026-06,WIDGET-006,WH1,1.23\n')
        assert data_rows(f) == 1

    def test_embedded_newline_does_not_inflate_the_count(self, tmp_path):
        """A quoted newline is one row, not two.

        Counting lines instead of parsing would read a single product
        whose title wraps as two data rows — turning an empty file into a
        populated one on exactly the reports that carry long titles.
        """
        f = tmp_path / 'ledger.csv'
        _write(f, 'sku,title\n"WIDGET-006","two\nlines"\n')
        assert data_rows(f) == 1

    def test_unreadable_file_counts_as_nothing_not_zero(self, tmp_path):
        """An unparseable file reports ``None``, not 0.

        0 is a claim about the data ("nothing happened"); ``None`` is the
        absence of a claim. They were the same value until an EVENT entry
        declared ``min_rows=0`` and made 0 a passing grade.
        """
        bad = tmp_path / 'broken.xlsx'
        bad.write_bytes(b'not a zip at all')
        assert data_rows(bad) is None

    def test_xlsx_rows_are_counted(self, tmp_path):
        f = tmp_path / 'ads.xlsx'
        sheet = (
            '<worksheet><sheetData>'
            '<row r="1"><c/></row><row r="2"><c/></row><row r="3"><c/></row>'
            '</sheetData></worksheet>'
        )
        with zipfile.ZipFile(f, 'w') as zf:
            zf.writestr('xl/worksheets/sheet1.xml', sheet)
        assert data_rows(f) == 2  # 3 rows minus the header

    def test_xlsx_rows_spanning_read_boundaries_are_not_lost(self, tmp_path):
        """A ``<row`` tag split across a read boundary must still count.

        Placed DELIBERATELY, not by luck: a tag is aligned to start two
        bytes before the 64KB mark, so ``<r`` lands in one read and ``ow``
        in the next. Merely making the sheet large does not exercise this —
        with wide rows the odds of a tag straddling a boundary are under
        1%, so such a test passes against the bug and proves nothing.

        Chunked scanning without a carry-over drops that row. Undercounting
        here is not benign: it reports a populated export as empty and
        manufactures a gap against a healthy file.
        """
        chunk = 65536
        head = '<worksheet><sheetData>'
        header_row = '<row r="1"><c/></row>'
        # Pad with an XML comment so the NEXT '<row' starts at chunk-2.
        overhead = len('<!--') + len('-->')
        pad = chunk - 2 - len(head) - len(header_row) - overhead
        assert pad > 0
        body = (
            f'{head}{header_row}<!--{"x" * pad}-->'
            '<row r="2"><c/></row><row r="3"><c/></row>'
            '</sheetData></worksheet>'
        )
        assert body.index('<row r="2"') == chunk - 2, 'alignment broke'

        f = tmp_path / 'ads.xlsx'
        with zipfile.ZipFile(f, 'w') as zf:
            zf.writestr('xl/worksheets/sheet1.xml', body)
        assert data_rows(f) == 2  # 3 rows minus the header

    def test_empty_xlsx_has_zero_rows(self, tmp_path):
        f = tmp_path / 'ads.xlsx'
        with zipfile.ZipFile(f, 'w') as zf:
            zf.writestr(
                'xl/worksheets/sheet1.xml',
                '<worksheet><sheetData><row r="1"><c/></row>'
                '</sheetData></worksheet>',
            )
        assert data_rows(f) == 0


class TestEmptyFileIsNotDelivered:
    """The bug: ``Complete`` upstream + valid header + size>0 passed."""

    def test_empty_accrual_past_publication_is_a_gap(self, tmp_path):
        _write(tmp_path / 'storage.csv', STORAGE_HEADER)
        entry = Deliverable(
            relpath='storage.csv',
            month='2026-06',
            kind=DeliverableKind.ACCRUAL,
            published_from_day=15,
        )
        report = verify_workspace(tmp_path, [entry], today=dt.date(2026, 8, 3))
        assert report.statuses['storage.csv'] is DeliverableStatus.EMPTY
        assert not report.ok
        assert '0 data rows' in report.gaps[0]

    def test_empty_event_file_is_delivered(self, tmp_path):
        """Zero removals in a month is an answer, not an omission."""
        _write(tmp_path / 'rtv.csv', 'barcode,charged_amount\n')
        entry = Deliverable(
            relpath='rtv.csv',
            month='2026-06',
            kind=DeliverableKind.EVENT,
            min_rows=0,
            published_from_day=15,
        )
        report = verify_workspace(tmp_path, [entry], today=dt.date(2026, 8, 3))
        assert report.statuses['rtv.csv'] is DeliverableStatus.OK
        assert report.ok

    def test_unparseable_file_is_never_delivered(self, tmp_path):
        """A file nothing can be read out of answers nothing.

        ``min_rows=0`` makes zero rows a legal answer, and the row counter
        used to report 0 for a file it could not parse — so a truncated
        download, or an HTML error page saved under a ``.csv`` name, would
        satisfy ``count >= 0`` and pass as a legitimate empty report.
        Producing no rows and producing nothing readable are different
        claims and only the first one is evidence.
        """
        bad = tmp_path / 'rtv.xlsx'
        bad.write_bytes(b'<html>Gateway Timeout</html>')
        entry = Deliverable(
            relpath='rtv.xlsx',
            month='2026-06',
            kind=DeliverableKind.EVENT,
            min_rows=0,
            published_from_day=15,
        )
        report = verify_workspace(tmp_path, [entry], today=dt.date(2026, 8, 3))
        assert report.statuses['rtv.xlsx'] is DeliverableStatus.UNREADABLE
        assert not report.ok
        assert any('rtv.xlsx' in g for g in report.gaps)

    def test_min_rows_governs_even_for_an_event(self, tmp_path):
        """An EVENT that declares a floor must be held to it.

        Zero is legal for events only because they declare ``min_rows=0``.
        Exempting the whole kind instead would silently ignore a raised
        threshold on a future entry that genuinely must carry rows.
        """
        _write(tmp_path / 'ev.csv', 'a,b\n1,2\n')  # one row
        entry = Deliverable(
            relpath='ev.csv',
            month='2026-06',
            kind=DeliverableKind.EVENT,
            min_rows=3,
        )
        report = verify_workspace(tmp_path, [entry], today=dt.date(2026, 8, 3))
        assert report.statuses['ev.csv'] is DeliverableStatus.EMPTY
        assert not report.ok
        assert 'short of 3' in report.gaps[0]

    def test_missing_event_file_is_still_a_gap(self, tmp_path):
        """Empty proves the question was asked; absent proves nothing."""
        entry = Deliverable(
            relpath='rtv.csv',
            month='2026-06',
            kind=DeliverableKind.EVENT,
            min_rows=0,
            published_from_day=15,
        )
        report = verify_workspace(tmp_path, [entry], today=dt.date(2026, 8, 3))
        assert report.statuses['rtv.csv'] is DeliverableStatus.MISSING
        assert not report.ok


class TestPublicationLatency:
    """Absent-because-unpublished must complete; absent-later must not."""

    ENTRY = Deliverable(
        relpath='storage.csv',
        month='2026-07',
        kind=DeliverableKind.ACCRUAL,
        published_from_day=15,
    )

    def test_before_publication_day_is_pending_not_a_gap(self, tmp_path):
        report = verify_workspace(
            tmp_path, [self.ENTRY], today=dt.date(2026, 8, 3)
        )
        assert report.statuses['storage.csv'] is DeliverableStatus.PENDING
        assert report.ok, 'a day-3 storage absence must not fail the run'
        assert report.pending

    def test_on_publication_day_absence_becomes_a_gap(self, tmp_path):
        report = verify_workspace(
            tmp_path, [self.ENTRY], today=dt.date(2026, 8, 15)
        )
        assert report.statuses['storage.csv'] is DeliverableStatus.MISSING
        assert not report.ok

    def test_an_older_month_is_never_excused_by_an_early_day(self, tmp_path):
        """The prose rule keyed off the day number and forgot the month.

        June's fees are long published by 3 August; only July's are not.
        A day-of-month test alone would excuse both.
        """
        june = Deliverable(
            relpath='storage.csv',
            month='2026-06',
            kind=DeliverableKind.ACCRUAL,
            published_from_day=15,
        )
        report = verify_workspace(tmp_path, [june], today=dt.date(2026, 8, 3))
        assert report.statuses['storage.csv'] is DeliverableStatus.MISSING
        assert not report.ok

    def test_empty_before_publication_day_is_pending(self, tmp_path):
        """Downloaded-but-empty early in the month is the same fact."""
        _write(tmp_path / 'storage.csv', STORAGE_HEADER)
        report = verify_workspace(
            tmp_path, [self.ENTRY], today=dt.date(2026, 8, 3)
        )
        assert report.statuses['storage.csv'] is DeliverableStatus.PENDING
        assert report.ok


class TestCapabilities:
    """A store cannot be failed for a report it cannot produce."""

    ENTRY = Deliverable(
        relpath='storage.csv',
        month='2026-06',
        kind=DeliverableKind.ACCRUAL,
        requires='amazon.fba',
        published_from_day=15,
    )

    def test_undeclared_capability_makes_absence_acceptable(self, tmp_path):
        report = verify_workspace(
            tmp_path, [self.ENTRY], today=dt.date(2026, 8, 3), capabilities={}
        )
        assert report.statuses['storage.csv'] is DeliverableStatus.SKIPPED
        assert report.ok

    def test_declared_capability_makes_absence_a_gap(self, tmp_path):
        report = verify_workspace(
            tmp_path,
            [self.ENTRY],
            today=dt.date(2026, 8, 3),
            capabilities={'amazon.fba': True},
        )
        assert report.statuses['storage.csv'] is DeliverableStatus.MISSING
        assert not report.ok

    def test_declared_false_drops_the_entry_from_the_manifest(self):
        store = _store(
            {'amazon': ['SA']}, {'amazon': {'fba': False, 'ads': False}}
        )
        paths = [
            d.relpath
            for d in derive_manifest(
                store, 'acme', '2026-07', fee_month='2026-06'
            )
        ]
        assert not any('storage.csv' in p for p in paths)
        assert not any('return.csv' in p for p in paths)
        assert not any('Advertised_product' in p for p in paths)
        # The transaction ledger has no capability requirement, so it
        # survives — a store with no FBA still has sales.
        assert any('MonthlyTransaction.csv' in p for p in paths)

    def test_capabilities_flatten_to_dotted_keys(self):
        store = _store({}, {'amazon': {'fba': True, 'ads': False}})
        assert store_capabilities(store) == {
            'amazon.fba': True,
            'amazon.ads': False,
        }

    def test_malformed_capabilities_do_not_raise(self):
        store = types.SimpleNamespace(
            platform_countries='{}', capabilities='not json'
        )
        assert store_capabilities(store) == {}


class TestDerivedDenominator:
    """The denominator may not come from what the run produced."""

    def test_two_platform_store_owes_every_file(self):
        store = _store(
            {'amazon': ['SA', 'AE'], 'noon': ['SA', 'AE']},
            {
                'amazon': {'fba': True, 'ads': True},
                'noon': {'fbn': True, 'ads': True},
            },
        )
        manifest = derive_manifest(
            store, 'acme', '2026-07', fee_month='2026-06'
        )
        paths = [d.relpath for d in manifest]

        # Amazon: transaction + SP + return, per marketplace.
        assert sum('MonthlyTransaction.csv' in p for p in paths) == 2
        assert sum('Advertised_product' in p for p in paths) == 2
        assert sum(p.endswith('return.csv') for p in paths) == 2
        # Fee backfill for the older month, per marketplace.
        assert sum(p.endswith('storage.csv') for p in paths) == 2
        # noon: ONE transaction view for the store, ads per country.
        assert sum('transactionviewreport' in p for p in paths) == 1
        assert sum('ads_overview' in p for p in paths) == 2
        # noon fee reports: 4 per country for the older month.
        assert sum('monthly_storage_' in p for p in paths) == 2
        assert sum('longterm_storage_' in p for p in paths) == 2
        assert sum('nonsaleable_storage_' in p for p in paths) == 2
        assert sum('rtv_removal_' in p for p in paths) == 2

        assert len(paths) == len(set(paths)), 'no entry may be duplicated'

    def test_a_partial_run_reports_the_full_denominator(self, tmp_path):
        """The "9 of 9 expected" bug, pinned.

        Producing one file of several must never read as 1/1. The
        denominator is what the store *owes* — every derived entry whose
        capability is declared — so it can exceed what the run attempted.
        """
        store = _store(
            {'amazon': ['SA']}, {'amazon': {'fba': True, 'ads': True}}
        )
        manifest = derive_manifest(
            store, 'acme', '2026-07', fee_month='2026-06'
        )
        produced = next(
            d for d in manifest if 'MonthlyTransaction' in d.relpath
        )
        _write(tmp_path / produced.relpath, 'a,b\n1,2\n')

        report = verify_workspace(
            tmp_path,
            manifest,
            today=dt.date(2026, 8, 3),
            capabilities={'amazon.fba': True, 'amazon.ads': True},
        )
        assert report.delivered == 1
        assert report.expected == len(manifest) > 1
        assert f'1/{len(manifest)}' in report.summary()

    def test_undeclared_capability_is_not_counted_as_owed(self, tmp_path):
        """An unasserted capability must not pad the denominator.

        Reporting "1/4" when one of those four is a report nobody has
        established this store can produce understates a run that did
        everything actually asked of it.
        """
        store = _store({'amazon': ['SA']}, {'amazon': {'fba': True}})
        manifest = derive_manifest(store, 'acme', '2026-07')
        for entry in manifest:
            _write(tmp_path / entry.relpath, 'a,b\n1,2\n')
        # amazon.ads is absent from capabilities → undeclared. Remove the
        # file so it is judged on absence rather than on content.
        ads = next(d for d in manifest if 'Advertised_product' in d.relpath)
        (tmp_path / ads.relpath).unlink()

        report = verify_workspace(
            tmp_path,
            manifest,
            today=dt.date(2026, 8, 3),
            capabilities={'amazon.fba': True},
        )
        assert report.statuses[ads.relpath] is DeliverableStatus.SKIPPED
        assert report.expected == len(manifest) - 1
        assert report.ok

    def test_undeclared_capability_beats_publication_latency(self, tmp_path):
        """Capability is checked first, so it cannot report as pending.

        A not-yet-due entry whose capability is undeclared used to come
        back PENDING — a claim that we are waiting on a report which may
        not exist for this store at all.
        """
        entry = Deliverable(
            relpath='storage.csv',
            month='2026-07',
            kind=DeliverableKind.ACCRUAL,
            requires='amazon.fba',
            published_from_day=15,
        )
        report = verify_workspace(
            tmp_path, [entry], today=dt.date(2026, 8, 3), capabilities={}
        )
        assert report.statuses['storage.csv'] is DeliverableStatus.SKIPPED
        assert not report.pending
        assert report.ok

    def test_only_monthly_storage_demands_rows(self):
        """Long-term and non-saleable storage may legitimately be zero.

        Monthly storage bills all held stock, so rows there prove the
        service month posted. Long-term bills only aged stock and
        non-saleable only damaged units — a small, fast-turning store has
        genuinely none. Demanding rows from those reports a published
        month as missing, which sends a run back for a correct file.
        """
        store = _store({'noon': ['sa']}, {'noon': {'fbn': True}})
        by_name = {
            d.relpath.rsplit('/', 1)[-1]: d
            for d in derive_manifest(
                store, 'acme', '2026-07', fee_month='2026-06'
            )
        }
        assert by_name['monthly_storage_sa_2026-06.csv'].min_rows == 1
        for stem in ('longterm_storage', 'nonsaleable_storage'):
            entry = by_name[f'{stem}_sa_2026-06.csv']
            assert entry.min_rows == 0, stem
            # Still expected to EXIST — an absent file proves nothing.
            assert entry.published_from_day == 15

    def test_a_present_but_empty_conditional_report_is_accepted(self, tmp_path):
        store = _store({'noon': ['sa']}, {'noon': {'fbn': True}})
        man = derive_manifest(store, 'acme', '2026-07', fee_month='2026-06')
        conditional = next(d for d in man if 'longterm_storage' in d.relpath)
        _write(tmp_path / conditional.relpath, 'sku,charged_amount\n')
        report = verify_workspace(
            tmp_path,
            [conditional],
            today=dt.date(2026, 8, 20),
            capabilities={'noon.fbn': True},
        )
        assert report.statuses[conditional.relpath] is DeliverableStatus.OK
        assert report.ok

    def test_fee_month_is_optional(self):
        store = _store({'amazon': ['SA']}, {'amazon': {'fba': True}})
        paths = [d.relpath for d in derive_manifest(store, 'acme', '2026-07')]
        assert not any(p.endswith('storage.csv') for p in paths)

    def test_no_platforms_yields_no_obligations(self):
        assert derive_manifest(_store({}), 'acme', '2026-07') == []


class TestMonthArithmetic:
    def test_month_before(self):
        assert month_before('2026-08') == '2026-07'

    def test_month_before_crosses_the_year(self):
        assert month_before('2026-01') == '2025-12'

    def test_filename_month_token_matches_the_month(self):
        store = _store({'amazon': ['SA']})
        paths = [d.relpath for d in derive_manifest(store, 'acme', '2026-01')]
        assert any('2026JanMonthlyTransaction.csv' in p for p in paths)
        assert any(p.startswith('reports_01_sa_acme/') for p in paths)
