"""ad_coverage: an audit covered what it declared, with consistent numbers.

Each case is one incident the old prose gates were built for, now
checked against the TSV contract. Figures are round and invented.
"""

from datetime import UTC, date, datetime, timedelta
import os
import time
import uuid

import pytest

from app.ai.stop_gates import ad_coverage
from app.config import VIBE_SELLER_DIR
from tests.unit.ad_fixtures import AdTask

pytestmark = pytest.mark.unit

TODAY = date.today().isoformat()


@pytest.fixture
def task(monkeypatch):
    started = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    t = AdTask(f'cov-{uuid.uuid4().hex[:8]}', monkeypatch, started)
    t.declare('audit')
    t.targets([('amazon', 'SA')])
    return t


def campaign(cid='A1234567', **kw):
    row = {
        'platform': 'amazon',
        'country': 'SA',
        'campaign_id': cid,
        'name': f'Widget {cid[-2:]}',
        'type': 'SP',
        'state': 'enabled',
        'currency': 'SAR',
        'window_start': '2026-01-01',
        'window_end': TODAY,
        'spend': '100',
        'clicks': '50',
        'orders': '5',
        'sales': '400',
        'drill_status': 'drilled',
        'note': '',
    }
    row.update({k: str(v) for k, v in kw.items()})
    return row


def full_drill(task, cid='A1234567', term_spend=(60, 40), **kw):
    task.drill(
        cid,
        [
            {
                'ad_group': 'g1',
                'target': 'widget',
                'match_type': 'exact',
                'spend': '60',
                'clicks': '30',
            },
            {
                'ad_group': 'g1',
                'target': 'gadget',
                'match_type': 'phrase',
                'spend': '40',
                'clicks': '20',
            },
        ],
        [
            {'ad_group': 'g1', 'search_term': f'term {i}', 'spend': str(s)}
            for i, s in enumerate(term_spend)
        ],
        **kw,
    )


def findings(task):
    deny = ad_coverage.check('', task.id)
    if deny is None:
        return [], []
    return list(deny.gaps), list(deny.contradictions)


def test_complete_audit_passes(task):
    task.campaigns([campaign()])
    full_drill(task)
    assert findings(task) == ([], [])


def test_non_audit_kinds_owe_no_coverage(task):
    task.declare('investigate')
    task.campaigns([campaign(drill_status='drilled')])  # nothing drilled
    assert ad_coverage.check('', task.id) is None


def test_undeclared_data_asks_for_a_declaration(task):
    (task.dir / 'AD_TASK.json').unlink()
    task.campaigns([campaign()])
    gaps, _ = findings(task)
    assert gaps == [ad_coverage.MISSING_DECLARATION]


def test_undeclared_and_no_data_is_not_an_ad_task(task):
    (task.dir / 'AD_TASK.json').unlink()
    assert ad_coverage.check('', task.id) is None


def test_missing_campaigns_file(task):
    gaps, _ = findings(task)
    assert any('CAMPAIGNS.tsv missing' in g for g in gaps)


def test_owed_marketplace_absent(task):
    """A 5-market store once got a 1-market audit."""
    task.targets([('amazon', 'SA'), ('amazon', 'AE'), ('noon', 'SA')])
    task.campaigns([campaign()])
    full_drill(task)
    gaps, _ = findings(task)
    assert sum('owed by the declaration' in g for g in gaps) == 2


def test_a_narrow_declaration_owes_only_its_combo(task):
    task.targets([('amazon', 'SA'), ('amazon', 'AE')])
    task.declare('audit', {'combos': [{'platform': 'amazon', 'country': 'SA'}]})
    task.campaigns([campaign()])
    full_drill(task)
    assert findings(task) == ([], [])


def test_outside_declared_scope(task):
    task.declare('audit', {'combos': [{'platform': 'amazon', 'country': 'SA'}]})
    task.campaigns([campaign(), campaign('A7654321', country='AE')])
    full_drill(task)
    gaps, _ = findings(task)
    assert any('outside the declared scope' in g for g in gaps)


def test_an_empty_marketplace_is_one_noted_row(task):
    task.campaigns([campaign(cid='', note='no campaigns in this market')])
    assert findings(task) == ([], [])


def test_collapsed_list_against_history(task):
    """A lazy list read as 20 of 45; AE called empty with 10 live."""
    old = time.time() - 3 * 86400
    for i in range(6):
        for p in task.drill(f'A90000{i:02d}', [{'target': 'x'}], []):
            os.utime(p, (old, old))
    task.campaigns([campaign()])
    full_drill(task)
    gaps, _ = findings(task)
    assert any('in the last weeks' in g for g in gaps)
    task.campaigns([campaign(note='five paused this week')])
    gaps, _ = findings(task)
    assert not any('in the last weeks' in g for g in gaps)


def test_live_campaign_not_drilled(task):
    task.campaigns([campaign(drill_status='inactive')])
    gaps, _ = findings(task)
    assert any('live campaign marked inactive' in g for g in gaps)


def test_blocked_needs_a_reason(task):
    task.campaigns([campaign(drill_status='blocked')])
    assert any('blocked without a note' in g for g in findings(task)[0])
    task.campaigns([campaign(drill_status='blocked', note='console error')])
    assert findings(task) == ([], [])


def test_drilled_without_files(task):
    """12 campaigns claimed drilled, 4 files on disk."""
    task.campaigns([campaign()])
    gaps, _ = findings(task)
    assert any('searchterms.tsv missing' in g for g in gaps)


def test_aggregate_row_is_not_a_drill(task):
    task.campaigns([campaign()])
    task.drill(
        'A1234567',
        [{'ad_group': 'g1', 'target': 'all', 'spend': '100'}],
        [{'search_term': 't', 'spend': '100'}],
    )
    assert any('not a campaign total' in g for g in findings(task)[0])


def test_partial_search_term_capture(task):
    """noon's top-15 tab read instead of the export: 0.27 of the total."""
    task.campaigns([campaign()])
    full_drill(task, term_spend=(20, 7))
    assert any('search terms cover 27%' in g for g in findings(task)[0])


def test_search_terms_above_targeting_is_a_contradiction(task):
    """Ratios of 1.26 and 13.3 came from joining the wrong campaign."""
    task.campaigns([campaign()])
    full_drill(task, term_spend=(100, 30))
    _, contradictions = findings(task)
    assert any('exceeds targeting' in c for c in contradictions)


def test_campaign_row_disagreeing_with_its_layer(task):
    task.campaigns([campaign(spend='300')])
    full_drill(task)
    _, contradictions = findings(task)
    assert any('joined to the wrong id' in c for c in contradictions)


def test_name_is_the_id(task):
    task.campaigns([campaign(name='A1234567')])
    full_drill(task)
    assert any('name is empty or the id' in g for g in findings(task)[0])


def test_transcribed_files_from_an_earlier_run(task):
    """Last week's report re-submitted as this week's."""
    task.campaigns([campaign()])
    full_drill(task)
    old = time.time() - 7 * 86400
    for p in task.ads_dir().iterdir():
        os.utime(p, (old, old))
    assert any('predates this task' in g for g in findings(task)[0])


def test_stale_window(task):
    old = (date.today() - timedelta(days=30)).isoformat()
    task.campaigns([campaign(window_end=old)])
    full_drill(task)
    assert any('days old' in g for g in findings(task)[0])


def test_campaign_narrowed_scope_checks_only_those(task):
    task.declare(
        'audit',
        {
            'combos': [{'platform': 'amazon', 'country': 'SA'}],
            'campaigns': ['A1234567'],
        },
    )
    task.campaigns([campaign(), campaign('A7654321', drill_status='inactive')])
    full_drill(task)
    assert findings(task) == ([], [])


def test_legacy_store_dir_is_read_too(task):
    """Older history lives under stores/<slug>/ads; it still counts."""
    task.campaigns([campaign()])
    full_drill(task)
    legacy = VIBE_SELLER_DIR / 'stores' / task.slug / 'ads' / 'Amazon' / 'sa'
    legacy.mkdir(parents=True)
    for p in task.ads_dir().iterdir():
        p.rename(legacy / p.name)
    assert findings(task) == ([], [])
