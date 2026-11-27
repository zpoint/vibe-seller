"""Two servers on one machine get their own CDP proxy ports (PR C1).

Tier-1 e2e for the ownership handshake: server 1 opens a store browser
and holds a proxy port; a store opened on server 2 binds its OWN port
(never server 1's), each mux answers ``/vibe/instance`` with ITS
server's instance id, and server 2's wrapper bakes server 2's port +
id. Before the fix, both servers allocated the same ports and the
wrapper's liveness probe accepted whatever answered — a test task on
one server drove the other server's store browser.

(The "first store gets the base port" pin is a unit test —
tests/unit/test_browser/test_proxy_port_allocation.py — because a
shared e2e run may legitimately have other browsers holding the base.)

Run: ``pytest tests/e2e/test_cdp_instance_ports.py --e2e -v``
(headless Chromium; the servers are owned by this module).
"""

import json
import socket

import httpx
import pytest

from tests.e2e.e2e_helpers import login_client
from tests.e2e.own_server import AGENT_SERVER_ENV, OwnServer, pick_free_port

pytestmark = [pytest.mark.e2e]


def _probe_instance(port: int, timeout: float = 2.0) -> str | None:
    """The instance id a listener serves on /vibe/instance (raw socket,
    same contract as app.browser.cdp_probe.proxy_ownership)."""
    try:
        with socket.create_connection(
            ('127.0.0.1', port), timeout=timeout
        ) as sock:
            sock.settimeout(timeout)
            sock.sendall(
                f'GET /vibe/instance HTTP/1.1\r\n'
                f'Host: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n'.encode()
            )
            buf = b''
            while b'\r\n\r\n' not in buf:
                chunk = sock.recv(512)
                if not chunk:
                    break
                buf += chunk
            body = buf.partition(b'\r\n\r\n')[2]
            return json.loads(body.decode()).get('instance')
    except Exception:
        return None


def _start_store_browser(base_url: str, name: str) -> dict:
    """Create a chrome store, start its browser, return the session."""
    with httpx.Client(base_url=base_url, timeout=120.0) as client:
        login_client(client, base_url)
        store = client.post('/api/stores', json={'name': name})
        store.raise_for_status()
        store_id = store.json()['id']
        resp = client.post(f'/api/stores/{store_id}/browser/start')
        assert resp.status_code == 200, resp.text
        return {
            'store_id': store_id,
            'proxy_port': resp.json()['proxy_port'],
            'instance_id': resp.json()['instance_id'],
        }


class TestTwoServersOwnProxyPorts:
    def test_server_2_binds_its_own_port_and_wrapper_follows(self, tmp_path):
        # Two OWNED servers: dedicated ports, dedicated homes, headless
        # browsers (CI=true forces it), no outside calls. The shared
        # per-instance base keeps the run deterministic (nothing else
        # uses 95xx in CI) and lock-free on a dev machine — server 2
        # walks past server 1's bind, which is exactly the behaviour
        # under test.
        env = {
            **AGENT_SERVER_ENV,
            'CI': 'true',
            'VIBE_CDP_PROXY_BASE': '9500',
        }
        server1 = OwnServer(
            port=pick_free_port(), home=tmp_path / 'home1', env=env
        )
        server2 = OwnServer(
            port=pick_free_port(), home=tmp_path / 'home2', env=env
        )
        server1.start()
        server2.start()
        try:
            s1 = _start_store_browser(server1.base_url, 'c1-e2e-one')
            s2 = _start_store_browser(server2.base_url, 'c1-e2e-two')

            # Distinct instance ids, and each port answers with ITS
            # server's id — never the other's.
            assert s1['instance_id'] != s2['instance_id']
            assert s1['proxy_port'] != s2['proxy_port']
            assert _probe_instance(s1['proxy_port']) == s1['instance_id']
            assert _probe_instance(s2['proxy_port']) == s2['instance_id']

            # Server 2's wrapper bakes server 2's port + id, so its
            # agents can only ever attach to server 2's proxy.
            wrapper = tmp_path / 'home2' / 'bin' / 'c1-e2e-two' / 'browser-use'
            assert wrapper.is_file()
            text = wrapper.read_text()
            assert f'PROXY_PORT={s2["proxy_port"]}' in text
            assert f'VIBE_PROXY_ID="{s2["instance_id"]}"' in text
        finally:
            server1.stop()
            server2.stop()
