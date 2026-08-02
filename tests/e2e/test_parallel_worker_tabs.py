"""E2E: two concurrent browser drivers on ONE store browser.

The failure this pins is silent. A subagent runs inside its parent's
process and inherits ``VIBE_TASK_ID``; the wrapper derives BOTH the
browser-use daemon name and the CDP mux client id from that one
variable; and ``browser_harness`` serves every socket connection in its
own asyncio task with no lock. So two agents driving the browser at the
same time land on one daemon and one tab, interleave, and read each
other's pages — no crash, no error, just wrong data.

``browser-use --worker N`` is the fix: a validated slot number that the
wrapper turns into a separate daemon and a separate mux client on the
same logged-in browser.

These tests drive the wrapper **directly**, as two real concurrent
processes sharing one ``VIBE_TASK_ID`` — the exact shape of a parent and
a subagent. That is deliberate: routing this through a task and hoping
the LLM chooses to parallelise would make the regression test depend on
a model's judgement. The plumbing is what must not regress, so the
plumbing is what is tested.

Run:
    pytest tests/e2e/test_parallel_worker_tabs.py --e2e -v
"""

import concurrent.futures
import http.server
import logging
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import threading
import time
import uuid

import pytest

from tests.e2e.e2e_helpers import BASE_URL, create_store

logger = logging.getLogger(__name__)
pytestmark = [pytest.mark.e2e]

VIBE_HOME = Path.home() / '.vibe-seller'
BIN_DIR = VIBE_HOME / 'bin'
RUNTIME_DIR = VIBE_HOME / 'r'

# One distinct page per driver. If isolation breaks, a driver reads the
# other's token — which is the whole point: the tokens make a silent
# cross-read loud.
PAGES = {
    '/alpha': ('Alpha Page', 'TOKEN_ALPHA_A1'),
    '/beta': ('Beta Page', 'TOKEN_BETA_B2'),
}

_HTML = """\
<!DOCTYPE html>
<html><head><title>{title}</title></head>
<body><h1>{title}</h1><p id="tok">{token}</p></body></html>
"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        page = PAGES.get(self.path)
        if not page:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(_HTML.format(title=page[0], token=page[1]).encode())

    def log_message(self, *args):
        pass


@pytest.fixture(scope='module')
def test_site():
    # Threading, not the plain HTTPServer: these tests point up to four
    # concurrent browsers at this site at once, and a single-threaded
    # server would serialise their page loads — turning the thing under
    # test (parallelism) into the thing that makes the test slow.
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_address[1]}'
    server.shutdown()
    server.server_close()


@pytest.fixture(scope='module')
def chrome_store(api_client):
    """A Chrome store whose wrapper + browser are up and reachable."""
    ts = int(time.time())
    slug = f'worker-e2e-{ts}'
    store = create_store(api_client, slug, browser_backend='chrome')
    # try/finally, not a bare yield: every check below can skip, and a
    # `pytest.skip` raised BEFORE the yield never reaches teardown — so
    # the store the run just created would be left behind on a
    # developer's real instance, once per attempt.
    try:
        resp = api_client.post(
            f'{BASE_URL}/api/stores/{store["id"]}/browser/start?force=1',
            timeout=180,
        )
        if resp.status_code >= 300:
            pytest.skip(
                f'browser/start failed: {resp.status_code} {resp.text[:200]}'
            )
        wrapper = BIN_DIR / slug / 'browser-use'
        if not wrapper.is_file():
            pytest.skip(f'wrapper not generated at {wrapper}')
        if '--worker' not in wrapper.read_text():
            pytest.skip('server runs a pre-v5 wrapper (no --worker support)')
        yield {'id': store['id'], 'slug': slug, 'wrapper': wrapper}
    finally:
        # Deleting the store also stops its browser and removes the
        # wrapper, so repeated local runs leave no stores behind and no
        # orphaned Chrome windows.
        try:
            api_client.delete(
                f'{BASE_URL}/api/stores/{store["id"]}', timeout=60
            )
        except Exception:
            logger.warning('could not delete e2e store %s', store['id'])


def _drive(wrapper: Path, task_id: str, url: str, *flags: str) -> str:
    """One wrapper invocation: open ``url``, read the token back.

    Open and read are a SINGLE call so the read cannot be separated from
    its own navigation by the other driver — with shared state, the read
    still returns whatever tab the daemon is pointed at, which is
    precisely the cross-read we want to detect.
    """
    code = (
        f'new_tab("{url}")\n'
        'wait_for_load()\n'
        'print("TOKEN=" + (js("document.getElementById(\'tok\').textContent")'
        ' or "NONE"))\n'
    )
    env = {**os.environ, 'VIBE_TASK_ID': task_id}
    for var in ('BU_NAME', 'BU_CDP_WS', 'BU_CDP_URL'):
        env.pop(var, None)

    # Capture to FILES, not pipes. A wrapper call that has to start a
    # daemon leaves behind a long-lived, detached ``browser_harness``
    # process that inherited the wrapper's stdout/stderr — so the pipe's
    # write end outlives the wrapper and `capture_output=True` waits for
    # an EOF that only arrives when the daemon eventually dies. That
    # hangs the whole run past any subprocess timeout (`communicate()`
    # blocks on the pipe even after the direct child is killed).
    # A file has no such semantics: the daemon may hold the fd forever,
    # we just read the bytes once the wrapper itself exits.
    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / 'out'
        err_path = Path(tmp) / 'err'
        with (
            open(out_path, 'w+') as out_f,
            open(err_path, 'w+') as err_f,
            open(Path(tmp) / 'in', 'w+') as in_f,
        ):
            in_f.write(code)
            in_f.seek(0)
            proc = subprocess.Popen(
                [str(wrapper), *flags],
                stdin=in_f,
                stdout=out_f,
                stderr=err_f,
                env=env,
                # Own process group, so a timeout can reap the wrapper's
                # whole tree instead of orphaning a live browser-use.
                start_new_session=True,
            )
            try:
                rc = proc.wait(timeout=180)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                proc.wait(timeout=30)
                raise AssertionError(
                    f'wrapper {flags or "(main)"} did not finish in 180s'
                ) from None
        stdout = out_path.read_text(errors='replace')
        stderr = err_path.read_text(errors='replace')

    assert rc == 0, (
        f'wrapper {flags or "(main)"} failed rc={rc}\n'
        f'stdout={stdout[-800:]}\nstderr={stderr[-800:]}'
    )
    m = re.search(r'TOKEN=(\S+)', stdout)
    assert m, f'no token in output: {stdout[-800:]}'
    return m.group(1)


def _daemons_for(slug: str, task8: str) -> set[str]:
    """BU_NAMEs of daemons this task currently owns."""
    if not RUNTIME_DIR.is_dir():
        return set()
    return {
        p.name[len('bu-') : -len('.pid')]
        for p in RUNTIME_DIR.glob(f'bu-{slug}-{task8}*.pid')
    }


@pytest.mark.e2e
class TestParallelWorkerTabs:
    def test_concurrent_drivers_each_read_their_own_page(
        self, chrome_store, test_site
    ):
        """The property that matters: neither driver reads the other's page.

        Both processes share one ``VIBE_TASK_ID`` — as a parent and its
        subagent do — and run at the same time. Only the slot flag
        separates them.
        """
        wrapper = chrome_store['wrapper']
        task_id = str(uuid.uuid4())

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            main = pool.submit(_drive, wrapper, task_id, f'{test_site}/alpha')
            worker = pool.submit(
                _drive, wrapper, task_id, f'{test_site}/beta', '--worker', '1'
            )
            main_token = main.result(timeout=200)
            worker_token = worker.result(timeout=200)

        assert main_token == 'TOKEN_ALPHA_A1', (
            f'the main session read {main_token!r} — it navigated to /alpha, '
            'so anything else means the worker drove its tab'
        )
        assert worker_token == 'TOKEN_BETA_B2', (
            f'worker slot 1 read {worker_token!r} — it navigated to /beta, '
            'so anything else means it was driving the main tab'
        )

    def test_the_slot_is_what_creates_the_separation(
        self, chrome_store, test_site
    ):
        """Contrast case, so a pass above cannot be luck.

        Same store, same task id: WITHOUT the flag both callers resolve
        to one daemon; WITH it they resolve to two. Daemon identity is
        deterministic, unlike which of two interleaved reads happens to
        win, so this is the assertion that actually holds every run.
        """
        wrapper = chrome_store['wrapper']
        slug = chrome_store['slug']

        shared_id = str(uuid.uuid4())
        _drive(wrapper, shared_id, f'{test_site}/alpha')
        _drive(wrapper, shared_id, f'{test_site}/beta')
        no_slot = _daemons_for(slug, shared_id[:8])
        assert no_slot == {f'{slug}-{shared_id[:8]}'}, (
            f'two flagless callers must share one daemon, got {no_slot}'
        )

        slot_id = str(uuid.uuid4())
        _drive(wrapper, slot_id, f'{test_site}/alpha')
        _drive(wrapper, slot_id, f'{test_site}/beta', '--worker', '1')
        with_slot = _daemons_for(slug, slot_id[:8])
        assert with_slot == {
            f'{slug}-{slot_id[:8]}',
            f'{slug}-{slot_id[:8]}-w1',
        }, f'the slot must add a second daemon, got {with_slot}'

    def test_every_slot_is_its_own_daemon(self, chrome_store, test_site):
        """All configured slots are usable at once, none aliasing another.

        Also proves the CDP mux capacity covers what the wrapper
        advertises — a slot the wrapper accepts but the proxy refuses
        (`max_clients`) would surface here as a failed invocation.
        """
        wrapper = chrome_store['wrapper']
        slug = chrome_store['slug']
        slots = int(os.environ.get('VIBE_BROWSER_WORKER_SLOTS', '3'))
        task_id = str(uuid.uuid4())

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=slots + 1
        ) as pool:
            futures = [
                pool.submit(_drive, wrapper, task_id, f'{test_site}/alpha')
            ] + [
                pool.submit(
                    _drive,
                    wrapper,
                    task_id,
                    f'{test_site}/beta',
                    '--worker',
                    str(n),
                )
                for n in range(1, slots + 1)
            ]
            tokens = [f.result(timeout=300) for f in futures]

        assert tokens[0] == 'TOKEN_ALPHA_A1'
        assert all(t == 'TOKEN_BETA_B2' for t in tokens[1:]), tokens
        assert len(_daemons_for(slug, task_id[:8])) == slots + 1

    def test_out_of_range_slot_is_refused(self, chrome_store, test_site):
        """The bound is enforced by the server-generated script.

        An agent cannot widen it, and a refusal must not leave a daemon
        behind — otherwise a bad slot number would still consume a mux
        client.
        """
        wrapper = chrome_store['wrapper']
        slug = chrome_store['slug']
        slots = int(os.environ.get('VIBE_BROWSER_WORKER_SLOTS', '3'))
        task_id = str(uuid.uuid4())

        proc = subprocess.run(
            [str(wrapper), '--worker', str(slots + 1)],
            input='print("should not run")\n',
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, 'VIBE_TASK_ID': task_id},
        )
        assert proc.returncode == 1, proc.stdout
        assert 'ERROR: --worker' in proc.stderr
        assert not _daemons_for(slug, task_id[:8])
