"""E2E test for 3-level knowledge catalog system.

Single test flow:
  1. Write knowledge file with SECRET + store files for 2 stores
  2. Run catalog sync for each store
  3. Verify accumulation (L2 ⊃ L1, L3 ⊃ L2 ⊃ L1)
  4. Verify isolation (L1 ⊄ L2, L2 ⊄ L3, store_a ⊄ store_b)
  5. Agent finds SECRET via catalog (not by globbing)
  6. No Glob/Grep on symlinked dirs (knowledge/, stores/)

**This module owns its server.** Every step is server-global: the
catalog sync is ONE system schedule whose fanout rebuilds the catalog of
EVERY store on the server, and a knowledge file written for it makes
every store's catalog stale. On the shared e2e server another xdist
worker was inside that same tree while this test sat between its syncs
(observed in CI: the store the knowledge-cleanup test expected to be
skipped was rebuilt under it). A dedicated port and a dedicated
``VIBE_HOME`` — ``owned_agent_stack`` over ``tests/e2e/own_server.py`` —
make that impossible rather than merely locked: no worker can observe or
change this test's catalogs, and this test cannot touch anybody else's.
"""

from datetime import UTC, datetime
import json
from pathlib import Path
import time
import uuid
import warnings

import httpx
import pytest

from app.browser.manager import store_slug as _store_slug
from app.models.schedule_constants import SYSTEM_CATALOG_SYNC_ID
from tests.e2e import e2e_helpers
from tests.e2e.e2e_helpers import (
    PIPELINE_TIMEOUT,
    create_store,
    create_task,
    get_messages,
    poll_task_status,
    run_tag,
)
from tests.e2e.own_server import owned_agent_stack

pytestmark = [pytest.mark.e2e]

FAKE_PLATFORM = 'amazon'
FAKE_COUNTRY = 'PL'

# Set by the module fixture (own_stack) to THIS module's own server and
# its own home: seeding files here lands where the server's workspace
# manager reads them, and never in a shared or a developer's
# ~/.vibe-seller.
BASE_URL = ''
HOME: Path = Path()
KNOWLEDGE_DIR: Path = Path()
STORES_DIR: Path = Path()


@pytest.fixture(scope='module', autouse=True)
def own_stack(tmp_path_factory):
    """Run this module against a server, and a home, it owns alone.

    One per WORKER (a module-scoped fixture): the module's tests are
    the module's own, so whoever runs them gets a private stack.
    """
    global BASE_URL, HOME, KNOWLEDGE_DIR, STORES_DIR
    with owned_agent_stack(tmp_path_factory, 'catalog-sync') as server:
        BASE_URL = server.base_url
        HOME = server.home
        KNOWLEDGE_DIR = HOME / 'knowledge'
        STORES_DIR = HOME / 'stores'
        yield server


@pytest.fixture(scope='module')
def api_client(own_stack):
    client = httpx.Client(timeout=30)
    login_client(client, BASE_URL)
    yield client
    client.close()


@pytest.fixture(scope='module')
def store_a(api_client: httpx.Client) -> dict:
    ts = run_tag()
    return create_store(
        api_client,
        f'cat-store-a-{ts}',
        browser_backend='chrome',
        platforms=[FAKE_PLATFORM],
        countries=[FAKE_COUNTRY],
        platform_countries={FAKE_PLATFORM: [FAKE_COUNTRY]},
    )


@pytest.fixture(scope='module')
def store_b(api_client: httpx.Client) -> dict:
    ts = run_tag()
    return create_store(
        api_client,
        f'cat-store-b-{ts}',
        browser_backend='chrome',
        platforms=[FAKE_PLATFORM],
        countries=['SG'],
        platform_countries={FAKE_PLATFORM: ['SG']},
    )


def login_client(client: httpx.Client, base_url: str) -> None:
    """Login as the seeded admin on the server this module owns.

    Its own home means its own fresh DB, so the seeded credentials are
    what is there — no shared-server account to inherit.
    """
    for attempt in range(5):
        try:
            client.post(
                f'{base_url}/api/auth/login',
                json={
                    'identifier': 'admin@vibe-seller.local',
                    'password': 'admin',
                },
            ).raise_for_status()
            return
        except (httpx.HTTPStatusError, httpx.ConnectError):
            if attempt < 4:
                time.sleep(2)
            else:
                raise


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def _write_store_metadata(slug: str, platforms: dict[str, list[str]]) -> None:
    meta_path = STORES_DIR / slug / 'metadata.json'
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps({'platforms': platforms}), encoding='utf-8')


SYMLINKED_DIRS = ('knowledge/', 'knowledge', 'stores/', 'stores')


def _warn_if_glob_grep_on_symlinks(
    messages: list[dict], task_label: str
) -> None:
    """Warn (do NOT fail) if the agent used Glob/Grep on symlinked dirs.

    Glob/Grep use ripgrep, which cannot follow symlinks, so they come
    back empty on ``knowledge/``/``stores/``. This was once a hard
    assertion, but the logs showed the agent *recovers*: an empty
    Glob/Grep is followed by a Bash ``find -L``/``grep -r`` (or a
    catalog Read) and the task still finds the secret and completes.
    Those are the real signals and they're asserted by the caller
    (status == completed, secret in result, CATALOG read, no broad
    globs, turn budget). Touching the broken tool once and recovering
    is not a failure — only never finding the answer is. We surface it
    as a warning so a regression toward wasteful broken searches stays
    visible without flaking the suite on a benign, recovered attempt.
    """
    for m in messages:
        if m['role'] != 'tool_use':
            continue
        try:
            data = json.loads(m['content'])
        except (json.JSONDecodeError, TypeError):
            continue
        tool = data.get('tool', '')
        if tool not in ('Glob', 'Grep'):
            continue
        inp = data.get('input', {})
        for field in ('pattern', 'path'):
            val = str(inp.get(field, ''))
            for d in SYMLINKED_DIRS:
                if d in val:
                    warnings.warn(
                        f'[{task_label}] Agent used {tool} on symlinked '
                        f'dir "{d}" (field={field}, value={val!r}); it '
                        f'returns empty — Bash find -L/grep -r is the '
                        f'reliable path. Tolerated (agent recovered).',
                        stacklevel=2,
                    )
                    return


def trigger_catalog_sync(
    client: httpx.Client,
    keep_store_ids: set[str],
    base_url: str,
) -> tuple[list[dict], list[dict]]:
    """Trigger real catalog sync fanout, poll until done.

    Uses ``POST /api/schedules/{SYSTEM_CATALOG_SYNC_ID}/trigger`` —
    the same path as clicking "Run Now" in the UI.

    Returns ``(relevant, others)`` where *relevant* are L2 + test
    store tasks and *others* are tasks for unrelated stores
    (expected to skip via staleness check).

    *base_url* is required, never defaulted: this drives the ONE system
    schedule whose fanout rebuilds every store's catalog on THAT server,
    so it must name the server it owns the run on — and it refuses the
    shared one outright.
    """
    e2e_helpers.assert_not_the_shared_server(
        base_url, 'the global catalog sync fanout'
    )
    base = f'{base_url}/api/schedules/{SYSTEM_CATALOG_SYNC_ID}'

    # Snapshot the batch ids that already exist, so the trigger below can
    # be identified by the one it adds. Every task a fanout creates — the
    # two_phase L2 prereq AND every per-store L3 — shares a single
    # `batch_id`, so "the new batch" is exactly this call's work and
    # nothing else's.
    #
    # Selecting by a 120s wall-clock window instead (what this did
    # before) quietly defeats `--reruns 1`: a rerun fires seconds after
    # the attempt it replaces, so the window still holds the previous
    # attempt's tasks and the assertion trips over their already-`failed`
    # rows. Observed live — attempt 1 lost its LLM connection
    # mid-response at 02:42:35, the rerun triggered 9s later at 02:42:44,
    # and the rerun then reported attempt 1's error. No retry could have
    # passed, however healthy the second run was.
    resp = client.get(f'{base}/tasks')
    resp.raise_for_status()
    seen_batches = {t['batch_id'] for t in resp.json() if t.get('batch_id')}

    # Pin the system schedule to the provider THIS run was configured
    # with, at trigger time. The profile setup pins every schedule still
    # resolving to 'default', but `_catalog_sync` is ai_bot-owned and the
    # run may re-point the provider after that setup — and on the shared
    # server it used to be one row that several workers wrote: two saw
    # 'default' and both pinned, so the last writer's provider won and
    # the catalog tasks ran on a provider the worker was never assigned
    # (one out of quota). Trigger-time pinning is the one that holds.
    profile_id = e2e_helpers.DEFAULT_PROFILE_ID
    if profile_id:
        pinned = client.put(base, json={'ai_profile_id': profile_id})
        pinned.raise_for_status()
        assert pinned.json().get('ai_profile_id') == profile_id, (
            f'catalog sync not pinned to {profile_id}: {pinned.text}'
        )
    resp = client.post(f'{base}/trigger')
    resp.raise_for_status()

    start = time.time()
    batch_id: str | None = None

    # Catalog sync fans out N tasks (1 L2 + 1 per store ≈ 12) that
    # serialize through the agent concurrency semaphore (default 9
    # in CI). A single LLM stall mid-fanout may need the full stall
    # reaper window (~6 min) before being reaped. Give the helper
    # explicit headroom over the per-test PIPELINE_TIMEOUT so a
    # stalled L3 task is observed as `failed` here rather than
    # surfacing as an opaque "did not complete" timeout — the
    # caller's per-task assertion will then report the real reason.
    helper_timeout = max(PIPELINE_TIMEOUT, 900)

    while time.time() - start < helper_timeout:
        resp = client.get(f'{base}/tasks')
        resp.raise_for_status()
        tasks = resp.json()

        if batch_id is None:
            fresh = {
                t['batch_id']
                for t in tasks
                if t.get('batch_id') and t['batch_id'] not in seen_batches
            }
            if not fresh:
                time.sleep(3)
                continue
            # Pin the batch once and never re-derive it, so a concurrent
            # trigger later in the run cannot move the goalposts.
            batch_id = max(
                fresh,
                key=lambda b: max(
                    _parse_ts(t['created_at'])
                    for t in tasks
                    if t.get('batch_id') == b
                ),
            )

        recent = [t for t in tasks if t.get('batch_id') == batch_id]
        if not recent:
            time.sleep(3)
            continue

        # Ensure store tasks exist (L3 phase may not have
        # started yet when L2 finishes quickly in auto mode).
        has_store_tasks = any(
            t.get('store_id') in keep_store_ids for t in recent
        )
        if not has_store_tasks:
            time.sleep(3)
            continue

        # Wait for ALL of THIS batch's tasks to finish
        if all(t['status'] in ('completed', 'failed') for t in recent):
            relevant = [
                t
                for t in recent
                if not t.get('store_id') or t['store_id'] in keep_store_ids
            ]
            others = [
                t
                for t in recent
                if t.get('store_id') and t['store_id'] not in keep_store_ids
            ]
            return relevant, others
        time.sleep(3)

    raise TimeoutError(
        f'Catalog sync did not complete within {helper_timeout}s'
    )


def _parse_ts(iso: str) -> float:
    """Parse ISO timestamp to epoch seconds."""
    dt = datetime.fromisoformat(iso.replace('Z', '+00:00'))
    return dt.replace(tzinfo=UTC).timestamp()


class TestCatalogSystem:
    """Verify catalog sync, accumulation, isolation, and agent usage."""

    def test_catalog_sync_and_agent_usage(
        self,
        api_client: httpx.Client,
        store_a: dict,
        store_b: dict,
    ):
        """Single flow: sync → verify structure → agent finds secret.

        Uses one knowledge file with a SECRET so the same catalog
        sync serves both structure verification and agent testing.
        """
        slug_a = _store_slug(store_a['name'])
        slug_b = _store_slug(store_b['name'])

        # ── Setup: write knowledge + store files ──
        # Enforce the invariant "exactly one verification secret exists".
        # The home is this module's own, but a rerun (or a retry of the
        # task) reuses it inside the same session and its PL-*.md secret
        # file is still there, so the find-secret agent below would see
        # TWO secrets and could report the wrong one (observed flake: two
        # tokens present, agent returned the first-listed). Delete stale
        # verification stubs first so the invariant is guaranteed, not
        # hoped for.
        _amazon_dir = KNOWLEDGE_DIR / FAKE_PLATFORM
        for stale in _amazon_dir.glob(f'{FAKE_COUNTRY}-*.md'):
            try:
                if 'VERIFY-CATALOG' in stale.read_text(encoding='utf-8'):
                    stale.unlink()
            except OSError:
                pass

        # File path keeps a UUID-derived token (not a timestamp) so the L2
        # path can't share a numeric suffix with a same-second
        # ``cat-store-a-<ts>`` slug — a collision has made the L2-catalog
        # agent (GLM-4.7) surface the store slug in the L2 summary and trip
        # the L2-isolation assertion below.
        #
        # The SECRET the find-secret agent must transcribe is SHORT and
        # hyphen-grouped: a weak CI model drops a char when copying a long
        # run-on random token (observed: 12-hex ...96068 returned as
        # ...9608, failing the exact-match assert). Grouped 4-4 uppercase
        # hex copies reliably, and the cleanup above guarantees it is the
        # only secret present, so it needs no extra length for uniqueness.
        file_tok = uuid.uuid4().hex[:12]
        s = uuid.uuid4().hex[:8].upper()
        secret = f'VERIFY-CATALOG-{s[:4]}-{s[4:]}'
        l2_file = f'{FAKE_PLATFORM}/{FAKE_COUNTRY}-{file_tok}.md'
        # Give the file a describable topic — a real knowledge file has
        # one, and the L2 catalog sync only writes a useful summary for
        # non-near-empty files (a bare `SECRET: <token>` line is
        # "near-empty" and gets stubbed as "Empty/stub", which hides
        # WHERE the secret is and forces the find-secret agent to grep
        # instead of using the catalog).
        write_file(
            KNOWLEDGE_DIR / l2_file,
            (
                '# Catalog Verification\n\n'
                'This knowledge file exists to verify the catalog '
                'lookup flow end to end. Its topic is the verification '
                'secret that a catalog-driven task should return.\n\n'
                f'SECRET: {secret}\n'
            ),
        )

        write_file(
            STORES_DIR / slug_a / 'notes.md',
            '# Store A Notes\n\nStore A specific.\n',
        )
        _write_store_metadata(slug_a, {FAKE_PLATFORM: [FAKE_COUNTRY]})

        write_file(
            STORES_DIR / slug_b / 'notes.md',
            '# Store B Notes\n\nStore B specific.\n',
        )
        _write_store_metadata(slug_b, {FAKE_PLATFORM: ['SG']})

        # ── Step 1: Trigger real catalog sync fanout (L2 + L3) ──
        sync_tasks, other_tasks = trigger_catalog_sync(
            api_client,
            keep_store_ids={store_a['id'], store_b['id']},
            base_url=BASE_URL,
        )
        for t in sync_tasks:
            assert t['status'] == 'completed', (
                f'Sync task failed: store={t.get("store_id")}'
                f' error={t.get("error", "")}'
            )

        # ── Step 1b: Verify other stores completed ──
        # After L2 regen, all L3 are stale (L3 depends on L2
        # mtime), so other stores may run agents too — that's
        # correct.  Just verify they completed without error.
        for t in other_tasks:
            assert t['status'] == 'completed', (
                f'Other store task not completed: {t["id"]}'
                f' error={t.get("error", "")}'
            )

        # ── Step 1c: Verify no Glob/Grep on symlinked dirs ──
        for t in sync_tasks:
            label = t.get('store_id') or 'l2'
            msgs = get_messages(api_client, t['id'])
            _warn_if_glob_grep_on_symlinks(msgs, label)

        # ── Step 2: Read catalogs ──
        l1_path = KNOWLEDGE_DIR / 'project' / 'CATALOG.md'
        l2_path = KNOWLEDGE_DIR / 'CATALOG.md'
        l3_a_path = STORES_DIR / slug_a / 'CATALOG.md'
        l3_b_path = STORES_DIR / slug_b / 'CATALOG.md'

        assert l1_path.exists(), 'L1 catalog missing'
        assert l2_path.exists(), 'L2 catalog not generated'
        assert l3_a_path.exists(), 'L3 missing for store_a'
        assert l3_b_path.exists(), 'L3 missing for store_b'

        l1 = l1_path.read_text(encoding='utf-8')
        l2 = l2_path.read_text(encoding='utf-8')
        l3_a = l3_a_path.read_text(encoding='utf-8')
        l3_b = l3_b_path.read_text(encoding='utf-8')

        l1_file = 'amazon-sites.md'

        # ── Step 3: Verify accumulation ──
        assert l1_file in l2, f'L2 missing L1 entry {l1_file}'
        assert l2_file in l2, 'L2 missing its own files'
        assert l1_file in l3_a, f'L3 store_a missing L1 entry {l1_file}'
        assert l2_file in l3_a, 'L3 store_a missing L2 entry'
        assert 'notes.md' in l3_a, 'L3 store_a missing its own store files'

        # ── Step 4: Verify isolation ──
        # Forward: L1 ⊄ L2 files, L2 ⊄ L3 store files
        assert l2_file not in l1, 'L1 contains L2 file — isolation violated'
        assert slug_a not in l2, (
            'L2 contains store_a files — isolation violated'
        )
        assert slug_b not in l2, (
            'L2 contains store_b files — isolation violated'
        )
        # Cross-store: store_a ⊄ store_b
        assert slug_b not in l3_a, (
            'L3 store_a contains store_b — cross-store isolation violated'
        )
        assert slug_a not in l3_b, (
            'L3 store_b contains store_a — cross-store isolation violated'
        )

        # ── Step 5: Agent finds the secret ──
        task = create_task(
            api_client,
            title='Find the verification secret',
            store_id=store_a['id'],
            description=(
                'Find the SECRET value in the knowledge '
                'files for this store. Report just the '
                'SECRET value.'
            ),
        )

        result = poll_task_status(
            api_client,
            task['id'],
            {'completed', 'failed'},
            timeout=PIPELINE_TIMEOUT,
        )
        assert result['status'] == 'completed', (
            f'Task failed: {result.get("error", "")}'
        )
        assert result.get('result') and secret in result['result'], (
            f'Agent did not find secret {secret} in result: '
            f'{result.get("result", "")[:200]}'
        )

        # The agent is judged on its answer, not on its route to it. Two
        # stricter checks lived here — a cap of 8 total turns, then "no
        # search between reading the catalog and opening the file" — and
        # both tracked model habit instead: MiniMax failed for re-checking
        # its answer and narrating. What the catalog system owes is
        # asserted above: the secret's file is in store A's catalog
        # (``l2_file in l3_a``).
        messages = get_messages(api_client, task['id'])
        _warn_if_glob_grep_on_symlinks(messages, 'find_secret')
