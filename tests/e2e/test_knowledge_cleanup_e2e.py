"""E2E: the first catalog sync after an upgrade removes outdated notes.

A store upgraded from an older harness owes the one-time cleanup
(app/knowledge_contract.py). Its notes mix three kinds of text:

- passages describing how the platform's own checks used to behave
  (a reviewer refusing the first submit, when an approval froze) —
  these must go;
- real store knowledge, including lines that merely share words with
  those mechanics (a "Refused Delivery" return reason, a `key: value`
  standard) — these must stay, word for word;
- unrelated text — this must stay too.

The real catalog sync is triggered (the UI's "Run Now"); afterwards the
outdated passages are gone, everything else is intact, the store is
recorded as clean, and the next catalog sync skips it.
"""

import sqlite3
import time

import httpx
import pytest

from app.browser.manager import store_slug as _store_slug
from app.config import VIBE_SELLER_DIR
from app.knowledge_contract import KNOWLEDGE_CONTRACT, store_scope
from tests.e2e.e2e_helpers import create_store
from tests.e2e.test_catalog_sync import (
    STORES_DIR,
    login_client,
    trigger_catalog_sync,
    write_file,
)

pytestmark = [pytest.mark.e2e]

DB_PATH = VIBE_SELLER_DIR / 'data' / 'vibe_seller.db'

OUTDATED = [
    'After every set_task_result the platform runs a reviewer pass; the '
    'first submit is always refused with "Reviewer never ran" — that is '
    'normal, write REVIEW_<date>_iter1.md and submit again.',
    'The approval freezes when the audit result is accepted, so only the '
    "audit's own target_bid can be executed; a value the user changes "
    'later is refused by the gate.',
]
KEEP = [
    'margin: 40',
    'acos_no_lower: 25',
    'Business Reports live under Reports > Business Reports; pick the '
    'date range before exporting or the export is empty.',
    'Returns marked "Refused Delivery" twice for the same buyer within a '
    "day usually mean an address problem — check the listing's "
    'shipping template first.',
    'The warehouse closes for inbound deliveries on Fridays.',
]


def _notes() -> str:
    return (
        '# Notes\n\n'
        '## Standards\n\n'
        f'{KEEP[0]}\n{KEEP[1]}\n\n'
        '## Platform behaviour\n\n'
        f'- {OUTDATED[0]}\n'
        f'- {KEEP[2]}\n'
        f'- {OUTDATED[1]}\n\n'
        '## Operations\n\n'
        f'- {KEEP[3]}\n'
        f'- {KEEP[4]}\n'
    )


def _owe_cleanup(store_id: str) -> None:
    """The state an upgrade leaves: this store's notes predate the
    current contract."""
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(
            'INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)',
            (f'knowledge_contract:{store_scope(store_id)}', '1'),
        )
        con.commit()
    finally:
        con.close()


def _recorded(store_id: str) -> str | None:
    con = sqlite3.connect(DB_PATH)
    try:
        row = con.execute(
            'SELECT value FROM app_settings WHERE key = ?',
            (f'knowledge_contract:{store_scope(store_id)}',),
        ).fetchone()
    finally:
        con.close()
    return row[0] if row else None


def test_first_catalog_sync_after_upgrade_removes_only_outdated_notes():
    client = httpx.Client(timeout=30)
    login_client(client)
    store = create_store(
        client,
        f'kc-store-{int(time.time())}',
        browser_backend='chrome',
        platforms=['amazon'],
        countries=['US'],
        platform_countries={'amazon': ['US']},
    )
    slug = _store_slug(store['name'], store['id'])
    notes = STORES_DIR / slug / 'notes.md'
    write_file(notes, _notes())
    _owe_cleanup(store['id'])

    relevant, _ = trigger_catalog_sync(client, {store['id']})
    task = next(t for t in relevant if t.get('store_id') == store['id'])
    assert task['status'] == 'completed', task

    cleaned = notes.read_text(encoding='utf-8')
    for passage in OUTDATED:
        assert passage not in cleaned, f'outdated passage kept:\n{cleaned}'
    for line in KEEP:
        assert line in cleaned, f'real knowledge lost: {line!r}\n{cleaned}'
    assert _recorded(store['id']) == str(KNOWLEDGE_CONTRACT)

    # Once only: the next catalog sync leaves this store alone.
    relevant, _ = trigger_catalog_sync(client, {store['id']})
    again = next(t for t in relevant if t.get('store_id') == store['id'])
    assert again.get('result') == 'L3 catalog already up-to-date', again
    assert notes.read_text(encoding='utf-8') == cleaned
