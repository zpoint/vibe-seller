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

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import re
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


@dataclass
class SubagentRun:
    """What one subagent did, read out of its own transcript."""

    name: str
    slots: set[int]
    first: datetime
    last: datetime


def _subagent_runs(task_id: str) -> list[SubagentRun]:
    """Per-subagent slot usage and activity span.

    Claude Code writes one JSONL per subagent under the parent
    session's ``subagents/`` dir; that is the only place a subagent's
    own timeline exists — the parent transcript records the spawn, not
    the calls.

    The slot NUMBER comes straight out of the command the subagent ran,
    which attributes it to a client exactly. An earlier version tried
    to infer the mapping from overlapping time windows alone; that
    cannot work, because concurrent subagents overlap each other by
    definition.
    """
    root = Path.home() / '.claude' / 'projects'
    runs: list[SubagentRun] = []
    for tdir in root.glob(f'*{task_id}*'):
        for sub in tdir.glob('*/subagents/*.jsonl'):
            slots: set[int] = set()
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
                    if not (
                        isinstance(c, dict)
                        and c.get('type') == 'tool_use'
                        and c.get('name') == 'Bash'
                    ):
                        continue
                    cmd = str(c.get('input', {}).get('command', ''))
                    found = re.findall(r'--worker[= ](\d+)', cmd)
                    if not found:
                        continue
                    slots.update(int(x) for x in found)
                    # Transcript stamps are UTC ("…Z"); the server log
                    # is LOCAL. Comparing them naively is a silent
                    # timezone-wide offset that breaks the join on every
                    # box not on UTC.
                    raw = d.get('timestamp', '').replace('Z', '+00:00')
                    try:
                        stamps.append(
                            datetime.fromisoformat(raw)
                            .astimezone()
                            .replace(tzinfo=None)
                        )
                    except ValueError:
                        pass
            if slots and stamps:
                runs.append(
                    SubagentRun(sub.stem, slots, min(stamps), max(stamps))
                )
    return runs


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
        # Which slot each subagent used, from its OWN transcript. This
        # is the attribution — not an inference from "nobody else used
        # the flag", which stops holding as soon as there are two.
        runs = _subagent_runs(task['id'])
        assert len(runs) >= 2, (
            f'expected at least two browser-driving subagents, got '
            f'{[r.name for r in runs]}'
        )
        for r in runs:
            assert len(r.slots) == 1, (
                f'subagent {r.name} used slots {sorted(r.slots)} — a '
                'subagent must stay on the one slot it was given'
            )
        used = [next(iter(r.slots)) for r in runs]
        assert len(set(used)) == len(used), (
            f'two subagents shared a slot: {used} — the parent must hand '
            'out distinct numbers'
        )

        fam = assert_slots_isolated(trace, task['id'], set(used))

        # And each of those slots was genuinely live while its subagent
        # was calling. The call PRECEDES its client's connect (the call
        # is what boots the daemon), so allow lead-in for startup rather
        # than requiring containment.
        by_slot = {c.slot: c for c in fam.values() if c.slot is not None}
        startup = timedelta(seconds=180)
        for r in runs:
            slot = next(iter(r.slots))
            w = by_slot[slot].window
            assert w and w[0] - startup <= r.last and r.first <= w[1], (
                f'subagent {r.name} ran --worker {slot} at '
                f'{r.first:%H:%M:%S}..{r.last:%H:%M:%S} but client '
                f'-w{slot} was only active {w[0]:%H:%M:%S}..{w[1]:%H:%M:%S}'
            )

        # Concurrency, not a sequence of solo runs.
        assert trace.concurrent_at_any_instant(list(fam)), (
            f'clients never overlapped: '
            f'{ {k: v.window for k, v in fam.items()} }'
        )
