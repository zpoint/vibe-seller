"""A real agent, real subagents, real slots — the half tests can't fake.

``tests/integration/test_worker_slot_isolation.py`` proves the wrapper
turns ``--worker N`` into isolated tabs on a real browser. It cannot
prove the other half: that a real LLM agent, handed nothing but human
intent, actually *reaches for the flag* when it fans work out.

That half lives in the prompt block (``PARALLEL_WORKER_PROMPT`` in
``app/task_runner_context.py``) and the browser-harness skill. Prompt
guidance is the easiest thing in this codebase to regress silently —
reword the block, and concurrent subagents quietly go back to sharing
one tab and returning each other's pages. So **zero slots used is a
failure here**, not a skip: it is the regression this test exists for.

The task text is pure human intent by design. Naming ``--worker`` in it
would test nothing but the agent's ability to copy a flag.
"""

from datetime import datetime
import json
import logging
from pathlib import Path
import time

import pytest

from tests.e2e.click_lab import page_code, start_click_lab
from tests.e2e.e2e_helpers import (
    BASE_URL,
    create_store,
    create_task,
    poll_task_status,
)
from tests.e2e.mux_probe import assert_slots_isolated, parse

logger = logging.getLogger(__name__)
pytestmark = [pytest.mark.e2e]

SERVER_LOG = Path('logs/backend.log')

# JS-rendered pages, not static HTML. The first version of this test
# served the code in the markup and the agent read it with `curl` —
# correctly, since nothing about an unauthenticated static page needs a
# browser. The run then failed the slot assertion having never opened a
# browser at all. See tests/e2e/click_lab.py.
PAGES = (1, 2, 3)


@pytest.fixture(scope='module')
def site():
    base, srv = start_click_lab()
    yield base
    srv.shutdown()
    srv.server_close()


@pytest.fixture(scope='module')
def chrome_store(api_client):
    ts = int(time.time())
    slug = f'slots-e2e-{ts}'
    store = create_store(api_client, slug, browser_backend='chrome')
    try:
        yield {'id': store['id'], 'slug': slug}
    finally:
        try:
            api_client.delete(
                f'{BASE_URL}/api/stores/{store["id"]}', timeout=60
            )
        except Exception:
            logger.warning('could not delete e2e store %s', store['id'])


def _subagent_bash_times(task_id: str) -> dict[str, list[datetime]]:
    """When each subagent issued a browser-use call, from its transcript.

    Claude Code writes one JSONL per subagent under the parent session's
    ``subagents/`` dir; that is the only place a subagent's own timeline
    exists (the parent transcript records the spawn, not the calls).
    """
    root = Path.home() / '.claude' / 'projects'
    out: dict[str, list[datetime]] = {}
    for tdir in root.glob(f'*{task_id}*'):
        for sub in tdir.glob('*/subagents/*.jsonl'):
            stamps: list[datetime] = []
            for line in sub.read_text(errors='replace').splitlines():
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                msg = d.get('message') or {}
                content = msg.get('content')
                if not isinstance(content, list):
                    continue
                for c in content:
                    if (
                        isinstance(c, dict)
                        and c.get('type') == 'tool_use'
                        and c.get('name') == 'Bash'
                        and '--worker'
                        in str(c.get('input', {}).get('command', ''))
                    ):
                        raw = d.get('timestamp', '').replace('Z', '+00:00')
                        try:
                            stamps.append(
                                datetime.fromisoformat(raw).replace(tzinfo=None)
                            )
                        except ValueError:
                            pass
            if stamps:
                out[sub.stem] = sorted(stamps)
    return out


@pytest.mark.e2e
class TestParallelSubagentSlots:
    def test_agent_gives_concurrent_subagents_distinct_slots(
        self, api_client, chrome_store, site
    ):
        """Human intent in, isolated parallel browsing out."""
        if not SERVER_LOG.exists():
            pytest.skip(f'server log not readable at {SERVER_LOG}')
        started = datetime.now()

        task = create_task(
            api_client,
            title='Read all three reports at once',
            store_id=chrome_store['id'],
            description=(
                'I am in a hurry and need all three of these read at the '
                'same time, not one after another — put a helper on each '
                'of the other two while you take the first yourself:\n'
                + '\n'.join(f'  {p}) {site}/report/{p}' for p in PAGES)
                + '\n\nThey are JavaScript dashboards, so open them in the '
                'browser — the reference code is not in the page source. '
                'Report all three codes, saying which page each came from.'
            ),
            plan_mode=False,
        )
        result = poll_task_status(
            api_client, task['id'], {'completed', 'failed'}
        )
        assert result['status'] == 'completed', (
            f'task failed: {result.get("error")}'
        )

        # Every code present, so no driver lost or swapped its page.
        text = result.get('result') or ''
        for p in PAGES:
            assert page_code(p) in text, (
                f'code {page_code(p)} (/report/{p}) missing from result — a '
                f'driver did not read its own page. Result: {text[:600]}'
            )

        trace = parse(SERVER_LOG, since=started)
        # Separate the two ways this can go wrong. No client AT ALL means
        # the agent never opened a browser (a broken premise — the pages
        # must be unreadable without one), which is a different bug from
        # "browsed, but concurrently on one tab".
        assert trace.for_task(task['id']), (
            'the agent completed the task without ever connecting to the '
            'browser — the pages are supposed to be JS-rendered and '
            'unreadable by curl; fix the fixture, not the prompt'
        )
        fam = assert_slots_isolated(trace, task['id'], expected_slots={1, 2})

        # Timestamp join: a subagent's own calls must land inside the
        # activity window of a slot client. Without this the mapping
        # "subagent -> -wN" is only inferred from "nobody else used the
        # flag", which stops holding the moment there are two of them.
        slot_windows = {
            cid: c.window for cid, c in fam.items() if c.slot is not None
        }
        for name, stamps in _subagent_bash_times(task['id']).items():
            assert any(
                w and w[0] <= stamps[-1] and stamps[0] <= w[1]
                for w in slot_windows.values()
            ), (
                f'subagent {name} made --worker calls at '
                f'{stamps[0]}..{stamps[-1]} but no slot client was active '
                f'then; windows={slot_windows}'
            )

        # Concurrency, not a sequence of solo runs.
        assert trace.concurrent_at_any_instant(list(fam)), (
            f'clients never overlapped: '
            f'{ {k: v.window for k, v in fam.items()} }'
        )
