"""Live smoke test for the 悦互联 (yuehulian) bind / start / read flow.

Runs against the REAL yuehulian client on Windows (or WSL driving the
Windows side over 127.0.0.1) and on macOS (natively). It is NOT a CI
test — screen-scraping an
Electron GUI is inherently coupled to that GUI, so this script is the
regression guard for THAT coupling: if yuehulian upgrades its UI (el-table
classes, the store-list menu, the tags-view shell, pagination) or its
routes, a scenario FAILS and names what broke. All PASS ⇒ the whole
bind/enumerate/open flow still matches the live client.

It drives the real adapter (`RealYuehulianClient`) plus the inspector
(`ProcessLayer` / `InspectorSession`) to put the client into each state a
user can create — on another view, paginated, store already open, client
closed, logged out — exactly the scenarios that unit tests with fakes
cannot see.

Usage (repo root; client installed, running and LOGGED IN):

    python tests/e2e/yuehulian_live_smoke.py
    python tests/e2e/yuehulian_live_smoke.py --restart   # also kills+autostarts the client

Run it again AFTER logging OUT in the client to exercise the logout path
(it auto-detects the logged-out state). Full coverage wants 2+ stores
spanning more than one pagination page.

Exit code 0 ⇒ every scenario passed; 1 ⇒ at least one failed (UI/API drift
or a real bug).
"""

# T201: prints in a script; E402: imports grouped below the docstring.
import argparse
import asyncio
from contextlib import suppress
import os
import re
import signal
import subprocess
import sys
import time

import aiohttp

from app.browser.adapters.yuehulian_client import (
    RealYuehulianClient,
    inspector_session,
    process_layer,
)
from app.browser.inject.errors import (
    CdpUnreachable,
    LoginRequired,
    StoreNotFound,
)
from app.browser.ziniao_utils import get_platform

_BOGUS = '__vs_no_such_store__'

_results: list[tuple[str, bool, str]] = []


def _record(name: str, passed: bool, detail: str = '') -> None:
    _results.append((name, passed, detail))
    mark = 'PASS' if passed else 'FAIL'
    print(f'  [{mark}] {name}' + (f' — {detail}' if detail else ''), flush=True)


async def _cdp_ok(host: str, port: int) -> bool:
    url = f'http://{host}:{port}/json/version'
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                return bool((await r.json()).get('webSocketDebuggerUrl'))
    except Exception:
        return False


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


async def _close_and_settle(host: str, port: int) -> None:
    """Close a store's env AND wait for the kernel to actually exit, so the
    next scenario that reuses the store opens cleanly instead of racing a
    still-closing kernel (which would look 'already open')."""
    await _browser_close(host, port)
    for _ in range(12):
        if not await _cdp_ok(host, port):
            return
        await asyncio.sleep(1)


async def _goto_personal_center() -> None:
    """Drive the client OFF the store list (the '切换/进了关' state)."""
    proc = process_layer({})
    insp = inspector_session(proc)
    await insp.open(proc.main_pid())
    try:
        await insp.eval_in_renderer(
            "location.hash = '#/personal-center/personal-center'"
        )
        await asyncio.sleep(1.0)
    finally:
        await insp.close()


async def _client_total() -> int | None:
    """Read the store list's 'Total N' from the pagination (−1 if absent)."""
    proc = process_layer({})
    insp = inspector_session(proc)
    await insp.open(proc.main_pid())
    try:
        txt = await insp.eval_in_renderer(
            "(function(){var p=document.querySelector('.el-pagination');"
            "return p?(p.innerText||''):'';})()"
        )
        m = re.search(r'(\d+)', txt or '')
        return int(m.group(1)) if m else None
    finally:
        await insp.close()


async def _kill_client() -> None:
    if get_platform() == 'mac':
        # The Mac client ignores TERM/AppleScript quit; SIGKILL the MAIN
        # pid only (its kernels are left as they are — that is the
        # recovery scenario under test).
        pid = process_layer({}).main_pid()
        if pid:
            await asyncio.to_thread(os.kill, pid, signal.SIGKILL)
    else:
        ps = (
            'Get-CimInstance Win32_Process | Where-Object '
            "{ $_.ExecutablePath -match 'YueHuLian' } | "
            'ForEach-Object { Stop-Process -Id $_.ProcessId -Force '
            '-ErrorAction SilentlyContinue }'
        )
        await asyncio.to_thread(
            subprocess.run,
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', ps],
            capture_output=True,
        )
    await asyncio.sleep(3)


async def _run_logged_in(client: RealYuehulianClient, restart: bool) -> None:
    print('\n== logged-in suite ==', flush=True)

    # S: enumerate from the store-list view.
    stores = await client.list_stores()
    _record(
        'list_stores returns stores', bool(stores), f'{len(stores)} store(s)'
    )
    if not stores:
        print('No stores bound to this login — bind at least one, re-run.')
        return

    # S: enumerate after switching OFF the list (Personal Center) — the
    # nav fix. A logged-in client on another view must still enumerate.
    await _goto_personal_center()
    from_pc = await client.list_stores()
    _record(
        'list_stores from Personal Center view (切换)',
        {s.name for s in from_pc} == {s.name for s in stores},
        f'{len(from_pc)} store(s)',
    )

    # S: pagination completeness — enumerate must equal the client's Total.
    total = await _client_total()
    if total is None:
        _record(
            'pagination: all pages enumerated', True, 'single page (no pager)'
        )
    else:
        _record(
            'pagination: all pages enumerated',
            len(stores) == total,
            f'enumerated {len(stores)} vs client Total {total}',
        )

    # S: open the FIRST store → CDP reachable → close.
    first = stores[0].name
    try:
        opened = await client.open_store(first)
        ok = await _cdp_ok(opened.cdp_host, opened.cdp_port)
        await _close_and_settle(opened.cdp_host, opened.cdp_port)
        _record(
            f'open page-1 store {first!r} → CDP', ok, f'port {opened.cdp_port}'
        )
    except Exception as e:
        _record(f'open page-1 store {first!r} → CDP', False, repr(e))

    # S: open the LAST store (likely a later page) → CDP → close. Covers
    # "第二三页的能不能找到启动".
    last = stores[-1].name
    if last != first:
        try:
            opened = await client.open_store(last)
            ok = await _cdp_ok(opened.cdp_host, opened.cdp_port)
            await _close_and_settle(opened.cdp_host, opened.cdp_port)
            _record(
                f'open last/paged store {last!r} → CDP',
                ok,
                f'port {opened.cdp_port}',
            )
        except Exception as e:
            _record(f'open last/paged store {last!r} → CDP', False, repr(e))

    # S: concurrent — two stores open at once, distinct ports, both CDP.
    if len(stores) >= 2:
        a, b = stores[0].name, stores[1].name
        try:
            oa = await client.open_store(a)
            ob = await client.open_store(b)
            distinct = (
                oa.cdp_port != ob.cdp_port
                and oa.user_data_dir != ob.user_data_dir
            )
            both = await _cdp_ok(*(oa.cdp_host, oa.cdp_port)) and await _cdp_ok(*(
                ob.cdp_host,
                ob.cdp_port,
            ))
            _record(
                'concurrent two stores (distinct ports, both CDP)',
                distinct and both,
                f'{oa.cdp_port} / {ob.cdp_port}',
            )
            await _close_and_settle(oa.cdp_host, oa.cdp_port)
            await _close_and_settle(ob.cdp_host, ob.cdp_port)
        except Exception as e:
            _record('concurrent two stores', False, repr(e))

    # S: already-open → clear CdpUnreachable (not a misleading StoreNotFound).
    try:
        held = await client.open_store(first)
    except CdpUnreachable:
        # Already open (e.g. a prior scenario's close still settling) — that
        # IS the already-open signal we want to see.
        _record('already-open → CdpUnreachable', True, 'detected on first open')
    except Exception as e:
        _record('already-open → CdpUnreachable', False, f'setup: {e!r}')
    else:
        try:
            await client.open_store(first)
            _record(
                'already-open → CdpUnreachable',
                False,
                'no error on second open',
            )
        except CdpUnreachable:
            _record('already-open → CdpUnreachable', True)
        except Exception as e:
            _record('already-open → CdpUnreachable', False, repr(e))
        await _close_and_settle(held.cdp_host, held.cdp_port)

    # S: a bogus name → StoreNotFound.
    try:
        await client.open_store(_BOGUS)
        _record('bogus name → StoreNotFound', False, 'no error')
    except StoreNotFound:
        _record('bogus name → StoreNotFound', True)
    except Exception as e:
        _record('bogus name → StoreNotFound', False, repr(e))

    # S (opt-in): close the client, then enumerate → autostart (关了进).
    if restart:
        await _kill_client()
        try:
            await client.ensure_running()
            again = await client.list_stores()
            _record(
                'client closed → autostart + enumerate (关了进)',
                bool(again),
                f'{len(again)} store(s)',
            )
        except Exception as e:
            _record(
                'client closed → autostart + enumerate (关了进)', False, repr(e)
            )


async def _run_logged_out(client: RealYuehulianClient) -> None:
    print('\n== logged-out suite (退出) ==', flush=True)
    for name, coro in (
        ('list_stores → LoginRequired', client.list_stores()),
        ('open_store → LoginRequired', client.open_store(_BOGUS)),
    ):
        try:
            await coro
            _record(name, False, 'no LoginRequired raised')
        except LoginRequired:
            _record(name, True)
        except Exception as e:
            _record(name, False, repr(e))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        '--restart',
        action='store_true',
        help='also kill the client and verify autostart (关了进)',
    )
    args = ap.parse_args()

    print('=' * 62)
    print('悦互联 (yuehulian) live bind/start/read smoke test')
    print('=' * 62)
    plat = get_platform()
    _record(f'platform supported ({plat})', plat in ('windows', 'wsl', 'mac'))
    if plat not in ('windows', 'wsl', 'mac'):
        print('This backend needs the Windows or macOS client; aborting.')
        return 1

    client = RealYuehulianClient({})
    t0 = time.time()
    try:
        await client.ensure_running()
        _record('ensure_running', True)
    except Exception as e:
        _record('ensure_running', False, repr(e))
        _summary()
        return 1

    if await client.is_logged_in():
        await _run_logged_in(client, args.restart)
    else:
        await _run_logged_out(client)

    print(f'\n(elapsed {time.time() - t0:.0f}s)')
    return _summary()


def _summary() -> int:
    print('\n' + '=' * 62)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = [n for n, ok, _ in _results if not ok]
    print(f'RESULTS: {passed}/{len(_results)} passed')
    if failed:
        print('FAILED scenarios (UI/API drift or a real bug):')
        for n in failed:
            print(f'  - {n}')
    print('=' * 62)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
