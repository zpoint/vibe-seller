"""Ziniao concurrency + recovery e2e tests — LOCAL ONLY (never in CI).

Pins the invariants established while root-causing the multi-store
"browsers kill each other" outage (see docs/ziniao-concurrency.md):

  1. A per-store stale launch must NOT restart the shared Ziniao client
     (a global kill destroys every OTHER store's live browser → cascade).
  2. A scheduled all-store fan-out must survive `./restart.sh --dev`
     (tasks re-queue / resume, not stick FAILED forever).
  3. Killing the Ziniao client must auto-relaunch it on the next
     browser start (ensure_ziniao_running).

Requires a running server on 7777 and a real Ziniao client + at least
two Ziniao-backed stores configured. These are heavy, stateful, and
depend on live Ziniao, so they are gated behind --e2e AND -m ziniao and
are NEVER run in CI.

Run locally:
    ./start.sh 7777
    pytest tests/e2e/test_ziniao_concurrency_recovery.py --e2e -v -m ziniao
"""

import logging
from pathlib import Path
import platform
import shutil
import subprocess
import time

import httpx
import pytest

from tests.e2e.e2e_helpers import BASE_URL, login

logger = logging.getLogger(__name__)
pytestmark = [pytest.mark.e2e, pytest.mark.ziniao]

BACKEND_LOG = Path('logs/backend_7777.log')
# The log strings that mark a SHARED-CLIENT restart. After the fix these
# must never be emitted by per-store browser starts — only by an explicit
# user-initiated force-restart.
GLOBAL_KILL_MARKERS = (
    'Killing Ziniao for WebDriver relaunch',
    'SIGKILL',
    'force_kill_ziniao',
)


def _client() -> httpx.Client:
    c = httpx.Client(base_url=BASE_URL, timeout=120, trust_env=False)
    login(c)
    return c


def _ziniao_store_ids(client: httpx.Client) -> list[str]:
    stores = client.get('/api/stores').json()
    return [s['id'] for s in stores if s.get('browser_backend') == 'ziniao']


def _log_len() -> int:
    return BACKEND_LOG.stat().st_size if BACKEND_LOG.exists() else 0


def _log_since(offset: int) -> str:
    if not BACKEND_LOG.exists():
        return ''
    with BACKEND_LOG.open('rb') as f:
        f.seek(offset)
        return f.read().decode('utf-8', 'ignore')


def _proxy_ok(client: httpx.Client, store_id: str) -> bool:
    """Is this store's CDP proxy serving (browser actually up)?"""
    r = client.get(f'/api/stores/{store_id}')
    sess = r.json().get('browser_session') or {}
    return sess.get('status') == 'running'


@pytest.fixture
def client():
    try:
        c = _client()
    except Exception as e:  # noqa: BLE001
        pytest.skip(
            f'server not reachable / login failed at {BASE_URL} '
            f'(these are local-only tests, run against a dev server): {e}'
        )
    yield c
    c.close()


def test_per_store_stale_never_global_kills(client):
    """Concurrent multi-store browser starts must recover per-store — a
    stale launch in one store must never fire a global Ziniao kill (which
    would tear down peers). This is the core fix."""
    store_ids = _ziniao_store_ids(client)
    if len(store_ids) < 2:
        pytest.skip('need >=2 Ziniao stores for the concurrency invariant')

    offset = _log_len()

    # Fire all store browser starts concurrently (the fan-out pattern).
    def _start(sid: str):
        try:
            return client.post(
                f'/api/stores/{sid}/browser/start',
                params={'force': 1},
                timeout=180,
            ).status_code
        except Exception as e:  # noqa: BLE001
            return repr(e)

    import concurrent.futures as cf

    with cf.ThreadPoolExecutor(max_workers=len(store_ids)) as ex:
        list(ex.map(_start, store_ids))

    window = _log_since(offset)
    # THE invariant: no shared-client kill happened during per-store starts.
    for marker in GLOBAL_KILL_MARKERS:
        assert marker not in window, (
            f'per-store browser start triggered a GLOBAL Ziniao kill '
            f'({marker!r}) — this cascades and kills peer stores. '
            f'Recovery must be per-store (stopBrowser + retry).'
        )


# NOTE on restart-resume: an all-store fan-out surviving `./restart.sh`
# (running tasks re-queued, PLANNED scheduled tasks re-enqueued, none stuck
# FAILED('server_restart') forever) is the invariant from PR #41. It is
# pinned by UNIT tests — tests/unit/test_task_queue.py
# (test_planned_scheduled_re_enqueued, test_running_marked_failed, …) — and
# is intentionally NOT reproduced here: an in-process `./restart.sh` tears
# down the very server the test runs against (and its own harness), so it
# can't assert reliably in-process. Run it by hand if you want the live
# check: trigger the schedule, `./restart.sh --dev`, then confirm no task
# is stuck RUNNING.


def _ziniao_running() -> bool:
    """True if a Ziniao client process is alive (posix `pgrep`)."""
    return (
        subprocess.run(
            ['pgrep', '-f', 'ziniao'], capture_output=True
        ).returncode
        == 0
    )


def test_kill_ziniao_auto_relaunches(client):
    """Killing the Ziniao client must not wedge the platform: the next
    browser start auto-relaunches the client (ensure_ziniao_running).

    Asserts the *client process* comes back — not that the browser fully
    launches, since Ziniao's startBrowser is nondeterministically flaky
    (docs/ziniao-concurrency.md) and per-store retry owns that."""
    if platform.system() not in ('Darwin', 'Linux'):
        pytest.skip('kill step uses posix pkill/pgrep')
    if not shutil.which('pkill') or not shutil.which('pgrep'):
        pytest.skip('pkill/pgrep not available')
    store_ids = _ziniao_store_ids(client)
    if not store_ids:
        pytest.skip('no Ziniao store configured')
    sid = store_ids[0]

    # Bring the client up.
    client.post(
        f'/api/stores/{sid}/browser/start', params={'force': 1}, timeout=180
    )
    # Kill the whole Ziniao client out from under it.
    subprocess.run(['pkill', '-9', '-f', 'ziniao'], capture_output=True)
    time.sleep(3)
    assert not _ziniao_running(), 'Ziniao should be dead right after kill'

    # Next start must auto-relaunch the client (browser launch itself may
    # flake — that is retried per store, not asserted here).
    try:
        client.post(
            f'/api/stores/{sid}/browser/start',
            params={'force': 1},
            timeout=180,
        )
    except Exception:  # noqa: BLE001
        pass
    assert _ziniao_running(), (
        'Ziniao client was not auto-relaunched after being killed'
    )
