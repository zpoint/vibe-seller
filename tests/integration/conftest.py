"""Shared fixtures for CDP mux integration tests.

Provides a module-scoped Chromium browser so each test file
launches the browser only once, avoiding SIGSEGV on CI from
repeated Chromium create/destroy cycles.
"""

import socket

import aiohttp
from playwright.async_api import async_playwright
import pytest_asyncio


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest_asyncio.fixture(scope='module', loop_scope='module')
async def _browser():
    """Launch Chromium once per test module.

    Yields the browser's remote-debugging port.  Per-test fixtures
    create their own CDPMuxProxy pointing at this port.
    """
    bp = _free_port()
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=True,
        args=[f'--remote-debugging-port={bp}'],
    )
    yield bp
    await browser.close()
    await pw.stop()


async def cleanup_browser_tabs(browser_port: int) -> None:
    """Close all page targets via Chrome DevTools HTTP API.

    Called between tests to prevent tab leaks in the shared
    browser instance.
    """
    try:
        async with aiohttp.ClientSession() as session:
            url = f'http://127.0.0.1:{browser_port}/json/list'
            async with session.get(url) as resp:
                targets = await resp.json()
            for t in targets:
                if t.get('type') == 'page':
                    close_url = (
                        f'http://127.0.0.1:{browser_port}/json/close/{t["id"]}'
                    )
                    async with session.get(close_url) as resp:
                        await resp.text()
    except Exception:
        pass
