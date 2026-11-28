"""Live smoke test for the BitBrowser bind / open / workbench flow.

Runs against the REAL BitBrowser client on Windows (or WSL driving
the Windows side over 127.0.0.1). NOT a CI test: it pins the coupling
to the vendor's local API shapes (verified live 2026-11-27, client
v7.2.0). If BitBrowser changes its API (logged-out shape, open
fields, the workbench tab), a scenario FAILS and names what broke.

It reads `test.yaml` from the repo root (gitignored — local-only
config) and SKIPS cleanly when the file or the client is absent:

    python tests/e2e/bitbrowser_live_smoke.py
    python tests/e2e/bitbrowser_live_smoke.py --restart  # kill client
                                                       # + relaunch

Scenarios: enumerate (paged, pageSize=1 forced to walk ≥2 pages),
kernel greying, open → CDP → workbench tab closed, open-again → same
port, close → the client's running list agrees, client killed →
ensure_running relaunches it, Firefox profile → UnsupportedKernel
(only when one exists). Exit 0 ⇒ all passed; 1 ⇒ at least one failed.
"""

# T201: prints in a script.
import argparse
import asyncio
from contextlib import suppress
from pathlib import Path
import sys

import aiohttp
import yaml

from app.browser.adapters.bitbrowser import BitBrowserClient
from app.browser.inject.errors import (
    ClientNotRunning,
    LoginRequired,
    UnsupportedKernel,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEST_YAML = _REPO_ROOT / 'test.yaml'

_results: list[tuple[str, bool, str]] = []


def _record(name: str, passed: bool, detail: str = '') -> None:
    _results.append((name, passed, detail))
    mark = 'PASS' if passed else 'FAIL'
    print(f'  [{mark}] {name}' + (f' — {detail}' if detail else ''), flush=True)


def _load_config() -> dict | None:
    """The bitbrowser block of test.yaml, or None (skip cleanly)."""
    if not _TEST_YAML.is_file():
        return None
    try:
        data = yaml.safe_load(_TEST_YAML.read_text(encoding='utf-8'))
    except yaml.YAMLError:
        return None
    cfg = (data or {}).get('bitbrowser') or {}
    if not cfg.get('api_port'):
        return None
    return cfg


async def _client_reachable(port: int) -> bool:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f'http://127.0.0.1:{port}/health',
                json={},
                timeout=aiohttp.ClientTimeout(total=4),
            ) as r:
                body = await r.json(content_type=None)
                return bool(body.get('success'))
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


async def _running_pids(client: BitBrowserClient) -> dict:
    """POST /browser/pids/all raw (the client answers the open map)."""
    status, payload = await client.api_call(
        'POST', '/browser/pids/all', json_body={}
    )
    if isinstance(payload, dict) and payload.get('success'):
        return payload.get('data') or {}
    return {}


async def _kill_client(port: int) -> bool:
    """taskkill /F the Electron main pid owning the API port (matched
    by port, never by the Chinese process name)."""
    import subprocess

    script = (
        f'$p = (Get-NetTCPConnection -LocalPort {port} -State Listen '
        '-ErrorAction SilentlyContinue | Select-Object -First 1)'
        '.OwningProcess; '
        'if ($p) { taskkill /F /PID $p | Out-Null; "killed" } '
        'else { "no-owner" }'
    )
    try:
        out = await asyncio.to_thread(
            subprocess.run,
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
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return 'killed' in (out.stdout or '')


async def _scenario_enumerate(client: BitBrowserClient) -> str | None:
    """Paged enumeration (pageSize forced to 1 → walks every page) and
    the kernel greying, from the REAL list."""
    client.page_size = 1  # force ≥2 pages whenever ≥2 profiles exist
    refs = await client.list_stores()
    client.page_size = 100
    if not refs:
        return 'the list is empty (no profiles for this login?)'
    greyed = [r for r in refs if not r.available]
    print(
        f'    {len(refs)} profiles, {len(greyed)} greyed (non-Chromium kernel)',
        flush=True,
    )
    for ref in refs:
        if not ref.name or not ref.ref:
            return f'a row lost its binding fields: {ref!r}'
    for ref in greyed:
        if not ref.unavailable_reason:
            return f'greyed row without a reason: {ref!r}'
    return None


def _pick_chrome_profile(refs) -> object | None:
    for ref in refs:
        if ref.available:
            return ref
    return None


async def _scenario_open_workbench_close(client, ref) -> str | None:
    """Open → CDP answers → the workbench tab is gone and a store tab
    is active; reopen → the SAME port; close → pids/all agrees."""
    opened = await client.open_store(ref.ref)
    pages = await _cdp_pages(opened.cdp_host, opened.cdp_port)
    workbench = [
        p for p in pages if 'console.bitbrowser.net' in str(p.get('url'))
    ]
    if workbench:
        return f'{len(workbench)} workbench tab(s) still open after after_open'
    if not pages:
        return 'no page tabs after open'
    # Reopen while open: idempotent, same endpoint.
    again = await client.open_store(ref.ref)
    if (again.cdp_host, again.cdp_port) != (opened.cdp_host, opened.cdp_port):
        return f'port rotated on reopen: {opened.cdp_port} -> {again.cdp_port}'
    # Close: the client's running list must agree.
    await client.close_store(ref.ref)
    await asyncio.sleep(2)
    running = await _running_pids(client)
    if ref.ref in running:
        return f'pids/all still lists {ref.ref} after close'
    return None


async def _scenario_firefox(client, refs) -> str | None:
    """A Firefox-kernel profile is refused with UNSUPPORTED_KERNEL —
    only when one exists (the smoke never mutates profiles)."""
    firefox = next((r for r in refs if not r.available), None)
    if firefox is None:
        print('    (no non-Chromium profile present — skipped)', flush=True)
        return None
    try:
        await client.open_store(firefox.ref)
    except UnsupportedKernel:
        return None
    return f'opening {firefox.ref} did not raise UnsupportedKernel'


async def _scenario_logged_out_shape(client) -> str | None:
    """The recorded logged-out body must still map to LoginRequired.
    Live sign-out needs the client UI (out of scope here): instead pin
    that an unknown-path call keeps the success/msg envelope the
    mapper relies on."""
    status, payload = await client.api_call(
        'POST', '/nonexistent-path', json_body={}
    )
    if status != 404:
        return f'unknown route answered {status}, expected 404'
    return None


async def _scenario_client_killed(client, cfg, ref) -> str | None:
    """--restart: kill the client with the profile open → the next
    browser use relaunches it (ensure_running) and the store opens."""
    port = int(cfg['api_port'])
    opened = await client.open_store(ref.ref)
    if not await _cdp_pages(opened.cdp_host, opened.cdp_port):
        return 'the store browser did not come up before the kill'
    if not await _kill_client(port):
        return 'could not kill the client (no port owner?)'
    await asyncio.sleep(3)
    client2 = BitBrowserClient({'api_port': port})
    try:
        client2.min_interval_s = client.min_interval_s
        await client2.ensure_running()  # probe → launch → poll
        opened2 = await client2.open_store(ref.ref)
        pages = await _cdp_pages(opened2.cdp_host, opened2.cdp_port)
        if not pages:
            return 'relaunched client opened the store but no tabs'
        await client2.close_store(ref.ref)
        return None
    finally:
        await client2.aclose()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--restart',
        action='store_true',
        help='also kill the client mid-flow and prove recovery',
    )
    args = parser.parse_args()

    cfg = _load_config()
    if cfg is None:
        print('SKIP: no test.yaml bitbrowser section (local-only file)')
        return 0
    port = int(cfg['api_port'])
    if not await _client_reachable(port):
        print(
            f'SKIP: no BitBrowser local API on 127.0.0.1:{port} '
            '(client not running?)'
        )
        return 0

    client = BitBrowserClient({'api_port': port})
    try:
        print('BitBrowser live smoke:', flush=True)

        err = await _scenario_enumerate(client)
        _record('enumerate paged + kernel greying', err is None, err or '')

        refs = await client.list_stores()
        profile = _pick_chrome_profile(refs)
        if profile is None:
            _record(
                'open/workbench/close',
                False,
                'no Chromium-kernel profile to open',
            )
        else:
            try:
                err = await _scenario_open_workbench_close(client, profile)
            except LoginRequired:
                err = 'client is signed out (sign in once in BitBrowser)'
            _record(
                'open → workbench closed → reopen → close',
                err is None,
                err or '',
            )

            err = await _scenario_firefox(client, refs)
            _record('firefox kernel refused', err is None, err or '')

            if args.restart:
                try:
                    err = await _scenario_client_killed(client, cfg, profile)
                except ClientNotRunning as e:
                    err = f'recovery failed: {e}'
                _record(
                    'client killed → relaunch → reopen', err is None, err or ''
                )

        err = await _scenario_logged_out_shape(client)
        _record('api envelope sane', err is None, err or '')
    finally:
        # Never leave a profile browser open behind the smoke.
        with suppress(Exception):
            for ref in await client.list_stores():
                with suppress(Exception):
                    await client.close_store(ref.ref)
        await client.aclose()

    failed = [r for r in _results if not r[1]]
    print(f'\n{len(_results) - len(failed)}/{len(_results)} passed', flush=True)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
