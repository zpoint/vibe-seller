"""Live smoke test for the 稳卖 (wenmai) bind / start / read flow.

Runs against the REAL 稳卖 client on Windows (or WSL driving the
Windows side over 127.0.0.1). NOT a CI test: it pins the coupling to
the vendor's local API shapes (verified live 2026-11-27, client
v5.1.0715). If 稳卖 changes its API (logged-out shape, openEnv fields,
the middle page), a scenario FAILS and names what broke.

It reads `test.yaml` from the repo root (gitignored — local-only
credentials) and SKIPS cleanly when the file or the client is absent:

    python tests/e2e/wenmai_live_smoke.py
    python tests/e2e/wenmai_live_smoke.py --restart   # kill + relaunch
    python tests/e2e/wenmai_live_smoke.py --logout    # also exercise
                                                     # logout + auto-login

Scenarios: enumerate (paged), open → middle page clicked → CDP,
reopen → same port, close → QueueRunningStoreList agrees, client
killed → recovery, store browser closed by hand → recovery, device
unbound → StoreUnavailable (restored afterwards via bindEnvDevice),
wrong password → LoginRequired.

Exit code 0 ⇒ every scenario passed; 1 ⇒ at least one failed.
"""

# T201: prints in a script; E402: imports grouped below the docstring.
import argparse
import asyncio
from contextlib import suppress
import logging
from pathlib import Path
import subprocess
import sys
import time

import aiohttp
import yaml

from app.browser.adapters.wenmai_client import RealWenmaiClient
from app.browser.inject.errors import (
    LoginRequired,
    StoreUnavailable,
)
from app.browser.win_inspector import InspectorSession
from app.browser.ziniao_utils import get_platform

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEST_YAML = _REPO_ROOT / 'test.yaml'

_results: list[tuple[str, bool, str]] = []


def _record(name: str, passed: bool, detail: str = '') -> None:
    _results.append((name, passed, detail))
    mark = 'PASS' if passed else 'FAIL'
    print(f'  [{mark}] {name}' + (f' — {detail}' if detail else ''), flush=True)


def _load_config() -> dict | None:
    """The wenmai block of test.yaml, or None (skip cleanly)."""
    if not _TEST_YAML.is_file():
        return None
    try:
        data = yaml.safe_load(_TEST_YAML.read_text(encoding='utf-8'))
    except yaml.YAMLError:
        return None
    cfg = (data or {}).get('wenmai') or {}
    if not cfg.get('test_store_id'):
        return None
    return cfg


def _creds(cfg: dict) -> dict:
    """Credentials into the browser config under the key names the
    registry injects (wenmai_<field>) — exactly what auto-login will
    read once the generic credential storage lands."""
    return {
        'wenmai_company': str(cfg.get('company') or ''),
        'wenmai_username': str(cfg.get('username') or ''),
        'wenmai_password': str(cfg.get('password') or ''),
    }


async def _cdp_ok(host: str, port: int) -> bool:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f'http://{host}:{port}/json/version',
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                return bool((await r.json()).get('webSocketDebuggerUrl'))
    except Exception:
        return False


async def _cdp_pages(host: str, port: int) -> list[dict]:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f'http://{host}:{port}/json/list',
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                targets = await r.json(content_type=None)
        return [t for t in targets if t.get('type') == 'page']
    except Exception:
        return []


async def _browser_close(host: str, port: int) -> None:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f'http://{host}:{port}/json/version',
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                ws = (await r.json()).get('webSocketDebuggerUrl')
            if ws:
                async with s.ws_connect(ws, timeout=10) as w:
                    await w.send_json({'id': 1, 'method': 'Browser.close'})
                    with suppress(Exception):
                        await asyncio.wait_for(w.receive(), timeout=3)
    except Exception:
        pass


def _ps(script: str) -> str:
    try:
        return subprocess.run(
            [
                'powershell.exe',
                '-NoProfile',
                '-NonInteractive',
                '-Command',
                script,
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ''


async def _running_store_ids(client: RealWenmaiClient) -> set[str]:
    """GET /QueueRunningStoreList — answered by the shell itself.

    Verified live: a BARE JSON array of open env ids (``[100000000001]``),
    empty when none run. Fetched raw because the client's _request
    only returns dict bodies.
    """
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f'{client._base}/QueueRunningStoreList',
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                raw = await r.json(content_type=None)
    except (TimeoutError, aiohttp.ClientError, ValueError):
        return set()
    if not isinstance(raw, list):
        return set()
    return {str(x.get('id') if isinstance(x, dict) else x) for x in raw}


async def _logout_via_client(client: RealWenmaiClient) -> bool:
    """Log the client out through ITS OWN logout button (the app's
    handleLogout), so the auto-login scenarios start logged out.
    Test-harness only — never product code.

    handleLogout triggers a page reload, which tears down any promise
    that is still awaiting it (seen live: inspector target closed
    mid-call). So the logout is FIRED, and the login page is confirmed
    from separate short evals — each reconnects on its own and survives
    the reload. The API token clear is awaited too (it lags the UI).
    """
    for attempt in range(3):
        try:
            async with client._inspector() as insp:
                called = str(
                    await insp.eval_in_renderer(
                        '(function(){'
                        'for (let i = 0; i < 20 && '
                        '!document.querySelector("#app"); i++) {}'
                        'const appEl = document.querySelector("#app");'
                        'if (!appEl) return "NO_APP:" + location.hash;'
                        'const root = appEl.__vue__;'
                        'const stack = [root]; let target = null;'
                        'while (stack.length) { const vm = stack.pop();'
                        'if (vm && typeof vm.handleLogout === "function") '
                        '{ target = vm; break; } '
                        'stack.push(...((vm && vm.$children) || [])'
                        '.filter(Boolean)); }'
                        'if (!target) return "NO_LOGOUT_COMPONENT:" '
                        '+ location.hash;'
                        # Fire, don't await: the reload it triggers would
                        # tear the awaiting promise down.
                        'target.handleLogout(); return "CALLED"; })()'
                    )
                )
                if called != 'CALLED':
                    return False
                on_login = False
                for _ in range(15):
                    await asyncio.sleep(1.0)
                    try:
                        h = str(await insp.eval_in_renderer('location.hash'))
                    except Exception:
                        continue  # each eval reconnects on its own
                    if h.startswith('#/login'):
                        on_login = True
                        break
                if not on_login:
                    return False
            for _ in range(10):  # the API token lags the UI logout
                if not await client.is_logged_in():
                    return True
                await asyncio.sleep(1.0)
            return False
        except Exception:
            if attempt == 2:
                return False
            await asyncio.sleep(2.0)
    return False


async def _running_store_ids(client: RealWenmaiClient) -> set[str]:
    """GET /QueueRunningStoreList — answered by the shell itself.

    Verified live: a BARE JSON array of open env ids (``[100000000001]``),
    empty when none run. Fetched raw because the client's _request
    only returns dict bodies.
    """
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f'{client._base}/QueueRunningStoreList',
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                raw = await r.json(content_type=None)
    except (TimeoutError, aiohttp.ClientError, ValueError):
        return set()
    if not isinstance(raw, list):
        return set()
    return {str(x.get('id') if isinstance(x, dict) else x) for x in raw}


async def _logout_via_client(client: RealWenmaiClient) -> bool:
    """Log the client out through ITS OWN logout button (the app's
    handleLogout), so the auto-login scenarios start logged out.
    Test-harness only — never product code."""
    async with client._inspector() as insp:
        result = str(
            await insp.eval_in_renderer(
                '(async () => {'
                # The window can be mid-reload right after a login or
                # logout (seen live: #app was null). Wait for the app
                # root before walking the Vue tree.
                'for (let i = 0; i < 20 && '
                '!document.querySelector("#app"); i++) {'
                'await new Promise(r => setTimeout(r, 500)); }'
                'const appEl = document.querySelector("#app");'
                'if (!appEl) return "NO_APP:" + location.hash;'
                'const root = appEl.__vue__;'
                'const stack = [root]; let target = null;'
                'while (stack.length) { const vm = stack.pop();'
                'if (typeof vm.handleLogout === "function") '
                '{ target = vm; break; }'
                'stack.push(...((vm && vm.$children) || []).filter(Boolean)); }'
                'if (!target) return "NO_LOGOUT_COMPONENT:" + location.hash;'
                'await target.handleLogout();'
                'for (let i = 0; i < 15; i++) {'
                'await new Promise(r => setTimeout(r, 1000));'
                'if (location.hash.startsWith("#/login")) '
                'return "LOGGED_OUT:" + location.hash; }'
                'return "STILL:" + location.hash; })()'
            )
        )
    return result.startswith('LOGGED_OUT')


async def _run(client: RealWenmaiClient, cfg: dict, args) -> None:
    store_ref = str(cfg['test_store_id'])

    # S: enumerate (the API pages by itself).
    stores = await client.list_stores()
    _record('list_stores returns stores', bool(stores), f'{len(stores)}')
    target = next((s for s in stores if s.ref == store_ref), None)
    _record(
        f'test store {store_ref} is listed',
        target is not None,
        target.name if target else 'absent',
    )
    if target is None:
        print('The test store is not visible to this login — aborting.')
        return
    _record(
        'device-bound state is carried',
        target.available is True,
        'available' if target.available else 'unbound',
    )

    # S (opt-in): log out through the client's own button, then
    # auto-login with the saved credentials must bring it back.
    if args.logout:
        if not await _logout_via_client(client):
            _record('logout via the client (setup)', False, 'could not')
        else:
            _record('logout via the client (setup)', True)
            try:
                await client.ensure_logged_in()
                ok = await client.is_logged_in()
                _record('logged out → auto-login', ok)
            except LoginRequired as e:
                _record('logged out → auto-login', False, str(e))
            # S: wrong password → a clear LoginRequired, client not
            # stuck. The client must be LOGGED OUT first: a logged-in
            # client short-circuits at the token check and would make
            # this scenario "pass" for the wrong reason.
            if not await _logout_via_client(client):
                _record(
                    'wrong password → LoginRequired',
                    False,
                    'could not log out for the setup',
                )
            else:
                bad = RealWenmaiClient({
                    **_creds(cfg),
                    'wenmai_password': 'definitely-wrong-password',
                })
                try:
                    await bad.ensure_logged_in()
                    _record('wrong password → LoginRequired', False, 'no raise')
                except LoginRequired:
                    _record('wrong password → LoginRequired', True)
                except Exception as e:
                    _record('wrong password → LoginRequired', False, repr(e))
            # The client must not be left stuck: the real login still works.
            try:
                await client.ensure_logged_in()
                _record('client not stuck after a failed login', True)
            except Exception as e:
                _record('client not stuck after a failed login', False, repr(e))

            # S: a leftover inspector on the target pid is REUSED, not
            # refused. Simulate an operation dying between open and
            # close while the client is LOGGED OUT: open an inspector
            # on the client's main process, then release ONLY the
            # machine lock (a killed process frees the OS lock but
            # leaves the target's inspector serving on 9229) without
            # inspector.close(). The next auto-login must adopt that
            # leftover and drive the form through it (the pre-P5
            # behavior refused forever — auto-login was dead until a
            # client restart).
            try:
                if not await _logout_via_client(client):
                    _record(
                        'leftover inspector on the target pid → reused',
                        False,
                        'could not log out for the setup',
                    )
                else:
                    pid = await asyncio.to_thread(client._proc.main_pid)
                    leftover = InspectorSession(client._proc.exe_path_script)
                    await leftover.open(pid)
                    leftover._release_lock()  # crash: lock freed, the
                    # inspector stays; NO inspector.close() happened.

                    reuse_seen = []

                    class _ReuseSpot(logging.Handler):
                        def emit(self, record):
                            if 'reusing the leftover' in record.getMessage():
                                reuse_seen.append(record.getMessage())

                    spot = _ReuseSpot()
                    logging.getLogger('app.browser.win_inspector').addHandler(
                        spot
                    )
                    try:
                        await client.ensure_logged_in()
                    finally:
                        logging.getLogger(
                            'app.browser.win_inspector'
                        ).removeHandler(spot)
                    ok = await client.is_logged_in()
                    _record(
                        'leftover inspector on the target pid → reused',
                        ok and bool(reuse_seen),
                        reuse_seen[0] if reuse_seen else 'no reuse log',
                    )
                    await leftover.close()  # tidy: now OUR session again
            except Exception as e:
                _record(
                    'leftover inspector on the target pid → reused',
                    False,
                    repr(e),
                )

    # S: open → CDP reachable → after_open (the ENGINE's entry-page
    # hook, driven here exactly as InjectCdpBackend.start does) → a
    # real platform page target exists.
    opened = await client.open_store(store_ref)
    ok = False
    for _ in range(4):  # like the engine's probe: retries, one shot is
        ok = await _cdp_ok(opened.cdp_host, opened.cdp_port)  # not it
        if ok:
            break
        await asyncio.sleep(1.5)
    _record('open → CDP', ok, f'port {opened.cdp_port}')
    await client.after_open(opened)
    pages = await _cdp_pages(opened.cdp_host, opened.cdp_port)
    has_store_page = any(
        str(p.get('url', '')).startswith(('http://', 'https://')) for p in pages
    )
    _record(
        'middle page clicked → platform page target',
        has_store_page,
        '; '.join(str(p.get('url', ''))[:60] for p in pages),
    )

    # S: reopen → the SAME port (a store opened by hand attaches too).
    again = await client.open_store(store_ref)
    _record(
        'reopen → same port',
        again.cdp_port == opened.cdp_port,
        f'{opened.cdp_port} vs {again.cdp_port}',
    )

    # S: QueueRunningStoreList agrees the store is open.
    running = await _running_store_ids(client)
    _record(
        'QueueRunningStoreList lists the store',
        store_ref in running,
        f'{len(running)} running',
    )

    # S: the user closes the store browser mid-task → reopen recovers.
    await _browser_close(opened.cdp_host, opened.cdp_port)
    for _ in range(12):
        if not await _cdp_ok(opened.cdp_host, opened.cdp_port):
            break
        await asyncio.sleep(1)
    recovered = await client.open_store(store_ref)
    _record(
        'store browser closed by hand → reopen',
        await _cdp_ok(recovered.cdp_host, recovered.cdp_port),
        f'port {recovered.cdp_port}',
    )

    # S: close via the engine's path (Browser.close + closeEnv) → the
    # client agrees, and a second task reopens cleanly.
    await _browser_close(opened.cdp_host, opened.cdp_port)
    await client.close_store(store_ref)
    for _ in range(12):
        running = await _running_store_ids(client)
        if store_ref not in running:
            break
        await asyncio.sleep(1)
    _record(
        'close → client agrees (QueueRunningStoreList)',
        store_ref not in running,
    )
    third = await client.open_store(store_ref)
    _record(
        'second task reopens cleanly after close',
        await _cdp_ok(third.cdp_host, third.cdp_port),
        f'port {third.cdp_port}',
    )

    # S (opt-in): kill the client mid-task → the next use recovers.
    if args.restart:
        _ps(
            'Stop-Process -Name WenMaiAIBrowser -Force '
            '-ErrorAction SilentlyContinue'
        )
        await asyncio.sleep(3)
        try:
            await client.ensure_running()
            fourth = await client.open_store(store_ref)
            ok = await _cdp_ok(fourth.cdp_host, fourth.cdp_port)
            _record(
                'client killed → next use recovers',
                ok,
                f'port {fourth.cdp_port}',
            )
        except Exception as e:
            _record('client killed → next use recovers', False, repr(e))

    # S: device unbound → StoreUnavailable; restored via bindEnvDevice.
    listing = await client._call(
        'queryEnvV2', {'pageNum': '1', 'pageSize': '100'}
    )
    envs = (listing or {}).get('response', {}).get('envs') or []
    env = next(
        (e for e in envs if str(e['id']) == store_ref),
        None,
    )
    ip_id = (env or {}).get('ipId')
    equipment = (env or {}).get('equipmentType') or 'self'
    unbound = await client._call('unbindEnvDevice', {'id': store_ref})
    _record(
        'unbindEnvDevice (setup)',
        isinstance(unbound, dict),
        str(unbound)[:120],
    )
    try:
        await client.open_store(store_ref)
        _record('device unbound → StoreUnavailable', False, 'no raise')
    except StoreUnavailable as e:
        _record('device unbound → StoreUnavailable', True, str(e)[:120])
    except Exception as e:
        _record('device unbound → StoreUnavailable', False, repr(e))
    # RESTORE — if this fails, stop and report (the store must not be
    # left unusable).
    restored = await client._call(
        'bindEnvDevice',
        {
            'id': store_ref,
            'ipId': ip_id,
            'equipmentType': equipment,
        },
    )
    _record(
        'bindEnvDevice restore',
        isinstance(restored, dict),
        str(restored)[:120],
    )
    try:
        fifth = await client.open_store(store_ref)
        ok = await _cdp_ok(fifth.cdp_host, fifth.cdp_port)
        _record('device restored → store opens again', ok)
    except Exception as e:
        _record('device restored → store opens again', False, repr(e))

    # Leave the store browser closed (the handoff state).
    await _browser_close(opened.cdp_host, opened.cdp_port)
    await client.close_store(store_ref)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        '--restart',
        action='store_true',
        help='also kill the client and verify recovery',
    )
    ap.add_argument(
        '--logout',
        action='store_true',
        help='also log out via the client and verify auto-login',
    )
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format='%(levelname)s %(name)s %(message)s',
    )

    print('=' * 62)
    print('稳卖 (wenmai) live bind/start/read smoke test')
    print('=' * 62)
    plat = get_platform()
    _record(f'platform supported ({plat})', plat in ('windows', 'wsl'))
    if plat not in ('windows', 'wsl'):
        print('This backend is Windows-only; aborting.')
        return 1

    cfg = _load_config()
    if cfg is None:
        print('No test.yaml (or no wenmai.test_store_id) — skipping.')
        return 0

    client = RealWenmaiClient(_creds(cfg))
    t0 = time.time()
    try:
        await client.ensure_running()
        _record('ensure_running', True)
    except Exception as e:
        _record('ensure_running', False, repr(e))
        return _summary()

    try:
        await client.ensure_logged_in()
        _record('ensure_logged_in', True)
    except LoginRequired as e:
        _record('ensure_logged_in', False, str(e))
        return _summary()

    try:
        await _run(client, cfg, args)
    except Exception as e:
        _record('scenario run', False, repr(e))

    print(f'\n(elapsed {time.time() - t0:.0f}s)')
    return _summary()


def _summary() -> int:
    print('\n' + '=' * 62)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = [n for n, ok, _ in _results if not ok]
    print(f'RESULTS: {passed}/{len(_results)} passed')
    if failed:
        print('FAILED scenarios (API drift or a real bug):')
        for n in failed:
            print(f'  - {n}')
    print('=' * 62)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
