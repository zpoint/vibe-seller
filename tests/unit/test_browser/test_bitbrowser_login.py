"""BitBrowser auto-login tests (option B) — fake inspector + fake DOM.

Every behaviour pairs with a negative control. The credentials ride
only the one eval (a sentinel pins it — including through an
exceptionDetails echo of the fill script); the policy (kick /
ping-pong) is tested against a real temp login-state.json, across
client restarts (a fresh client reading the same persisted state).
"""

import json
import logging
import time

import pytest

import app.browser.adapters.bitbrowser as bb
import app.browser.adapters.bitbrowser_login as bl
from app.browser.inject.errors import LoginRequired
from app.browser.win_inspector import InspectorBusy, InspectorError
from tests.unit.test_browser.bitbrowser_test_support import (
    _serve,
    cleanup,
)

pytestmark = pytest.mark.unit


class FakeLoginSession:
    """A fake InspectorSession with the REAL session's contract: an
    eval on an unopened session raises (so a code path that skipped
    the open cannot silently read a scripted answer), close() always
    runs, and a scripted eval can raise an inspector error — 'echo'
    simulating the CDP exceptionDetails echo that embeds the script's
    own source line (the leak channel the sentinel tests pin)."""

    def __init__(
        self,
        probe=None,
        fill='OK',
        classify='UNKNOWN',
        open_error=None,
        eval_error=None,
    ):
        self.probe_result = probe
        self.fill_result = fill
        self.classify_result = classify
        self.open_error = open_error
        # ('probe'|'fill'|'classify', an Exception instance or 'echo')
        self.eval_error = eval_error
        self._is_open = False
        self.open_count = 0
        self.closed = False
        self.evaluated: list[str] = []

    async def open(self, main_pid: int) -> None:
        if self.open_error:
            raise self.open_error
        self._is_open = True
        self.open_count += 1

    async def close(self) -> None:
        self._is_open = False
        self.closed = True

    async def eval_in_renderer(self, js: str, *, timeout: float = 25.0):
        if not self._is_open:
            raise InspectorError('inspector not open')
        self.evaluated.append(js)
        kind = self._kind(js)
        error = self.eval_error
        if error is not None and error[0] == kind:
            if error[1] == 'echo':
                # What the real session does on a JS exception: the
                # error text carries exceptionDetails, whose echo can
                # embed the script's source line.
                raise InspectorError(
                    json.dumps({'exceptionDetails': {'sourceLine': js}})[:400]
                )
            raise error[1]
        if kind == 'probe':
            return self.probe_result or '{}'
        if kind == 'fill':
            return self.fill_result
        return self.classify_result

    @staticmethod
    def _kind(js: str) -> str:
        if 'setVal' in js:
            return 'fill'
        if 'hasCodeField' in js:
            return 'probe'
        return 'classify'


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


async def _login_client(
    tmp_path,
    monkeypatch,
    *,
    credentials=True,
    password='sentinel-secret-pass',
    session=None,
    poll=None,
    logged_out_msg='token 失效，请检查登录状态',
    poll_success=True,
):
    """Build a BitBrowserClient against a logged-out fake API with the
    login seams stubbed (inspector session, main pid, poll). Clients
    built with the same ``tmp_path`` share the persisted login-state
    file — the client-restart seam."""
    server = await _serve({
        ('POST', '/browser/list'): (
            200,
            {'success': False, 'msg': logged_out_msg},
        ),
    })
    cfg = {'api_port': server.port}
    if credentials:
        cfg['bitbrowser_username'] = 'demo-user'
        cfg['bitbrowser_password'] = password
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


async def _signed_in_client(tmp_path):
    """A client whose API answers signed-in (the user signed in by
    hand in the client)."""
    server = await _serve({
        ('POST', '/browser/list'): (
            200,
            {'success': True, 'data': {'list': [], 'totalNum': 0}},
        ),
    })
    client = bb.BitBrowserClient({'api_port': server.port})
    client._login_state = bl.LoginState(tmp_path / 'login-state.json')
    return client


# ── the policy (LoginState) ─────────────────────────────────────────


def test_kick_message_refuses(tmp_path):
    state = bl.LoginState(tmp_path / 's.json')
    assert state.refuse_auto_login('Login out！') == 'kick'


def test_recorded_kick_refuses_whatever_the_message(tmp_path):
    """A RECORDED kick refuses even when the current message is the
    plain-expiry shape (what a client restart shows) — only an
    observed sign-in lifts it (the persisted refusal, S2)."""
    state = bl.LoginState(tmp_path / 's.json')
    state.record_kick()
    assert state.refuse_auto_login('token 失效') == 'kick'


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


# ── the policy file (S4) ────────────────────────────────────────────


def test_corrupt_state_file_tolerated_with_warning(tmp_path, caplog):
    """A truncated / partial file reads as empty state — never a
    crash, and never a silent pass either: it warns."""
    p = tmp_path / 's.json'
    p.write_text('{"kicked_at": 12', encoding='utf-8')  # torn write
    with caplog.at_level(logging.WARNING, logger=bl.__name__):
        state = bl.LoginState(p)
    assert state.refuse_auto_login('token 失效') is None
    assert 'unreadable' in caplog.text
    # A non-object file is the same: empty state + a warning.
    p.write_text('[1, 2]', encoding='utf-8')
    with caplog.at_level(logging.WARNING, logger=bl.__name__):
        state = bl.LoginState(p)
    assert state.refuse_auto_login('token 失效') is None
    assert 'not an object' in caplog.text


def test_write_is_atomic_and_roundtrips(tmp_path):
    """The write lands whole (temp file + replace — no .tmp residue)
    and a fresh reader enforces what it recorded."""
    p = tmp_path / 's.json'
    state = bl.LoginState(p)
    state.record_kick()
    assert json.loads(p.read_text(encoding='utf-8'))['kicked_at']
    assert not (tmp_path / 's.json.tmp').exists()
    assert bl.LoginState(p).refuse_auto_login('token 失效') == 'kick'


# ── ensure_logged_in end to end (seams stubbed) ─────────────────────


async def test_expiry_auto_logins_and_succeeds(tmp_path, monkeypatch):
    """Expiry → the form is driven → the API poll succeeds."""
    session = FakeLoginSession(probe=LOGIN_PAGE_PROBE, fill='OK')
    client = await _login_client(tmp_path, monkeypatch, session=session)
    await client.ensure_logged_in()
    assert session.open_count == 1 and session.closed
    assert any('setVal' in js for js in session.evaluated)
    assert client._login_state._data.get('last_auto_login')


async def test_signed_in_short_circuits(tmp_path, monkeypatch):
    """Already signed in → no inspector, no form fill."""
    session = FakeLoginSession(probe=LOGIN_PAGE_PROBE)
    client = await _signed_in_client(tmp_path)
    await client.ensure_logged_in()
    assert session.open_count == 0


async def test_kick_refuses_without_driving(tmp_path, monkeypatch):
    """A device kick → NO auto-login, the kick hint, no form fill."""
    session = FakeLoginSession(probe=LOGIN_PAGE_PROBE)
    client = await _login_client(
        tmp_path, monkeypatch, session=session, logged_out_msg='Login out！'
    )

    with pytest.raises(LoginRequired) as exc:
        await client.ensure_logged_in()
    assert '另一台电脑' in str(exc.value)
    assert session.open_count == 0
    assert client._login_state._data.get('kicked_at')


async def test_kick_twist_expiry_after_kick_still_refuses(
    tmp_path, monkeypatch
):
    """After a recorded kick, the plain-expiry shape still does NOT
    auto-login — a client restart brings the expiry message back
    while the kick stands; auto-login would ping-pong the two
    machines. (Inverted from the old twist that pinned the
    message-only refusal.)"""
    session = FakeLoginSession(probe=LOGIN_PAGE_PROBE)
    client = await _login_client(
        tmp_path, monkeypatch, session=session, logged_out_msg='Login out！'
    )
    with pytest.raises(LoginRequired):
        await client.ensure_logged_in()

    # A client restart: a FRESH client reading the same persisted
    # state, seeing the expiry shape.
    session2 = FakeLoginSession(probe=LOGIN_PAGE_PROBE, fill='OK')
    client2 = await _login_client(tmp_path, monkeypatch, session=session2)
    with pytest.raises(LoginRequired) as exc:
        await client2.ensure_logged_in()
    assert '另一台电脑' in str(exc.value)
    assert session2.open_count == 0


async def test_kick_restart_expiry_manual_signin_then_expiry(
    tmp_path, monkeypatch
):
    """The addendum §3 sequence, end to end across client restarts:
    kick → restart shows expiry → STILL no auto-login → the user
    signs in by hand → the NEXT expiry auto-logs-in again."""
    # 1. The kick (the other machine signed in).
    session = FakeLoginSession(probe=LOGIN_PAGE_PROBE)
    client = await _login_client(
        tmp_path, monkeypatch, session=session, logged_out_msg='Login out！'
    )
    with pytest.raises(LoginRequired):
        await client.ensure_logged_in()

    # 2. Restart: fresh client, same persisted state, expiry shape.
    session2 = FakeLoginSession(probe=LOGIN_PAGE_PROBE, fill='OK')
    client2 = await _login_client(tmp_path, monkeypatch, session=session2)
    with pytest.raises(LoginRequired) as exc:
        await client2.ensure_logged_in()
    assert '另一台电脑' in str(exc.value)
    assert session2.open_count == 0

    # 3. The user signs in by hand in the client (the API answers
    # signed-in) — the observed sign-in lifts the refusal.
    client3 = await _signed_in_client(tmp_path)
    await client3.ensure_logged_in()

    # 4. The next expiry auto-logs-in again.
    session4 = FakeLoginSession(probe=LOGIN_PAGE_PROBE, fill='OK')
    client4 = await _login_client(tmp_path, monkeypatch, session=session4)
    await client4.ensure_logged_in()
    assert session4.open_count == 1


async def test_ping_pong_second_logout_refuses(tmp_path, monkeypatch):
    """Our auto-login just ran and the client is logged out again →
    the ping-pong guard refuses (never loop)."""
    session = FakeLoginSession(probe=LOGIN_PAGE_PROBE)
    client = await _login_client(tmp_path, monkeypatch, session=session)
    client._login_state.record_auto_login()  # < 15 min ago

    with pytest.raises(LoginRequired) as exc:
        await client.ensure_logged_in()
    assert '另一台电脑' in str(exc.value)
    assert session.open_count == 0


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
    assert session.open_count == 0


async def test_wrong_password_hint(tmp_path, monkeypatch):
    """The form submits but the API poll fails and the DOM shows the
    wrong-credentials error → the re-fill hint. The classification
    runs through the REAL path: a session that only answers when
    OPENED (the fill run opened once; the classify re-opened — the
    pre-fix code never opened for classify and always read UNKNOWN).
    A failed login also never arms the ping-pong guard (N5)."""
    session = FakeLoginSession(
        probe=LOGIN_PAGE_PROBE, fill='OK', classify='WRONGCREDS'
    )
    client = await _login_client(
        tmp_path, monkeypatch, session=session, poll_success=False
    )

    with pytest.raises(LoginRequired) as exc:
        await client.ensure_logged_in()
    assert '密码不对' in str(exc.value)
    assert session.open_count == 2  # fill run + classify re-open
    assert session.closed
    # N5: a wrong password must not start the 15-min ping-pong window
    # (the next task would read the kick hint for a bad password).
    assert not client._login_state._data.get('last_auto_login')


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
    assert session.open_count == 1 and session.closed  # lock released
    assert not any('setVal' in js for js in session.evaluated)


async def test_inspector_unavailable_hint(tmp_path, monkeypatch):
    """The inspector cannot open (busy/fuses) → the manual hint, the
    lock released (close ran on the stub)."""
    session = FakeLoginSession(open_error=InspectorBusy('9229 busy'))
    client = await _login_client(tmp_path, monkeypatch, session=session)

    with pytest.raises(LoginRequired) as exc:
        await client.ensure_logged_in()
    assert '手动' in str(exc.value)


async def test_probe_eval_error_maps_to_byhand_hint(tmp_path, monkeypatch):
    """A raw inspector error from the PROBE eval (stale target,
    timeout) maps to the sign-in-by-hand hint — it must not escape
    ensure_logged_in untranslated (S3). The lock is released."""
    session = FakeLoginSession(
        probe=LOGIN_PAGE_PROBE,
        eval_error=('probe', InspectorError('target went away')),
    )
    client = await _login_client(tmp_path, monkeypatch, session=session)

    with pytest.raises(LoginRequired) as exc:
        await client.ensure_logged_in()
    assert '手动' in str(exc.value)
    assert 'InspectorError' not in str(exc.value)
    assert session.closed


async def test_fill_eval_error_maps_to_byhand_hint(
    tmp_path, monkeypatch, caplog
):
    """A raw inspector error from the FILL eval maps to the
    sign-in-by-hand hint too, with the lock released — and the error
    text (which echoes the fill script) reaches NO log line (S3+N2)."""
    session = FakeLoginSession(
        probe=LOGIN_PAGE_PROBE, eval_error=('fill', 'echo')
    )
    client = await _login_client(tmp_path, monkeypatch, session=session)

    with caplog.at_level(logging.DEBUG, logger='app.browser'):
        with pytest.raises(LoginRequired) as exc:
            await client.ensure_logged_in()
    assert '手动' in str(exc.value)
    assert session.closed
    assert 'sentinel-secret-pass' not in caplog.text
    assert json.dumps('sentinel-secret-pass') not in caplog.text


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


# ── the classify regex (N4) ─────────────────────────────────────────


def test_classify_regex_has_no_bare_not_found():
    """A bare English 'not found' anywhere in the page text must not
    classify as wrong credentials (page copy, a 404 banner) — only
    the vendor's own wording matches."""
    assert 'not found' not in bl._CLASSIFY_JS
    for mark in ('密码错误', '账号或密码', 'incorrect'):
        assert mark in bl._CLASSIFY_JS


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
    fill_evals = [js for js in session.evaluated if 'setVal' in js]
    assert fill_evals, 'the form fill never ran'
    assert fill_evals[0].count('sentinel-secret-pass') == 1


async def test_hostile_password_encoded_and_never_leaks(
    tmp_path, monkeypatch, caplog
):
    """A hostile password (quotes, a backslash, </script>, a non-BMP
    emoji) is embedded with json.dumps (repr would mangle the emoji
    into literal U… text — a silently wrong password), and when the
    fill eval fails with an exceptionDetails ECHO of the script, the
    password appears in no raised message and no log line (N2)."""
    hostile = 'p\'a"s\\s</script>\U0001f631'
    session = FakeLoginSession(
        probe=LOGIN_PAGE_PROBE, eval_error=('fill', 'echo')
    )
    client = await _login_client(
        tmp_path, monkeypatch, session=session, password=hostile
    )

    with caplog.at_level(logging.DEBUG, logger='app.browser'):
        with pytest.raises(LoginRequired) as exc:
            await client.ensure_logged_in()

    # The fill JS carries the password exactly once, JSON-encoded.
    fill_evals = [js for js in session.evaluated if 'setVal' in js]
    assert fill_evals, 'the form fill never ran'
    assert json.dumps(hostile) in fill_evals[0]
    assert fill_evals[0].count(json.dumps(hostile)) == 1
    # Neither the raw nor the JSON-encoded form reaches the raised
    # message or any log line — even though the eval error's text
    # embedded the whole fill script.
    assert hostile not in str(exc.value)
    assert json.dumps(hostile) not in str(exc.value)
    assert hostile not in caplog.text
    assert json.dumps(hostile) not in caplog.text


async def test_credentials_twist_planted_leak_is_caught(caplog):
    """Negative control: the capture sees a planted leak."""
    with caplog.at_level(logging.DEBUG, logger='app.browser'):
        logging.getLogger('app.browser').warning(
            'planted %s', 'sentinel-secret-pass'
        )
    assert 'sentinel-secret-pass' in caplog.text
