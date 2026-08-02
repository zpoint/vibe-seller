"""Worker slots against a REAL browser, through the REAL wrapper.

The existing mux integration tests (``test_cdp_mux_proxy.py``,
``test_cdp_mux_browser_use.py``) already prove that N clients with
distinct ids get isolated tabs on one Chromium. They do it by
*fabricating* the ids — ``/client-task-a``, ``/client-task-b``.

That leaves the half this feature actually added unproven: that
``browser-use --worker N``, run out of the generated per-store wrapper
with ONE shared ``VIBE_TASK_ID`` (what a parent agent and its subagents
really have), is what produces those distinct ids. A wrapper bug that
handed two concurrent callers the same client id would sail through
every existing test and silently merge two agents onto one tab.

So this drives the genuine artefact end to end:

    write_browser_use_wrapper()  ->  bash wrapper  ->  browser-use
      ->  browser_harness daemon  ->  CDPMuxProxy  ->  real Chromium

No vibe-seller server is needed: the wrapper's auto-start block only
has to find the proxy already listening, so ``curl`` is stubbed true.

Ownership is asserted against the live proxy OBJECT (``_clients``,
``_target_to_client``, ``_session_to_client``) rather than parsed out of
logs — in-process, so it is exact.
"""

import concurrent.futures
import http.server
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import tempfile
import threading
import uuid

import pytest

from app.browser.cdp_mux_proxy import CDPMuxProxy
import app.browser.wrapper as wrapper_mod
from app.browser.wrapper import write_browser_use_wrapper
from tests.integration.conftest import cleanup_browser_tabs

SLUG = 'wslot'
# One page per concurrent driver. Distinct tokens are what turn a
# silent cross-read into a hard assertion failure.
PAGES = {
    '/alpha': 'TOKEN_ALPHA',
    '/beta': 'TOKEN_BETA',
    '/gamma': 'TOKEN_GAMMA',
}
_HTML = '<!doctype html><title>{t}</title><p id="tok">{t}</p>'


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        tok = PAGES.get(self.path)
        if not tok:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(_HTML.format(t=tok).encode())

    def log_message(self, *a):
        pass


@pytest.fixture(scope='module')
def site():
    # Threading: three browsers hit this at once, and a serialising
    # server would make the test slow for a reason unrelated to it.
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{srv.server_address[1]}'
    srv.shutdown()
    srv.server_close()


@pytest.fixture
async def slot_env(_browser, tmp_path):
    """Proxy on the shared Chromium + a real wrapper pointed at it."""
    pp = _free_port()
    proxy = CDPMuxProxy(listen_port=pp, target_port=_browser, cleanup_grace=0)
    await proxy.start()

    bin_dir = tmp_path / 'bin'
    real_bin, wrapper_mod._BIN_DIR = wrapper_mod._BIN_DIR, bin_dir
    try:
        write_browser_use_wrapper(SLUG, 'chrome', pp, store_id='wslot-store')
    finally:
        wrapper_mod._BIN_DIR = real_bin

    path = bin_dir / SLUG / 'browser-use'
    text = path.read_text()
    # The proxy is already up, so the auto-start block only needs its
    # health probe to succeed; there is no server to call.
    text = text.replace('curl ', '/usr/bin/true ')
    path.write_text(text)

    yield {'proxy': proxy, 'wrapper': path, 'port': pp}

    await proxy.stop()
    await cleanup_browser_tabs(_browser)


def _drive(wrapper: Path, task_id: str, url: str, *flags: str) -> str:
    """Open ``url`` and read its token back in ONE wrapper call.

    Output goes to files, never pipes: a call that starts a daemon
    leaves a detached ``browser_harness`` process holding the inherited
    stdout, so ``capture_output=True`` would block on an EOF that only
    arrives when that daemon dies.
    """
    code = (
        f'new_tab("{url}")\n'
        'wait_for_load()\n'
        'print("TOKEN=" + (js("document.getElementById(\'tok\').textContent")'
        ' or "NONE"))\n'
    )
    env = {**os.environ, 'VIBE_TASK_ID': task_id}
    for v in ('BU_NAME', 'BU_CDP_WS', 'BU_CDP_URL'):
        env.pop(v, None)
    with tempfile.TemporaryDirectory() as tmp:
        out, err = Path(tmp) / 'o', Path(tmp) / 'e'
        with (
            open(out, 'w+') as fo,
            open(err, 'w+') as fe,
            open(Path(tmp) / 'i', 'w+') as fi,
        ):
            fi.write(code)
            fi.seek(0)
            proc = subprocess.Popen(
                [str(wrapper), *flags],
                stdin=fi,
                stdout=fo,
                stderr=fe,
                env=env,
                start_new_session=True,
            )
            try:
                rc = proc.wait(timeout=180)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                proc.wait(timeout=30)
                raise AssertionError(
                    f'{flags or "(main)"} hung >180s'
                ) from None
        so, se = (
            out.read_text(errors='replace'),
            err.read_text(errors='replace'),
        )
    assert rc == 0, f'{flags or "(main)"} rc={rc}\n{so[-600:]}\n{se[-600:]}'
    m = re.search(r'TOKEN=(\S+)', so)
    assert m, f'no token: {so[-600:]}'
    return m.group(1)


@pytest.mark.integration
class TestWorkerSlotIsolationRealBrowser:
    async def test_three_concurrent_drivers_keep_their_own_tabs(
        self, slot_env, site
    ):
        """Main + two slots, one shared task id, one real Chromium.

        Asserts the two things that matter together: each driver reads
        the page IT opened (no cross-read), and the proxy's ownership
        maps partition cleanly (no shared tab or CDP session).
        """
        proxy, wrapper = slot_env['proxy'], slot_env['wrapper']
        task_id = str(uuid.uuid4())
        plan = [
            ((), f'{site}/alpha', 'TOKEN_ALPHA'),
            (('--worker', '1'), f'{site}/beta', 'TOKEN_BETA'),
            (('--worker', '2'), f'{site}/gamma', 'TOKEN_GAMMA'),
        ]
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futs = [
                pool.submit(_drive, wrapper, task_id, url, *flags)
                for flags, url, _ in plan
            ]
            got = [f.result(timeout=240) for f in futs]

        for (flags, _, want), actual in zip(plan, got, strict=True):
            assert actual == want, (
                f'{flags or "(main)"} read {actual!r}, expected {want!r} — '
                "a driver was looking at another driver's tab"
            )

        # --- the ids the WRAPPER minted, not ids we invented ---
        expected = {task_id, f'{task_id}-w1', f'{task_id}-w2'}
        seen = set(proxy._clients) | set(proxy._deferred_cleanups)
        assert expected <= seen, f'missing slot clients: {expected - seen}'

        # --- ownership partitions ---
        owners = {}
        for tid, cid in proxy._target_to_client.items():
            owners.setdefault(cid, set()).add(tid)
        for a in expected:
            for b in expected:
                if a < b:
                    assert not (owners.get(a, set()) & owners.get(b, set())), (
                        f'{a} and {b} share a tab'
                    )
        sess = {}
        for sid, cid in proxy._session_to_client.items():
            sess.setdefault(cid, set()).add(sid)
        for a in expected:
            for b in expected:
                if a < b:
                    assert not (sess.get(a, set()) & sess.get(b, set())), (
                        f'{a} and {b} share a CDP session'
                    )

    async def test_flagless_callers_share_one_client(self, slot_env, site):
        """Contrast case — the slot is what creates the separation.

        Without it, two callers on one task id resolve to a single
        daemon and a single mux client. If this ever starts producing
        two clients, the ids stopped deriving from VIBE_TASK_ID.
        """
        proxy, wrapper = slot_env['proxy'], slot_env['wrapper']
        task_id = str(uuid.uuid4())
        _drive(wrapper, task_id, f'{site}/alpha')
        _drive(wrapper, task_id, f'{site}/beta')
        ids = {
            c
            for c in set(proxy._clients) | set(proxy._deferred_cleanups)
            if c.startswith(task_id)
        }
        assert ids == {task_id}, f'expected one client, got {ids}'

    async def test_out_of_range_slot_never_reaches_the_proxy(
        self, slot_env, site
    ):
        """The bound is enforced before a connection is ever made."""
        proxy, wrapper = slot_env['proxy'], slot_env['wrapper']
        task_id = str(uuid.uuid4())
        proc = subprocess.run(
            [str(wrapper), '--worker', '99'],
            input='print("nope")\n',
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, 'VIBE_TASK_ID': task_id},
        )
        assert proc.returncode == 1
        assert 'ERROR: --worker' in proc.stderr
        assert not [
            c
            for c in set(proxy._clients) | set(proxy._deferred_cleanups)
            if c.startswith(task_id)
        ]
