"""BitBrowser CDP tab helpers: the workbench-tab hygiene after_open
runs on the opened kernel's debug endpoint.

Split out of ``bitbrowser`` (the client + spec) per the vendor-module
convention for large adapters. The helpers speak the kernel's CDP
surface directly (/json/list, /json/version + the browser-level
WebSocket for Target.closeTarget / Target.activateTarget), the same
aiohttp-over-CDP pattern the inject family's tab handling uses —
everything here
is best-effort: a failed command logs a warning and never fails the
open (the env IS open and CDP answers; tab hygiene must not turn into
a task failure).
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit

import aiohttp

from app.browser.inject.client import OpenedStore

logger = logging.getLogger(__name__)

# The workbench tab BitBrowser opens beside the platform page (verified
# live for both workbench='localserver' and 'chuhai2345' — the latter
# normalizes to the same URL on client 7.2.0).
WORKBENCH_HOST = 'console.bitbrowser.net'

_CDP_TIMEOUT = aiohttp.ClientTimeout(total=5)


def platform_hostname(platform: object) -> str:
    """The store's platform hostname ('https://www.amazon.com/' →
    'amazon.com') — the one list field besides name/seq worth showing."""
    if not isinstance(platform, str) or not platform:
        return ''
    host = urlsplit(platform if '://' in platform else f'https://{platform}')
    hostname = (host.hostname or platform).lower()
    return hostname.removeprefix('www.')


def is_http_page(target: dict) -> bool:
    """An http(s) page target — extension and chrome:// pages are not
    somewhere to land the agent."""
    return str(target.get('url', '')).startswith(('http://', 'https://'))


def preferred_tab(tabs: list[dict], platform_host: str) -> dict:
    """The tab to activate: the one on the profile's platform host when
    known, else the first http(s) page."""
    if platform_host:
        for tab in tabs:
            if platform_host in str(tab.get('url', '')):
                return tab
    return tabs[0]


async def browser_ws_call(opened: OpenedStore, method: str, params: dict):
    """One browser-level CDP command on the opened kernel's debug WS."""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f'http://{opened.cdp_host}:{opened.cdp_port}/json/version',
                timeout=_CDP_TIMEOUT,
            ) as r:
                ws_url = (await r.json(content_type=None)).get(
                    'webSocketDebuggerUrl'
                )
            if not ws_url:
                return None
            async with s.ws_connect(ws_url, max_msg_size=0) as ws:
                await ws.send_json({
                    'id': 1,
                    'method': method,
                    'params': params,
                })
                while True:
                    msg = await asyncio.wait_for(ws.receive_json(), timeout=5)
                    if msg.get('id') == 1:
                        return msg.get('result')
    except Exception as e:  # best-effort tab hygiene; never fatal
        logger.warning('bitbrowser CDP %s failed: %s', method, e)
        return None


async def page_targets(opened: OpenedStore) -> list[dict] | None:
    """Page targets from the kernel's /json/list (None = listing
    failed — transient, retried by the caller; [] = no pages)."""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f'http://{opened.cdp_host}:{opened.cdp_port}/json/list',
                timeout=_CDP_TIMEOUT,
            ) as r:
                targets = await r.json(content_type=None)
    except (TimeoutError, aiohttp.ClientError, ValueError) as e:
        logger.warning('bitbrowser /json/list failed: %s', e)
        return None
    if not isinstance(targets, list):
        return None
    return [t for t in targets if t.get('type') == 'page']


def _target_id(target: dict) -> str:
    """A target's id for Target.* commands.

    The HTTP ``/json/list`` endpoint names it ``id`` (the CDP
    Target.getTargets response calls the same value ``targetId``) —
    take either. Found live: closing with the wrong key fails
    silently and the workbench tab survives after_open.
    """
    return target.get('targetId') or target.get('id') or ''


async def close_target(opened: OpenedStore, target: dict) -> None:
    await browser_ws_call(
        opened, 'Target.closeTarget', {'targetId': _target_id(target)}
    )


async def activate_target(opened: OpenedStore, target: dict) -> None:
    await browser_ws_call(
        opened, 'Target.activateTarget', {'targetId': _target_id(target)}
    )
