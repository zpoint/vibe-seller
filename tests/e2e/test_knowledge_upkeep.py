"""E2E: agents keep store knowledge correct during the task itself.

There is no post-task reflection phase any more; the base prompt tells
the agent to rewrite a note the live page contradicts, in place, and to
leave knowledge alone when a task taught it nothing. These run a real
model against a local page and a seeded store notes file.
"""

import http.server
from pathlib import Path
import threading
import time

import pytest

from app.browser.manager import store_slug
from app.config import VIBE_SELLER_DIR
from tests.e2e.e2e_helpers import create_store, create_task, poll_task_status

pytestmark = [pytest.mark.e2e]

STORES_DIR = VIBE_SELLER_DIR / 'stores'
OLD_EMAIL = 'tickets-old@acme-widgets.test'
LIVE_EMAIL = 'help-desk@acme-widgets.test'

_PAGES = {
    '/': (
        '<html><body><h1>Acme Widgets</h1>'
        '<a href="/support">Support</a></body></html>'
    ),
    '/support': (
        '<html><body><h1>Support</h1>'
        f'<p>Open a ticket by emailing <b>{LIVE_EMAIL}</b>.</p>'
        '</body></html>'
    ),
}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 -- http.server API
        body = _PAGES.get(self.path)
        self.send_response(200 if body else 404)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write((body or 'not found').encode())

    def log_message(self, *args):
        pass


@pytest.fixture(scope='module')
def site():
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{server.server_address[1]}'
    server.shutdown()
    server.server_close()


def _seed_store(api_client, name: str, site: str) -> tuple[dict, Path]:
    """A store whose notes say where tickets go — wrongly."""
    store = create_store(api_client, name)
    root = STORES_DIR / store_slug(store['name'], store['id'])
    root.mkdir(parents=True, exist_ok=True)
    notes = root / 'notes.md'
    notes.write_text(
        '# Store notes\n\n'
        '## Support\n\n'
        f'- Our site is {site}; the support page lists where tickets '
        f'go: {OLD_EMAIL}\n'
    )
    (root / 'CATALOG.md').write_text(
        '# Store Knowledge Catalog (L3)\n\n'
        '| File | Relevance | Summary |\n|---|---|---|\n'
        f'| stores/{root.name}/notes.md | | Store notes: our website and '
        'where support tickets go |\n'
    )
    return store, notes


def _finish(api_client, task: dict) -> None:
    done = poll_task_status(api_client, task['id'], {'completed', 'failed'})
    assert done['status'] == 'completed', done.get('error')


def test_stale_note_is_rewritten_in_place(api_client, site):
    store, notes = _seed_store(
        api_client, f'e2e-knowledge-fix-{int(time.time())}', site
    )
    _finish(
        api_client,
        create_task(
            api_client,
            'Which email address do support tickets go to on our site? '
            'I need to send one today.',
            store_id=store['id'],
        ),
    )
    text = notes.read_text()
    assert LIVE_EMAIL in text, text
    assert OLD_EMAIL not in text, (
        'the contradicted note is still there (appended, not rewritten):\n'
        + text
    )
    assert text.count(LIVE_EMAIL) == 1, text


def test_task_that_learns_nothing_leaves_knowledge_alone(api_client, site):
    store, notes = _seed_store(
        api_client, f'e2e-knowledge-keep-{int(time.time())}', site
    )

    def knowledge():
        return {p.name: p.read_text() for p in notes.parent.glob('*.md')}

    before = knowledge()
    _finish(
        api_client,
        create_task(
            api_client,
            'What is 17 times 23? Reply with just the number.',
            store_id=store['id'],
        ),
    )
    assert knowledge() == before
