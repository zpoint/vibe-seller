"""Layout-v1 migration: old run artifacts auto-move at boot."""

import pytest

from app.workspace.store_data_migrate import (
    LAYOUT_VERSION,
    migrate_store_data,
)


@pytest.fixture
def ws(tmp_path):
    """A pre-v1 workspace: run data inside stores/, loose dated files."""
    slug = tmp_path / 'stores' / 'store-a'
    (slug / 'ads' / 'example').mkdir(parents=True)
    (slug / 'STORE.md').write_text('profile')
    (slug / 'notes.md').write_text('notes')
    area = slug / 'ads' / 'example'
    (area / 'AD_AUDIT_2026-06-05.md').write_text('audit')
    (area / 'METRICS_2026-06-05.tsv').write_text('m')
    (area / 'plans_onesite_20260603.tsv').write_text('p')
    (area / '商品报表_20260605_104818.zip').write_text('z')
    (area / '热销链接.xlsx').write_text('workbook')  # undated → stays
    return tmp_path


@pytest.mark.unit
def test_migrates_stores_subdirs_and_buckets_by_file_date(ws):
    result = migrate_store_data(ws)
    assert result['migrated'] is True

    area = ws / 'store-data' / 'store-a' / 'ads' / 'example'
    # dated files → their OWN month (not current month)
    assert (area / '2026-06' / 'AD_AUDIT_2026-06-05.md').is_file()
    assert (area / '2026-06' / 'METRICS_2026-06-05.tsv').is_file()
    assert (area / '2026-06' / 'plans_onesite_20260603.tsv').is_file()
    assert (area / '2026-06' / '商品报表_20260605_104818.zip').is_file()
    # cross-run working file stays at the area root
    assert (area / '热销链接.xlsx').is_file()
    # knowledge files untouched, run subdir gone from stores/
    assert (ws / 'stores' / 'store-a' / 'STORE.md').is_file()
    assert (ws / 'stores' / 'store-a' / 'notes.md').is_file()
    assert not (ws / 'stores' / 'store-a' / 'ads').exists()


@pytest.mark.unit
def test_marker_short_circuits_second_run(ws):
    migrate_store_data(ws)
    marker = ws / '.store-data-layout-version'
    assert marker.read_text().strip() == str(LAYOUT_VERSION)
    again = migrate_store_data(ws)
    assert again == {'migrated': False, 'moved': []}


@pytest.mark.unit
def test_already_bucketed_files_untouched(tmp_path):
    bucket = tmp_path / 'store-data' / 's1' / 'ads' / '2026-05'
    bucket.mkdir(parents=True)
    (bucket / 'AD_AUDIT_2026-05-09.md').write_text('old')
    result = migrate_store_data(tmp_path)
    assert result['moved'] == []
    assert (bucket / 'AD_AUDIT_2026-05-09.md').is_file()


@pytest.mark.unit
def test_merge_into_existing_dest_keeps_existing(tmp_path):
    src = tmp_path / 'stores' / 's1' / 'ads'
    src.mkdir(parents=True)
    (src / 'report_2026-04-01.tsv').write_text('from-stores')
    dest = tmp_path / 'store-data' / 's1' / 'ads'
    dest.mkdir(parents=True)
    (dest / 'report_2026-04-01.tsv').write_text('existing')
    migrate_store_data(tmp_path)
    # existing store-data copy wins; stores/ side removed
    moved = tmp_path / 'store-data' / 's1' / 'ads'
    assert not (tmp_path / 'stores' / 's1' / 'ads').exists()
    # the pre-existing file is bucketed by its date in step 2
    assert (moved / '2026-04' / 'report_2026-04-01.tsv').read_text() == (
        'existing'
    )
