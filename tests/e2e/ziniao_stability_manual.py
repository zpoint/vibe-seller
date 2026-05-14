"""Test Ziniao target stability with real URL vs about:blank.

Runs the stability check directly against the Ziniao CDP proxy
to verify that navigated targets survive Ziniao's initialization.

Usage:
    source .venv/bin/activate
    python tests/e2e/test_ziniao_stability_manual.py

Requires Ziniao to be running and a store configured.
"""

# (E402: imports after dotenv load; T201: print in script;
#  PLC0415: sqlite3 imported inside function)

# Load .env before app imports so JWT_SECRET is available
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / '.env')

import asyncio
import logging
import os
import sys
import time
import uuid

import aiohttp

from app.browser.ziniao import ZiniaoBackend
from app.browser.ziniao_utils import ensure_ziniao_running
from app.config import LOCALHOST
from app.utils.crypto import decrypt_password

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(name)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)


def _get_ziniao_config() -> dict:
    """Load Ziniao config from the database."""
    import sqlite3

    db_path = os.path.expanduser('~/.vibe-seller/data/vibe_seller.db')
    conn = sqlite3.connect(db_path)

    # Get ziniao account credentials
    account = conn.execute(
        'SELECT company, username, encrypted_password, '
        'socket_port, client_path '
        'FROM ziniao_accounts LIMIT 1'
    ).fetchone()
    if not account:
        print('ERROR: No Ziniao account found in DB')
        sys.exit(1)

    company, username, enc_password, socket_port, client_path = account
    password = decrypt_password(enc_password)

    # Get store browser_oauth for first ziniao store
    store = conn.execute(
        'SELECT name, browser_oauth FROM stores '
        "WHERE browser_backend='ziniao' LIMIT 1"
    ).fetchone()
    if not store:
        print('ERROR: No Ziniao store found in DB')
        sys.exit(1)

    store_name, enc_oauth = store
    try:
        browser_oauth = decrypt_password(enc_oauth)
    except Exception:
        browser_oauth = enc_oauth

    conn.close()

    return {
        'company': company,
        'username': username,
        'password': password,
        'socket_port': socket_port or 16851,
        'client_path': client_path or 'ziniao',
        'browser_oauth': browser_oauth,
        'proxy_port': 9225,
        'store_name': store_name,
    }


async def _manual_stability_test(
    proxy_port: int,
    url: str,
    survival_secs: float = 5.0,
    label: str = '',
) -> bool:
    """Create a target with the given URL and see if it survives.

    Returns True if target survived, False if destroyed.
    """
    ws_url = f'ws://{LOCALHOST}:{proxy_port}/client-test-{uuid.uuid4().hex[:8]}'
    destroyed = False
    target_id = None
    start = time.monotonic()

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url, timeout=10) as ws:
                # Create target
                await ws.send_json({
                    'id': 1,
                    'method': 'Target.createTarget',
                    'params': {'url': url},
                })

                deadline = time.monotonic() + survival_secs
                while time.monotonic() < deadline:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        msg = await asyncio.wait_for(
                            ws.receive_json(),
                            timeout=remaining,
                        )
                    except TimeoutError:
                        break

                    if msg.get('id') == 1 and 'result' in msg:
                        target_id = msg['result'].get('targetId')
                        elapsed = time.monotonic() - start
                        logger.info(
                            '[%s] Target created: %s (%.1fs)',
                            label,
                            target_id[:16] if target_id else '?',
                            elapsed,
                        )
                    if msg.get('method') == 'Target.targetDestroyed':
                        tid = msg.get('params', {}).get('targetId', '')
                        if tid == target_id:
                            elapsed = time.monotonic() - start
                            destroyed = True
                            logger.warning(
                                '[%s] Target DESTROYED after '
                                '%.1fs! (target=%s)',
                                label,
                                elapsed,
                                tid[:16],
                            )
                            break

                if not destroyed:
                    elapsed = time.monotonic() - start
                    logger.info(
                        '[%s] Target survived %.1fs ok',
                        label,
                        elapsed,
                    )

                # Cleanup
                if target_id and not destroyed:
                    await ws.send_json({
                        'id': 2,
                        'method': 'Target.closeTarget',
                        'params': {'targetId': target_id},
                    })
                    try:
                        await asyncio.wait_for(ws.receive_json(), timeout=2)
                    except TimeoutError:
                        pass

    except Exception as e:
        logger.error('[%s] Error: %s', label, e)
        destroyed = True

    return not destroyed


async def main():
    print('=' * 60)
    print('Ziniao Target Stability Test')
    print('=' * 60)

    # Load config
    config = _get_ziniao_config()
    logger.info(
        'Store: %s, Company: %s, Port: %d, OAuth: %s...',
        config['store_name'],
        config['company'],
        config['socket_port'],
        config['browser_oauth'][:8],
    )

    # Step 1: Ensure Ziniao is running
    logger.info('Ensuring Ziniao client is running...')
    await ensure_ziniao_running(
        config['socket_port'],
        config['client_path'],
        {
            'company': config['company'],
            'username': config['username'],
            'password': config['password'],
        },
    )
    logger.info('Ziniao client is running')

    # Step 2: Start browser + CDP proxy
    logger.info('Starting Ziniao browser session...')
    backend = ZiniaoBackend()
    session_info = None
    try:
        session_info = await backend.start(config)
        logger.info('Browser started, CDP port: %d', session_info.cdp_port)

        # Step 3: Run comparative tests
        print()
        print('=' * 60)
        print('TEST 1: about:blank (old behavior)')
        print('=' * 60)
        blank_ok = await _manual_stability_test(
            config['proxy_port'],
            'about:blank',
            survival_secs=5.0,
            label='about:blank',
        )

        print()
        print('=' * 60)
        print('TEST 2: http://example.com (new behavior)')
        print('=' * 60)
        example_ok = await _manual_stability_test(
            config['proxy_port'],
            'http://example.com',
            survival_secs=5.0,
            label='example.com',
        )

        print()
        print('=' * 60)
        print('TEST 3: https://www.baidu.com (real nav)')
        print('=' * 60)
        baidu_ok = await _manual_stability_test(
            config['proxy_port'],
            'https://www.baidu.com',
            survival_secs=5.0,
            label='baidu.com',
        )

        # Summary
        print()
        print('=' * 60)
        print('RESULTS')
        print('=' * 60)
        print(f'  about:blank:   {"SURVIVED" if blank_ok else "DESTROYED"}')
        print(f'  example.com:   {"SURVIVED" if example_ok else "DESTROYED"}')
        print(f'  baidu.com:     {"SURVIVED" if baidu_ok else "DESTROYED"}')

        if blank_ok and example_ok and baidu_ok:
            print('\nAll targets survived -- Ziniao is stable!')
        elif blank_ok and not example_ok:
            print(
                '\nabout:blank survives but real URLs get killed '
                '-- confirms the bug!'
            )
        elif not blank_ok:
            print('\nEven about:blank gets killed -- Ziniao not ready yet')

    finally:
        if session_info:
            await backend.stop(session_info)


if __name__ == '__main__':
    asyncio.run(main())
