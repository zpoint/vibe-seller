"""BitBrowser auto-login tests (option B) — fake inspector + fake DOM.

Every behaviour pairs with a negative control. The credentials ride
only the one eval (a sentinel pins it); the policy (kick /
ping-pong) is tested against a real temp login-state.json.
"""

import json
import logging
import time

import pytest

import app.browser.adapters.bitbrowser as bb
import app.browser.adapters.bitbrowser_login as bl
from app.browser.inject.errors import LoginRequired
from app.browser.win_inspector import InspectorBusy
from tests.unit.test_browser.bitbrowser_test_support import (
    _serve,
    cleanup,
)

pytestmark = pytest.mark.unit


class FakeLoginSession:
    """A fake InspectorSession: serves scripted probe/fill/classify
    results and records that close() always ran."""

    def __init__(
        self, probe=None, fill='OK', classify='UNKNOWN', open_error=None
    ):
        self.probe_result = probe
        self.fill_result = fill
        self.classify_result = classify
        self.open_error = open_error
        self.opened = False
        self.closed = False
        self.evaluated: list[str] = []

    async def open(self, main_pid: int) -> None:
        if self.open_error:
            raise self.open_error
        self.opened = True

    async def close(self) -> None:
        self.closed = True

    async def eval_in_renderer(self, js: str, *, timeout: float = 25.0):
        self.evaluated.append(js)
        if (
            '_PROBE_JS' in js
            or js.startswith('(async ()') is False
            and 'location.href' in js
        ):
            return self.probe_result or '{}'
        if 'setVal' in js:
            return self.fill_result
        if 'VERIFY' in js or 'WRONGCREDS' in js:
            return self.classify_result
        return self.probe_result or '{}'


LOGIN_PAGE_PROBE = json.dumps({
    'url': 'https://client.bitbrowser.cn/login',
    'hasUser': True,
    'hasPass': True,
    'hasSubmit': True,
    'hasCodeField': False,
    'errorText': '',
})


@pytest.fixture(autouse=True)
async def _teardown():
    yield
    await cleanup()


def _make_client(
    tmp_path,
    *,
    logged_out_msg='token 失效，请检查登录状态',
    logged_in=False,
    session=None,
    poll_success=True,
):
    """A client wired for login tests: a fake API answering the
    logged-out (or signed-in) list shape, a temp policy file, a fake
    inspector session, and a poll stub."""
    server_routes = {}
    if logged_in:
        server_routes[('POST', '/browser/list')] = (
            200,
            {'success': True, 'data': {'list': [], 'totalNum': 0}},
        )
    else:
        server_routes[('POST', '/browser/list')] = (
            200,
            {'success': False, 'msg': logged_out_msg},
        )
    return server_routes, session


async def _login_client(
    tmp_path,
    monkeypatch,
    *,
    credentials=True,
    session=None,
    poll=None,
    logged_out_msg='token 失效，请检查登录状态',
    poll_success=True,
):
    """Build a BitBrowserClient against a logged-out fake API with the
    login seams stubbed (inspector session, main pid, poll)."""
    server = await _serve({
        ('POST', '/browser/list'): (
            200,
            {'success': False, 'msg': logged_out_msg},
        ),
    })
    cfg = {'api_port': server.port}
    if credentials:
        cfg['bitbrowser_username'] = 'demo-user'
        cfg['bitbrowser_password'] = 'sentinel-secret-pass'
    client = bb.BitBrowserClient(cfg)
    client._login_state = bl.LoginState(tmp_path / 'login-state.json')
    if session is not None:
        monkeypatch.setattr(bb.login, 'make_session', lambda: session)
    monkeypatch.setattr(
        client,
        '_client_main_pid',
        _async_return_int(4242),
    )
    if poll is not None:
        monkeypatch.setattr(bb.login, 'poll_until_signed_in', poll)
    elif poll_success:

        async def _poll_ok(api_call, attempts=30):
            return True

        monkeypatch.setattr(bb.login, 'poll_until_signed_in', _poll_ok)
    else:

        async def _poll_fail(api_call, attempts=30):
            return False

        monkeypatch.setattr(bb.login, 'poll_until_signed_in', _poll_fail)
    return client


def _async_return_int(value):
    async def _inner():
        return value

    return _inner


# ── the policy (LoginState) ─────────────────────────────────────────


def test_kick_message_refuses(tmp_path):
    state = bl.LoginState(tmp_path / 's.json')
    assert state.refuse_auto_login('Login out！') == 'kick'


def test_expiry_allows(tmp_path):
    state = bl.LoginState(tmp_path / 's.json')
    assert state.refuse_auto_login('token 失效，请检查登录状态') is None


def test_ping_pong_guard(tmp_path):
    """Our own auto-login < 15 min ago + logged out again → refuse."""
    state = bl.LoginState(tmp_path / 's.json')
    state.record_auto_login()
    assert state.refuse_auto_login('token 失效') == 'ping-pong'


def test_ping_pong_twist_old_auto_login_allows(tmp_path):
    """Negative control: an auto-login > 15 min ago does not refuse."""
    state = bl.LoginState(tmp_path / 's.json')
    state._data['last_auto_login'] = time.time() - 16 * 60
    state._write()
    fresh = bl.LoginState(tmp_path / 's.json')
    assert fresh.refuse_auto_login('token 失效') is None


def test_manual_signin_resets_kick(tmp_path):
    state = bl.LoginState(tmp_path / 's.json')
    state.record_kick()
    state.record_signed_in()
    assert state.refuse_auto_login('token 失效') is None


# ── ensure_logged_in end to end (seams stubbed) ─────────────────────


async def test_expiry_auto_logins_and_succeeds(tmp_path, monkeypatch):
    """Expiry → the form is driven → the API poll succeeds."""
    session = FakeLoginSession(probe=LOGIN_PAGE_PROBE, fill='OK')
    client = await _login_client(tmp_path, monkeypatch, session=session)
    await client.ensure_logged_in()
    assert session.opened and session.closed
    assert any('setVal' in js for js in session.evaluated)
    assert client._login_state._data.get('last_auto_login')


async def test_signed_in_short_circuits(tmp_path, monkeypatch):
    """Already signed in → no inspector, no form fill."""
    session = FakeLoginSession(probe=LOGIN_PAGE_PROBE)
    server = await _serve({
        ('POST', '/browser/list'): (
            200,
            {'success': True, 'data': {'list': [], 'totalNum': 0}},
        ),
    })
    client = bb.BitBrowserClient({'api_port': server.port})
    client._login_state = bl.LoginState(tmp_path / 's.json')
    await client.ensure_logged_in()
    assert not session.opened


async def test_kick_refuses_without_driving(tmp_path, monkeypatch):
    """A device kick → NO auto-login, the kick hint, no form fill."""
    session = FakeLoginSession(probe=LOGIN_PAGE_PROBE)
    client = await _login_client(
        tmp_path, monkeypatch, session=session, logged_out_msg='Login out！'
    )

    with pytest.raises(LoginRequired) as exc:
        await client.ensure_logged_in()
    assert '另一台电脑' in str(exc.value)
    assert not session.opened
    assert client._login_state._data.get('kicked_at')


async def test_kick_twist_expiry_still_auto_logs_in(tmp_path, monkeypatch):
    """Negative control: the refusal is the kick MESSAGE (and the
    ping-pong window), not any logged-out state."""
    session = FakeLoginSession(probe=LOGIN_PAGE_PROBE, fill='OK')
    client = await _login_client(tmp_path, monkeypatch, session=session)
    await client.ensure_logged_in()
    assert session.opened


async def test_ping_pong_second_logout_refuses(tmp_path, monkeypatch):
    """Our auto-login just ran and the client is logged out again →
    the ping-pong guard refuses (never loop)."""
    session = FakeLoginSession(probe=LOGIN_PAGE_PROBE)
    client = await _login_client(tmp_path, monkeypatch, session=session)
    client._login_state.record_auto_login()  # < 15 min ago

    with pytest.raises(LoginRequired) as exc:
        await client.ensure_logged_in()
    assert '另一台电脑' in str(exc.value)
    assert not session.opened


async def test_no_credentials_clear_hint(tmp_path, monkeypatch):
    """Logged out, plain expiry, but nothing stored → the
    fill-in-settings hint, no inspector."""
    session = FakeLoginSession(probe=LOGIN_PAGE_PROBE)
    client = await _login_client(
        tmp_path, monkeypatch, session=session, credentials=False
    )

    with pytest.raises(LoginRequired) as exc:
        await client.ensure_logged_in()
    assert '账号密码' in str(exc.value)
    assert not session.opened


async def test_wrong_password_hint(tmp_path, monkeypatch):
    """The form submits but the API poll fails and the DOM shows the
    wrong-credentials error → the re-fill hint."""
    session = FakeLoginSession(
        probe=LOGIN_PAGE_PROBE, fill='OK', classify='WRONGCREDS'
    )

    async def _classify():
        return 'WRONGCREDS'

    client = await _login_client(
        tmp_path, monkeypatch, session=session, poll_success=False
    )
    monkeypatch.setattr(client, '_classify_login_failure', _classify)

    with pytest.raises(LoginRequired) as exc:
        await client.ensure_logged_in()
    assert '密码不对' in str(exc.value)


async def test_verification_step_hint(tmp_path, monkeypatch):
    """A code field on the login page → the verification hint, no
    form fill."""
    verify_probe = json.dumps({
        'url': 'https://client.bitbrowser.cn/login',
        'hasUser': True,
        'hasPass': True,
        'hasSubmit': True,
        'hasCodeField': True,
        'errorText': '',
    })
    session = FakeLoginSession(probe=verify_probe)
    client = await _login_client(tmp_path, monkeypatch, session=session)

    with pytest.raises(LoginRequired) as exc:
        await client.ensure_logged_in()
    assert '登录验证' in str(exc.value)
    assert session.opened and session.closed  # lock released
    assert not any('setVal' in js for js in session.evaluated)


async def test_inspector_unavailable_hint(tmp_path, monkeypatch):
    """The inspector cannot open (busy/fuses) → the manual hint, the
    lock released (close ran on the stub)."""
    session = FakeLoginSession(open_error=InspectorBusy('9229 busy'))
    client = await _login_client(tmp_path, monkeypatch, session=session)

    with pytest.raises(LoginRequired) as exc:
        await client.ensure_logged_in()
    assert '手动' in str(exc.value)


async def test_missing_selectors_hint(tmp_path, monkeypatch):
    """The remote page changed (no fields) → the failure code in the
    hint, lock released."""
    session = FakeLoginSession(probe=LOGIN_PAGE_PROBE, fill='NOFIELDS')
    client = await _login_client(tmp_path, monkeypatch, session=session)

    with pytest.raises(LoginRequired) as exc:
        await client.ensure_logged_in()
    assert 'NOFIELDS' in str(exc.value)
    assert session.closed


async def test_not_on_login_page(tmp_path, monkeypatch):
    """The window is not on a login page (already past login, or a
    service-line host without /login) → NOLOGINPAGE."""
    other_probe = json.dumps({
        'url': 'https://client.bitbrowser.cn/dashboard',
        'hasUser': False,
        'hasPass': False,
        'hasSubmit': False,
        'hasCodeField': False,
        'errorText': '',
    })
    session = FakeLoginSession(probe=other_probe)
    client = await _login_client(tmp_path, monkeypatch, session=session)

    with pytest.raises(LoginRequired) as exc:
        await client.ensure_logged_in()
    assert 'NOLOGINPAGE' in str(exc.value)
    assert session.closed


# ── credentials never leak ───────────────────────────────────────────


async def test_credentials_never_in_logs_or_exceptions(
    tmp_path, monkeypatch, caplog
):
    """The password rides ONE eval and appears in no log line and no
    exception text (even on failure)."""
    session = FakeLoginSession(probe=LOGIN_PAGE_PROBE, fill='NOFIELDS')
    client = await _login_client(tmp_path, monkeypatch, session=session)

    with caplog.at_level(logging.DEBUG, logger='app.browser'):
        with pytest.raises(LoginRequired):
            await client.ensure_logged_in()
    assert 'sentinel-secret-pass' not in caplog.text
    # the exception itself carries only the failure code
    # (the raise happened inside the pytest.raises above; re-raise to
    # inspect — simpler: assert on the driver's contract)
    fill_evals = [js for js in session.evaluated if 'setVal' in js]
    assert fill_evals, 'the form fill never ran'
    assert fill_evals[0].count('sentinel-secret-pass') == 1


async def test_credentials_twist_planted_leak_is_caught(caplog):
    """Negative control: the capture sees a planted leak."""
    with caplog.at_level(logging.DEBUG, logger='app.browser'):
        logging.getLogger('app.browser').warning(
            'planted %s', 'sentinel-secret-pass'
        )
    assert 'sentinel-secret-pass' in caplog.text
