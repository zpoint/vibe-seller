"""Live smoke test for the AdsPower bind / start / read flow.

Runs against the REAL AdsPower client on Windows (or WSL driving the
Windows side over 127.0.0.1). NOT a CI test: it pins the coupling to
the vendor's local API shapes (verified live 2026-11-27, client
v8.9.23). If AdsPower changes its API (error bodies, the empty-ws
Firefox answer, the local_api file), a scenario FAILS and names what
broke.

It reads `test.yaml` from the repo root (gitignored — local-only
credentials) and SKIPS cleanly when the file or the client is absent:

    python tests/e2e/adspower_live_smoke.py
    python tests/e2e/adspower_live_smoke.py --restart  # kill + relaunch

Scenarios: port discovery via the local_api file, /status, enumerate
(paged, page_size shrunk to 1), open → CDP /json/version + landing
tab = the profile's platform URL, reopen → same port, close →
local-active agrees, Firefox kernel → UnsupportedKernel with no
flowerbrowser window left, wrong key → AccountMismatch, unknown id →
StoreNotFound.

Exit code 0 ⇒ every scenario passed; 1 ⇒ at least one failed.
"""

# T201: prints in a script.
import argparse
import asyncio
from pathlib import Path
import sys

import aiohttp
import yaml

from app.browser.adapters import adspower_win
from app.browser.adapters.adspower import AdsPowerClient
from app.browser.inject.errors import (
    AccountMismatch,
    InjectError,
    StoreNotFound,
    UnsupportedKernel,
)
from app.browser.ziniao_utils import get_platform

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEST_YAML = _REPO_ROOT / 'test.yaml'

_results: list[tuple[str, bool, str]] = []


def _record(name: str, passed: bool, detail: str = '') -> None:
    _results.append((name, passed, detail))
    mark = 'PASS' if passed else 'FAIL'
    print(f'  [{mark}] {name}' + (f' — {detail}' if detail else ''), flush=True)


def _load_config() -> dict | None:
    """The adspower block of test.yaml, or None (skip cleanly)."""
    if not _TEST_YAML.is_file():
        return None
    try:
        data = yaml.safe_load(_TEST_YAML.read_text(encoding='utf-8'))
    except yaml.YAMLError:
        return None
    cfg = (data or {}).get('adspower') or {}
    if not cfg.get('api_key') or not cfg.get('test_profile_id'):
        return None
    return cfg


def _config(cfg: dict) -> dict:
    """Credentials straight into the browser config (spec-injected
    key names: <vendor>_<field>)."""
    return {'adspower_api_key': str(cfg.get('api_key') or '')}


async def _cdp_landing_url(host: str, port: int) -> str:
    """The first page tab's URL on the kernel's CDP endpoint."""
    async with aiohttp.ClientSession() as s:
        async with s.get(
            f'http://{host}:{port}/json', timeout=aiohttp.ClientTimeout(total=5)
        ) as r:
            targets = await r.json(content_type=None)
    for t in targets or []:
        if t.get('type') == 'page':
            return str(t.get('url') or '')
    return ''


async def _no_flowerbrowser_left(profile_id: str) -> bool:
    kernels = await asyncio.to_thread(adspower_win.list_kernel_processes)
    mine = AdsPowerClient._kernels_for(kernels, profile_id)
    return not mine


async def scenario_discover_and_status(client: AdsPowerClient) -> None:
    client._port = None  # force discovery through the file
    ok = await client._api_up()
    _record('discovery: local_api file + /status', ok, f'port={client._port}')
    if not ok:
        await client.ensure_running()
        _record(
            'ensure_running after discovery failed',
            client._port is not None,
            f'port={client._port}',
        )


async def scenario_enumerate(client: AdsPowerClient, ref: str) -> None:
    client.LIST_PAGE_SIZE = 1  # force paging (scenario 4)
    stores = await client.list_stores()
    client.LIST_PAGE_SIZE = 100
    refs = [s.ref for s in stores]
    _record(
        'enumerate (paged, page_size=1)',
        ref in refs,
        f'{len(stores)} profile(s); target {ref} '
        f'{"found" if ref in refs else "MISSING"}',
    )


async def scenario_open_and_tabs(
    client: AdsPowerClient, ref: str, domain: str
) -> None:
    opened = await client.open_store(ref)
    ok = opened.cdp_port > 0
    _record('open → CDP endpoint', ok, f'{opened.cdp_host}:{opened.cdp_port}')
    async with aiohttp.ClientSession() as s:
        async with s.get(
            f'http://{opened.cdp_host}:{opened.cdp_port}/json/version',
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            data = await r.json(content_type=None)
    _record('CDP /json/version answers', bool(data.get('webSocketDebuggerUrl')))
    url = await _cdp_landing_url(opened.cdp_host, opened.cdp_port)
    landed = domain in url if domain else url not in ('', 'about:blank')
    _record(
        'landing tab is the platform URL (ip_tab=0&open_tabs=0)',
        landed,
        f'{url!r}',
    )
    # Idempotent reopen: same endpoint back.
    again = await client.open_store(ref)
    _record(
        'reopen while open → same port',
        again.cdp_port == opened.cdp_port,
        f'{again.cdp_port} vs {opened.cdp_port}',
    )


async def scenario_stop(
    client: AdsPowerClient, ref: str, port: int, api_port: int
) -> None:
    await client.close_store(ref)
    await asyncio.sleep(2)
    async with aiohttp.ClientSession() as s:
        async with s.get(
            f'http://127.0.0.1:{api_port}/api/v1/browser/local-active',
            headers={'Authorization': f'Bearer {client._auth_value}'},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            data = await r.json(content_type=None)
    listed = [
        u.get('user_id') for u in (data.get('data') or {}).get('list') or []
    ]
    _record(
        'stop → local-active agrees', ref not in listed, f'running: {listed}'
    )


async def scenario_firefox(client: AdsPowerClient, ref: str) -> None:
    try:
        await client.open_store(ref)
        _record(
            'Firefox kernel → UnsupportedKernel',
            False,
            'open unexpectedly succeeded',
        )
    except UnsupportedKernel as e:
        left = await _no_flowerbrowser_left(ref)
        _record('Firefox kernel → UnsupportedKernel', True, str(e)[:60])
        _record('no flowerbrowser window left behind', left)
    except InjectError as e:
        # A different taxonomy shape IS a regression the smoke exists
        # to catch: report it, then fail loudly.
        _record(
            'Firefox kernel → UnsupportedKernel',
            False,
            f'{type(e).__name__}: {e}',
        )
        raise


async def scenario_wrong_key(cfg: dict, port: int) -> None:
    bad = AdsPowerClient({'adspower_api_key': 'not-a-real-key'})
    bad._read_port = lambda: port
    try:
        await bad.list_stores()
        _record('wrong key → AccountMismatch', False, 'no error raised')
    except AccountMismatch:
        _record('wrong key → AccountMismatch', True)
    except InjectError as e:
        _record(
            'wrong key → AccountMismatch', False, f'{type(e).__name__}: {e}'
        )
        raise


async def scenario_unknown_id(client: AdsPowerClient) -> None:
    try:
        await client.open_store('zzzz9999')
        _record('unknown id → StoreNotFound', False, 'no error raised')
    except StoreNotFound:
        _record('unknown id → StoreNotFound', True)
    except InjectError as e:
        _record('unknown id → StoreNotFound', False, f'{type(e).__name__}: {e}')
        raise


async def main(cfg: dict, restart: bool) -> int:
    platform = get_platform()
    if platform not in ('windows', 'wsl'):
        print(f'unsupported platform for the live client: {platform}')
        return 1
    if restart:
        print('* killing the AdsPower client (scenario: not running)')

        def _kill() -> int:
            import subprocess

            return subprocess.run(
                [
                    'powershell.exe',
                    '-NoProfile',
                    '-Command',
                    "taskkill /F /IM 'AdsPower Global.exe'",
                ],
                capture_output=True,
                timeout=60,
            ).returncode

        await asyncio.to_thread(_kill)
        await asyncio.sleep(3)
    client = AdsPowerClient(_config(cfg))
    ref = str(cfg['test_profile_id'])
    fox = str(cfg.get('firefox_profile_id') or '')
    domain = str(cfg.get('test_domain') or 'www.amazon.com')

    await scenario_discover_and_status(client)
    api_port = client._port or 0
    await scenario_enumerate(client, ref)
    await scenario_open_and_tabs(client, ref, domain)
    await scenario_stop(client, ref, api_port, api_port)
    if fox:
        await scenario_firefox(client, fox)
    await scenario_wrong_key(cfg, api_port)
    await scenario_unknown_id(client)

    failed = [r for r in _results if not r[1]]
    print()
    for name, ok, detail in _results:
        print(f'  [{"PASS" if ok else "FAIL"}] {name}')
    print(f'\n{len(_results) - len(failed)}/{len(_results)} passed')
    return 1 if failed else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--restart',
        action='store_true',
        help='kill the client first (scenario: client not running)',
    )
    args = parser.parse_args()
    _cfg = _load_config()
    if _cfg is None:
        print('test.yaml (adspower block) not found — skipping.')
        sys.exit(0)
    sys.exit(asyncio.run(main(_cfg, args.restart)))
