"""E2E test: Browser session reuse across tasks on the same store.

Spins up a local cookie-based "auth" server:
  - /do-login: sets a session_token cookie
  - /dashboard: returns different content based on cookie presence

Tests that cookies persist across sequential tasks on the same store,
proving browser session reuse works end-to-end:
  1. Task A navigates to /do-login (sets cookie)
  2. Task B navigates to /dashboard WITHOUT logging in (should see
     logged-in content because the browser session is reused)

Requires: provider credentials matching E2E_PROVIDER_MAP.
"""

from http.cookies import SimpleCookie
import http.server
import logging
import threading
import time

import pytest

from tests.e2e.conftest import BASE_URL
from tests.e2e.e2e_helpers import (
    create_task,
    poll_task_status,
)

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.e2e]

# -- Cookie-based auth server --

LOGIN_RESPONSE = """\
<!DOCTYPE html>
<html><head><title>Login OK</title></head>
<body>
  <h1>Login successful</h1>
  <p>Session cookie has been set. You are now authenticated.</p>
</body></html>
"""

DASHBOARD_LOGGED_IN = """\
<!DOCTYPE html>
<html><head><title>Dashboard</title></head>
<body>
  <h1>Welcome back, testuser! You are logged in.</h1>
  <p>Your session cookie was found. Authentication persisted.</p>
</body></html>
"""

DASHBOARD_LOGGED_OUT = """\
<!DOCTYPE html>
<html><head><title>Dashboard</title></head>
<body>
  <h1>NOT LOGGED IN</h1>
  <p>No session cookie found. Please login first.</p>
  <a href="/do-login">Login</a>
</body></html>
"""


class _AuthHandler(http.server.BaseHTTPRequestHandler):
    """Minimal cookie-based auth handler."""

    # Shared list to record dashboard auth results server-side
    dashboard_auth_log: list[bool] = []

    def do_GET(self):
        if self.path == '/do-login' or self.path.startswith('/do-login?'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header(
                'Set-Cookie',
                'session_token=authenticated_testuser; Path=/; Max-Age=3600',
            )
            self.end_headers()
            self.wfile.write(LOGIN_RESPONSE.encode())

        elif self.path == '/dashboard':
            cookie_header = self.headers.get('Cookie', '')
            cookies = SimpleCookie(cookie_header)
            token = cookies.get('session_token')
            authenticated = bool(
                token and token.value == 'authenticated_testuser'
            )

            _AuthHandler.dashboard_auth_log.append(authenticated)
            logger.info('Dashboard request: cookie_present=%s', authenticated)

            if authenticated:
                body = DASHBOARD_LOGGED_IN
            else:
                body = DASHBOARD_LOGGED_OUT

            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(body.encode())

        elif self.path == '/favicon.ico':
            self.send_response(404)
            self.end_headers()

        else:
            self.send_error(404)

    def log_message(self, format, *args):
        logger.debug('AuthServer: %s', format % args)


@pytest.fixture(scope='module')
def auth_site():
    """Start a local HTTP server with cookie-based auth."""
    server = http.server.HTTPServer(('127.0.0.1', 0), _AuthHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info('Auth test server started on port %d', port)
    yield f'http://127.0.0.1:{port}'
    server.shutdown()
    server.server_close()


@pytest.fixture(scope='module')
def test_store(api_client):
    """Create a dedicated store for session reuse tests."""
    tag = int(time.time())
    resp = api_client.post(
        f'{BASE_URL}/api/stores',
        json={'name': f'e2e-session-reuse-{tag}'},
    )
    resp.raise_for_status()
    return resp.json()


# -- Tests --


class TestBrowserSessionReuse:
    """Cookie set by Task A persists into Task B on the same store."""

    def test_cookie_persists_across_tasks_same_store(
        self,
        api_client,
        test_store,
        auth_site,
    ):
        """
        Task 1: navigate to /do-login (server sets session cookie).
        Task 2: navigate to /dashboard WITHOUT logging in.
        Assert: Task 2 sees "Welcome back" (cookie persisted via
        browser session reuse on the same store).
        """
        _AuthHandler.dashboard_auth_log.clear()
        store_id = test_store['id']
        tag = int(time.time())

        # -- Task 1: login (sets cookie) --
        task1 = create_task(
            api_client,
            f'Session login {tag}',
            store_id=store_id,
            description=(
                f'Use the browser-use CLI to open {auth_site}/do-login '
                f'in the browser. Wait for the page to load. '
                f'The page will say "Login successful" and set a cookie. '
                f'Once you see that confirmation, report that login is done.'
            ),
        )
        task1_id = task1['id']
        logger.info('Task 1 (login): %s', task1_id[:8])

        result1 = poll_task_status(
            api_client,
            task1_id,
            {'completed'},
            fail_statuses={'failed'},
        )
        assert result1['status'] == 'completed', (
            f'Task 1 (login) should complete, got: {result1["status"]} '
            f'error={result1.get("error")}'
        )

        # -- Task 2: check dashboard (should be auto-logged-in) --
        task2 = create_task(
            api_client,
            f'Session check {tag}',
            store_id=store_id,
            description=(
                f'Use the browser-use CLI to open {auth_site}/dashboard '
                f'in the browser. Do NOT navigate to /do-login. '
                f'Do NOT try to login. Just read the page heading. '
                f'Report the exact heading text you see on the page.'
            ),
        )
        task2_id = task2['id']
        logger.info('Task 2 (dashboard check): %s', task2_id[:8])

        result2 = poll_task_status(
            api_client,
            task2_id,
            {'completed'},
            fail_statuses={'failed'},
        )
        assert result2['status'] == 'completed', (
            f'Task 2 (dashboard) should complete, got: {result2["status"]} '
            f'error={result2.get("error")}'
        )

        # Server-side check: the auth server recorded whether the
        # cookie was present on /dashboard requests.  This is
        # deterministic — no LLM interpretation involved.
        assert any(_AuthHandler.dashboard_auth_log), (
            f'Cookie did NOT persist: server saw no authenticated '
            f'/dashboard request. '
            f'Auth log: {_AuthHandler.dashboard_auth_log}'
        )
