"""E2E: a real agent audits and tunes ads through the TSV contract.

Against the local ``fake_ads_console`` (three campaigns, the third only
behind "Load more"; a write form whose every change is logged), these
pin what the server now holds an ad task to:

1. an audit enumerates every campaign, writes the data contract, freezes
   its CHANGES.tsv as the approval, and changes nothing on the console;
2. an approved change is applied exactly: the console's own write log,
   EXECUTION_LOG.tsv and the approval agree;
3. a standard the user states is recorded in the store's notes and the
   server's rule check honours it.

The console's access/write log is the ground truth — it cannot be
narrated around. Human-style requests only: no mechanics in the prompts.
"""

import time

import pytest

from app.ai.stop_gates import ad_changes
from app.ai.stop_gates.ad_rules import resolve_rules
from app.ai.stop_gates.ad_tables import (
    CAMPAIGN_COLUMNS,
    CHANGE_COLUMNS,
    EXECUTION_COLUMNS,
    read_table,
)
from app.browser.manager import store_slug
from app.config import VIBE_SELLER_DIR
from tests.e2e.conftest import BASE_URL
from tests.e2e.e2e_helpers import (
    PIPELINE_TIMEOUT,
    create_store,
    create_task,
    poll_task_status,
)
from tests.e2e.fake_ads_console import (
    CAMPAIGN_A,
    CAMPAIGN_B,
    CAMPAIGN_C,
    serve,
)

pytestmark = [pytest.mark.e2e]

SELLER_FACTS = (
    'For context: we make about 40% margin, and the goal is profitable growth.'
)


@pytest.fixture
def console():
    server = serve(paged=True)
    yield server
    server.shutdown()
    server.server_close()


def _amazon_store(api_client, prefix: str) -> dict:
    """An Amazon US store, as a seller sets one up in Settings.

    The console is a local stand-in with no marketplace branding, so the
    store's own record is what tells the agent these are Amazon ads —
    the way a real store's configured platform does.
    """
    return create_store(
        api_client,
        f'{prefix}-{int(time.time())}',
        platform_countries={'amazon': ['US']},
    )


def _task_dir(task_id: str):
    return VIBE_SELLER_DIR / 'tasks' / task_id


def _finish(api_client, task_id: str) -> dict:
    done = poll_task_status(
        api_client,
        task_id,
        {'completed', 'failed'},
        fail_statuses=set(),
        timeout=PIPELINE_TIMEOUT * 2,
    )
    assert done['status'] == 'completed', done.get('error')
    return done


def _say(api_client, task_id: str, text: str) -> None:
    r = api_client.post(
        f'{BASE_URL}/api/tasks/{task_id}/messages', json={'content': text}
    )
    r.raise_for_status()


def _rows(task_id: str, name: str, columns) -> list[dict]:
    table = read_table(_task_dir(task_id) / name, columns)
    assert table is not None, f'{name} was not written'
    assert table.error is None, table.error
    return list(table.rows)


def test_audit_then_apply_exactly_what_was_approved(api_client, console):
    store = _amazon_store(api_client, 'e2e-adtsv')

    # ── 1. Audit: everything enumerated, nothing changed ────────────
    task = create_task(
        api_client,
        'Audit our ad campaigns and tell me what to change',
        store_id=store['id'],
        description=(
            f'Our ad console is at {console.base}. Look at every campaign '
            'and its keywords and tell me which bids you would change. '
            f'Do not change anything yet. {SELLER_FACTS}'
        ),
    )
    _finish(api_client, task['id'])
    listed = {
        r['campaign_id']
        for r in _rows(task['id'], 'CAMPAIGNS.tsv', CAMPAIGN_COLUMNS)
    }
    assert {CAMPAIGN_A, CAMPAIGN_B, CAMPAIGN_C} <= listed, (
        f'campaign list read incompletely (Load more?): {sorted(listed)}'
    )
    _rows(task['id'], 'CHANGES.tsv', CHANGE_COLUMNS)
    assert ad_changes.approved_path(task['id']).exists(), (
        'accepted audit did not freeze its CHANGES.tsv'
    )
    assert console.writes == [], f'audit changed the console: {console.writes}'

    # ── 2. Apply one approved change, exactly ───────────────────────
    _say(
        api_client,
        task['id'],
        'Thanks. Of all that, only do one thing: lower the bid on '
        '"widget red" to 1.35. Leave everything else as it is.',
    )
    _finish(api_client, task['id'])
    _say(api_client, task['id'], 'Looks right — go ahead and apply it.')
    _finish(api_client, task['id'])

    assert [(w['keyword'], w['to']) for w in console.writes] == [
        ('widget red', '1.35')
    ], console.writes
    executed = _rows(task['id'], 'EXECUTION_LOG.tsv', EXECUTION_COLUMNS)
    assert [(r['target'], r['to_bid']) for r in executed] == [
        ('widget red', '1.35')
    ], executed
    assert ad_changes.check('', task['id']) is None


def test_a_stated_standard_is_recorded_and_honoured(api_client, console):
    store = _amazon_store(api_client, 'e2e-adrule')
    task = create_task(
        api_client,
        'Lower the gizmo mini bid',
        store_id=store['id'],
        description=(
            f'Our ad console is at {console.base}. Our standard: a keyword is '
            'only protected from bid cuts when its ACOS is under 20%. '
            'Please remember that for the future. With that in mind, '
            'propose lowering the "gizmo mini" bid to 1.10 — just the '
            'proposal, do not change it yet.'
        ),
    )
    _finish(api_client, task['id'])

    notes = (
        VIBE_SELLER_DIR
        / 'stores'
        / store_slug(store['name'], store['id'])
        / 'notes.md'
    ).read_text(encoding='utf-8')
    rules = resolve_rules(notes)
    assert rules['acos_no_lower'] == 20, notes
    proposals = [
        r
        for r in _rows(task['id'], 'CHANGES.tsv', CHANGE_COLUMNS)
        if r['target'] == 'gizmo mini'
    ]
    assert proposals and proposals[0]['action'] == 'lower', proposals
    # Under the default 30% this lower is refused; the store's own 20%
    # standard is what lets it stand.
    assert ad_changes.rule_violations(tuple(proposals), rules, None) == []
    assert ad_changes.rule_violations(
        tuple(proposals), resolve_rules(None), None
    ), 'fixture no longer exercises the override'
    assert console.writes == []
