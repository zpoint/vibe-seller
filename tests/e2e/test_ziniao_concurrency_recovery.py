"""Ziniao concurrency + recovery e2e tests — LOCAL ONLY (never in CI).

Pins the invariants established while root-causing the multi-store
"browsers kill each other" outage (see docs/ziniao-concurrency.md):

  1. A per-store stale launch must NOT restart the shared Ziniao client
     (a global kill destroys every OTHER store's live browser → cascade).
  2. A scheduled all-store fan-out must survive `./restart.sh --dev`
     (tasks re-queue / resume, not stick FAILED forever).
  3. Killing the Ziniao client must auto-relaunch it on the next
     browser start (ensure_ziniao_running).
  4. Several browser starts arriving together while Ziniao sits in normal
     mode share ONE kill-all and ONE launch (the startup lock), and all
     succeed.

Requires a running server on 7777 and a real Ziniao client + at least
two Ziniao-backed stores configured. These are heavy, stateful, and
depend on live Ziniao, so they are gated behind --e2e AND -m ziniao and
are NEVER run in CI.

Run locally:
    ./start.sh 7777
    pytest tests/e2e/test_ziniao_concurrency_recovery.py --e2e -v -m ziniao

Against another port / log (e.g. a native-Windows server tested from WSL):
    E2E_PORT=7790 VIBE_E2E_BACKEND_LOG=/path/to/backend_7790.log \
    VIBE_E2E_ZINIAO_EXE='C:\\Program Files\\ziniao\\ziniao.exe' pytest ...
"""

import concurrent.futures as cf
import json
import logging
import os
from pathlib import Path
import platform
import subprocess
import time

import httpx
import pytest

from app.browser.ziniao_processes import (
    describe,
    kill_all_ziniao,
    list_ziniao_processes,
)
from app.browser.ziniao_utils import is_wsl
from tests.e2e.e2e_helpers import BASE_URL, login, server_log_path

logger = logging.getLogger(__name__)
pytestmark = [pytest.mark.e2e, pytest.mark.ziniao]

ZINIAO_PORT = 16851
# The log strings that mark a SHARED-CLIENT restart. These must never be
# emitted by per-store browser starts against an answering client — only
# by a client startup round (nothing answering on the control port).
GLOBAL_KILL_MARKERS = (
    'Stopping every Ziniao process before launch',
    'Launching Ziniao in WebDriver mode',
)


def _client() -> httpx.Client:
    c = httpx.Client(base_url=BASE_URL, timeout=120, trust_env=False)
    try:
        login(c)
    except httpx.HTTPStatusError:
        # A local server with sign-in turned off (Settings) needs no
        # login — and its admin password is the user's own.
        status = c.get('/api/auth/status').json()
        if status.get('auth_required') is not False:
            raise
    return c


def _ziniao_store_ids(client: httpx.Client) -> list[str]:
    stores = client.get('/api/stores').json()
    return [s['id'] for s in stores if s.get('browser_backend') == 'ziniao']


def _backend_log() -> Path:
    """The log of the server under test. A server whose log this process
    cannot see by itself (e.g. a native-Windows server driven from WSL)
    names it in VIBE_E2E_BACKEND_LOG."""
    override = os.environ.get('VIBE_E2E_BACKEND_LOG')
    return Path(override) if override else server_log_path()


def _log_len() -> int:
    log = _backend_log()
    return log.stat().st_size if log.exists() else 0


def _log_since(offset: int) -> str:
    log = _backend_log()
    if not log.exists():
        return ''
    with log.open('rb') as f:
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
                timeout=180,
            ).status_code
        except Exception as e:  # noqa: BLE001
            return repr(e)

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


def _wsl() -> bool:
    return is_wsl()


def _ziniao_procs() -> list:
    """Ziniao's processes, by the product's own rule (path/name, never a
    command line — ``pgrep -f ziniao`` matches this very test file)."""
    return list_ziniao_processes(wsl=_wsl())


def _ziniao_running() -> bool:
    return bool(_ziniao_procs())


def _api_answers() -> bool:
    try:
        r = httpx.post(
            f'http://127.0.0.1:{ZINIAO_PORT}',
            content=json.dumps({'action': 'getBrowserList'}),
            timeout=5,
            trust_env=False,
        )
        r.json()
        return True
    except Exception:  # noqa: BLE001
        return False


def _open_ziniao_normal_mode() -> None:
    """Close Ziniao, then open it the way a person does: no WebDriver flags
    (normal mode — the control port stays closed)."""
    kill_all_ziniao(wsl=_wsl())
    if platform.system() == 'Darwin':
        subprocess.run(['open', '-a', 'ziniao'], check=True)
    else:
        exe = os.environ.get(
            'VIBE_E2E_ZINIAO_EXE', r'C:\Program Files\ziniao\ziniao.exe'
        )
        if _wsl():
            subprocess.run(
                [
                    '/mnt/c/Windows/System32/WindowsPowerShell/v1.0/'
                    'powershell.exe',
                    '-NoProfile',
                    '-Command',
                    f"Start-Process -FilePath '{exe}'",
                ],
                check=True,
                cwd='/mnt/c/',
            )
        else:
            subprocess.Popen([exe])
    for _ in range(60):
        time.sleep(1)
        if any(p.name.lower().startswith('ziniao') for p in _ziniao_procs()):
            break
    time.sleep(10)  # let it settle as a person would see it
    assert _ziniao_running(), 'Ziniao did not open in normal mode'
    assert not _api_answers(), 'Ziniao answered — not in normal mode'


def _start_with_retry(client: httpx.Client, sid: str) -> int:
    """What the store wrapper does: POST the start, ask again on 503."""
    code = 0
    for _ in range(6):
        try:
            code = client.post(
                f'/api/stores/{sid}/browser/start', timeout=300
            ).status_code
        except Exception:  # noqa: BLE001
            code = -1
        if code != 503:
            return code
        time.sleep(5)
    return code


def test_simultaneous_starts_from_normal_mode_share_one_relaunch(client):
    """Ziniao opened by hand (normal mode), then several store browsers
    are wanted at once: exactly ONE kill-all and ONE launch, and every
    start succeeds. With one store, its start is simply asked for three
    times at once."""
    store_ids = _ziniao_store_ids(client)
    if not store_ids:
        pytest.skip('no Ziniao store configured')
    if platform.system() not in ('Darwin', 'Windows') and not _wsl():
        pytest.skip('opening Ziniao by hand is scripted for Mac/Windows only')
    targets = store_ids if len(store_ids) >= 3 else store_ids * 3
    _open_ziniao_normal_mode()
    logger.info('normal mode: %s', describe(_ziniao_procs()))

    offset = _log_len()
    with cf.ThreadPoolExecutor(max_workers=len(targets)) as ex:
        codes = list(ex.map(lambda s: _start_with_retry(client, s), targets))
    window = _log_since(offset)

    assert all(200 <= c < 300 for c in codes), codes
    assert window.count('Stopping every Ziniao process before launch') == 1, (
        window[-3000:]
    )
    assert window.count('Launching Ziniao in WebDriver mode (port') == 1, (
        window[-3000:]
    )
    assert _api_answers()


def test_kill_ziniao_auto_relaunches(client):
    """Killing the Ziniao client must not wedge the platform: the next
    browser start auto-relaunches the client (ensure_ziniao_running).

    Asserts the *client process* comes back — not that the browser fully
    launches, since Ziniao's startBrowser is nondeterministically flaky
    (docs/ziniao-concurrency.md) and per-store retry owns that."""
    store_ids = _ziniao_store_ids(client)
    if not store_ids:
        pytest.skip('no Ziniao store configured')
    sid = store_ids[0]

    # Bring the client up.
    client.post(f'/api/stores/{sid}/browser/start', timeout=300)
    # Kill the whole Ziniao client out from under it.
    kill_all_ziniao(wsl=_wsl())
    time.sleep(3)
    assert not _ziniao_running(), 'Ziniao should be dead right after kill'

    # Next start must auto-relaunch the client (browser launch itself may
    # flake — that is retried per store, not asserted here).
    try:
        client.post(f'/api/stores/{sid}/browser/start', timeout=300)
    except Exception:  # noqa: BLE001
        pass
    assert _ziniao_running(), (
        'Ziniao client was not auto-relaunched after being killed'
    )
