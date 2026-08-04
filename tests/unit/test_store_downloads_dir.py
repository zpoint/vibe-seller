"""A run must not be able to mistake last month's download for its own.

`~/.vibe-seller/downloads/<slug>/` accumulated every file the store had
ever downloaded, and the platform filenames carry no month — so a
month-old `fbn_finance_monthlystoragechargereport.csv` sat under exactly
the name this run's download would have. An agent globbing by name files
June data as July and nothing downstream can tell. It fooled a human
too: a `grep -c` over that directory reported twelve noon files landed
during a run that had produced none.
"""

from pathlib import Path

import pytest

from app.browser.downloads import ARCHIVE_NAME, archive_previous

pytestmark = pytest.mark.unit


def _touch(d: Path, name: str, body: str = 'x') -> Path:
    p = d / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding='utf-8')
    return p


class TestArchivingPreviousDownloads:
    def test_the_live_dir_is_empty_afterwards(self, tmp_path):
        dl = tmp_path / 'acme'
        _touch(dl, 'fbn_finance_monthlystoragechargereport.csv', 'june')
        _touch(dl, '_OVERVIEW_ALL_Report_2026-04-01_2026-04-30.xlsx')

        archive_previous(dl)

        live = [p.name for p in dl.iterdir() if p.name != ARCHIVE_NAME]
        assert live == [], f'a stale file can still shadow this run: {live}'

    def test_nothing_is_deleted(self, tmp_path):
        # These are a seller's financial reports. A retry that rmtree'd a
        # task workspace once destroyed exactly this kind of output, so
        # the sweep archives and never removes.
        dl = tmp_path / 'acme'
        _touch(dl, 'MonthlyTransaction.csv', 'june numbers')

        archive_previous(dl)

        found = list((dl / ARCHIVE_NAME).rglob('MonthlyTransaction.csv'))
        assert len(found) == 1
        assert found[0].read_text(encoding='utf-8') == 'june numbers'

    def test_this_run_sees_only_its_own_file(self, tmp_path):
        """The defect, end to end: same filename, two months apart."""
        dl = tmp_path / 'acme'
        name = 'fbn_finance_monthlystoragechargereport.csv'
        _touch(dl, name, 'JUNE')

        archive_previous(dl)  # browser starts for this run
        _touch(dl, name, 'JULY')  # this run downloads

        matches = sorted(p for p in dl.glob(name))
        assert len(matches) == 1, 'name-globbing must not find two'
        assert matches[0].read_text(encoding='utf-8') == 'JULY'

    def test_archives_never_nest(self, tmp_path):
        # Two sessions in a row must not bury the first archive inside
        # the second — `_prev` is excluded from the sweep.
        dl = tmp_path / 'acme'
        _touch(dl, 'first.csv')
        archive_previous(dl)
        _touch(dl, 'second.csv')
        archive_previous(dl)

        assert len(list((dl / ARCHIVE_NAME).glob('*/first.csv'))) == 1
        assert len(list((dl / ARCHIVE_NAME).glob('*/second.csv'))) == 1
        assert not list(dl.glob(f'{ARCHIVE_NAME}/*/{ARCHIVE_NAME}'))

    def test_a_fresh_store_is_a_no_op(self, tmp_path):
        dl = tmp_path / 'brand-new'
        assert archive_previous(dl) == dl
        assert dl.is_dir()
        assert not (dl / ARCHIVE_NAME).exists(), (
            'an empty dir must not accrue an empty archive every start'
        )

    def test_subdirectories_move_too(self, tmp_path):
        # browser-use sometimes lands files in a nested dir; leaving those
        # behind would leave the stale-shadow hole half open.
        dl = tmp_path / 'acme'
        _touch(dl / 'nested', 'report.csv', 'june')

        archive_previous(dl)

        assert not (dl / 'nested').exists()
        assert len(list((dl / ARCHIVE_NAME).glob('*/nested/report.csv'))) == 1

    def test_an_unwritable_dir_still_returns_the_path(self, tmp_path):
        # Housekeeping must never be the reason a browser fails to start.
        dl = tmp_path / 'acme'
        _touch(dl, 'a.csv')
        dl.chmod(0o500)  # read+execute: cannot create the archive
        try:
            assert archive_previous(dl) == dl
        finally:
            dl.chmod(0o700)
