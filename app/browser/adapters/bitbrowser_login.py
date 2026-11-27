"""BitBrowser auto-login: stored account + password, driven through
the client's own login page over the shared inspector.

The design (addendum 23, option B — the user's binding decision):

- The account + password are stored through the GENERIC credentials
  route (Fernet-encrypted, masked) and injected into the browser
  config as ``bitbrowser_username`` / ``bitbrowser_password`` — the
  same seam every ``single``-cardinality vendor uses.
- ``ensure_logged_in`` (the ONLY place auto-login happens) first
  classifies WHY the client is logged out, because the two shapes
  mean different things:
  - 「token 失效…」 (plain expiry) → auto-login is safe;
  - ``Login out！`` (a DEVICE KICK — one BitBrowser account is
    signed in on one computer at a time) → auto-login would either
    fail or ping-pong two machines; REFUSE with the kick hint until
    a sign-in we did not do is observed.
- The refusal state is persisted per machine in a small vendor-owned
  JSON file under ``VIBE_HOME/vendors/bitbrowser/login-state.json``
  so a kick survives server restarts; a successful list after a kick
  clears it (the user signed in by hand).
- The ping-pong guard: if OUR last auto-login was < 15 min ago and
  the client is logged out again, treat it as a kick (another
  install or device is using the account) — same refusal, never a
  loop.
- The login itself: take the inspector (the SHARED
  ``win_inspector``/``mac_inspector`` machinery with its machine-wide
  lock and pid verification), find the window on a ``/login`` page
  (any service-line host), fill the username + password inputs with
  GENERIC selectors (a text input + ``input[type=password]``) using
  native value setters, click the 登录/Login button, release the
  inspector, then decide SUCCESS by the LOCAL API (poll
  ``/browser/list`` until ``success:true``), never by the DOM.
- Failures are classified from the DOM AFTER the API poll fails
  (wrong-credentials error text, a verification step — SMS / email
  code / 2FA — or selectors missing entirely) and surface as
  LOGIN_REQUIRED with a plain-language hint; the inspector lock is
  always released.

Credentials never appear in logs or exceptions: the password rides
one ``eval_in_renderer`` call and nowhere else (the sentinel test
pins this).
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import subprocess
import time

from app.browser.adapters import bitbrowser_launch as launch
from app.browser.mac_inspector import MacInspectorSession
from app.browser.win_app_locator import (
    run_powershell as _run_ps,
)
from app.browser.win_inspector import (
    InspectorBusy,
    InspectorError,
    InspectorSession,
)
from app.browser.ziniao_utils import get_platform

logger = logging.getLogger(__name__)

# The policy file lives under VIBE_HOME, vendor-owned.
_POLICY_DIR = Path('vendors') / 'bitbrowser'
_POLICY_FILE = _POLICY_DIR / 'login-state.json'

# A kick refusal stands until a sign-in we did not do; our own
# auto-login must not retry sooner than this after a logout (the
# ping-pong guard).
_PING_PONG_S = 15 * 60

# How long to poll the local API after submitting the form.
_LOGIN_POLL_ATTEMPTS = 30
_LOGIN_POLL_DELAY_S = 1.0


# ── the persisted policy ─────────────────────────────────────────────


class LoginState:
    """The vendor-owned, per-machine login policy state.

    ``kicked_at``: a device kick was seen — no auto-login until a
    sign-in we did not do is observed. ``last_auto_login``: when OUR
    last auto-login ran (the ping-pong guard reads it). Both are
    plain unix timestamps; the file is small, JSON, and rewritten
    atomically-enough for a single-machine policy (a torn write reads
    as "no state", which only ever allows an auto-login attempt).
    """

    def __init__(self, path: Path):
        self._path = path
        self._data = self._read()

    def _read(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data), encoding='utf-8')

    # ── the policy questions ────────────────────────────────────────

    def refuse_auto_login(self, logged_out_msg: str) -> str | None:
        """Why auto-login must NOT run now (a hint), or None if it may.

        Two refusals, both returning the kick hint:
        - the logged-out message IS the kick shape;
        - our last auto-login was < 15 min ago (a re-logout that soon
          means another device is using the account — never loop).
        """
        if _is_kick_message(logged_out_msg):
            return 'kick'
        last = self._data.get('last_auto_login')
        if last and time.time() - float(last) < _PING_PONG_S:
            return 'ping-pong'
        return None

    # ── the state transitions ───────────────────────────────────────

    def record_kick(self) -> None:
        self._data['kicked_at'] = time.time()
        self._write()

    def record_auto_login(self) -> None:
        self._data['last_auto_login'] = time.time()
        self._write()

    def record_signed_in(self) -> None:
        """A successful login we may or may not have driven: the kick
        refusal only ever lifts on a sign-in, and ours counts too
        (the user may have fixed the other device in between)."""
        if self._data.get('kicked_at') is not None:
            self._data['kicked_at'] = None
            self._write()


def _is_kick_message(msg: str) -> bool:
    """The device-kick logged-out shape (vs plain expiry)."""
    return 'Login out' in msg


def load_login_state(vibe_home: Path) -> LoginState:
    """The policy state under ``vibe_home`` (VIBE_SELLER_DIR)."""
    return LoginState(vibe_home / _POLICY_FILE)


# ── the login-page DOM driving (generic selectors) ───────────────────

# Read the login page's shape: are the fields there, is a verification
# step showing, does the form carry an error message?
_PROBE_JS = """(() => {
  const inputs = Array.from(document.querySelectorAll('input'));
  const user = inputs.find(i => i.type === 'text' ||
    i.type === 'email' || i.type === 'tel');
  const pass = inputs.find(i => i.type === 'password');
  const code = inputs.find(i => i.type === 'text' &&
    (i.placeholder || '').match(/验证码|代码|code/i));
  const buttons = Array.from(document.querySelectorAll('button'));
  const submit = buttons.find(b => {
    const t = (b.innerText || '').trim();
    return t === '登录' || t === 'Login' || t === '登录/Login';
  });
  const err = document.querySelector(
    '.q-field__messages, .error, [class*=error]');
  return JSON.stringify({
    url: location.href,
    hasUser: !!user, hasPass: !!pass, hasSubmit: !!submit,
    hasCodeField: !!code,
    errorText: err ? (err.innerText || '').trim().slice(0, 80) : '',
  });
})()"""

# Fill + submit. Generic selectors; native value setters so the
# framework's bindings see the change. The credentials ride THIS ONE
# eval (as literals inside the JS) and nowhere else.
_FILL_JS_TEMPLATE = """(() => {{
  const setVal = (el, v) => {{
    const proto = el instanceof HTMLTextAreaElement ?
      HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
    el.dispatchEvent(new Event('input', {{bubbles: true}}));
    el.dispatchEvent(new Event('change', {{bubbles: true}}));
  }};
  const inputs = Array.from(document.querySelectorAll('input'));
  const user = inputs.find(i => i.type === 'text' ||
    i.type === 'email' || i.type === 'tel');
  const pass = inputs.find(i => i.type === 'password');
  if (!user || !pass) return 'NOFIELDS';
  setVal(user, {username!r});
  setVal(pass, {password!r});
  const buttons = Array.from(document.querySelectorAll('button'));
  const submit = buttons.find(b => {{
    const t = (b.innerText || '').trim();
    return t === '登录' || t === 'Login' || t === '登录/Login';
  }});
  if (!submit) return 'NOBUTTON';
  submit.click();
  return 'OK';
}})()"""


def _fill_js(username: str, password: str) -> str:
    return _FILL_JS_TEMPLATE.format(username=username, password=password)


# ── the failure classification (DOM AFTER the API poll failed) ──────

_CLASSIFY_JS = """(() => {
  const t = document.body.innerText;
  const codeInput = Array.from(document.querySelectorAll('input'))
    .find(i => (i.placeholder || '').match(/验证码|代码|code/i));
  if (codeInput) return 'VERIFY';
  if (/密码错误|账号或密码|incorrect|invalid.*(password|account)|not found/i
      .test(t)) return 'WRONGCREDS';
  return 'UNKNOWN';
})()"""


# ── the login driver ─────────────────────────────────────────────────


class LoginDriver:
    """Drives one auto-login attempt through the shared inspector.

    Platform wiring only (the DOM logic lives in the JS above, the
    policy in :class:`LoginState`): on Windows the session needs the
    PowerShell preamble that resolves the installed exe; on macOS the
    Mac session triggers with SIGUSR1. The main pid is supplied by
    the caller (the API port owner on Windows; the bundle's main
    process on macOS) — never guessed here.
    """

    def __init__(
        self,
        username: str,
        password: str,
        main_pid: int,
        session: InspectorSession,
    ):
        self._username = username
        self._password = password
        self._pid = main_pid
        self._session = session

    async def run(self) -> str:
        """One attempt → 'OK' | 'WRONGCREDS' | 'VERIFY' | a failure
        code naming the step (never a credential)."""
        try:
            await self._session.open(self._pid)
        except (InspectorBusy, InspectorError) as e:
            logger.warning(
                'bitbrowser auto-login: inspector unavailable: %s', e
            )
            return 'INSPECTOR'
        try:
            probe = await self._probe()
            if probe is None:
                return 'NOLOGINPAGE'
            if probe.get('hasCodeField'):
                return 'VERIFY'
            result = str(
                await self._session.eval_in_renderer(
                    _fill_js(self._username, self._password)
                )
            )
            if result != 'OK':
                logger.warning(
                    'bitbrowser auto-login: form step failed: %s', result
                )
                return result
            return 'SUBMITTED'
        finally:
            await self._session.close()

    async def _probe(self) -> dict | None:
        """The login page's shape, or None when not on a login page."""
        raw = await self._session.eval_in_renderer(_PROBE_JS)
        try:
            probe = json.loads(str(raw))
        except ValueError:
            return None
        if '/login' not in str(probe.get('url', '')):
            return None
        return probe

    async def classify_failure(self) -> str:
        """Read the DOM for WHY a submitted login did not take."""
        try:
            return str(await self._session.eval_in_renderer(_CLASSIFY_JS))
        except Exception as e:  # the renderer may have navigated away
            logger.warning('bitbrowser auto-login classify: %s', e)
            return 'UNKNOWN'


def windows_session() -> InspectorSession:
    """A Windows inspector session for the BitBrowser client.

    The preamble resolves the installed exe the way the launcher does
    (the locator's dir + the largest-exe rule), ASCII-only so it
    survives any console codepage.
    """

    def exe_script() -> str:
        return launch.locate_exe_script()

    return InspectorSession(exe_script)


def mac_session() -> MacInspectorSession:
    """A macOS inspector session (SIGUSR1 trigger)."""
    return MacInspectorSession()


async def main_pid_for_platform(api_port: int) -> int | None:
    """The Electron main pid that owns the local API (per platform).

    Windows/WSL: the TCP port's owning process, resolved with one
    PowerShell call (ASCII-only). macOS: the bundle's main process
    (no ``--type=`` arg), via the shared Mac locator's process list.
    """
    if get_platform() == 'mac':
        return await asyncio.to_thread(_mac_main_pid)
    return await asyncio.to_thread(_windows_port_owner, api_port)


def _windows_port_owner(api_port: int) -> int | None:
    """The pid listening on ``api_port`` (None when nothing does)."""
    out = _run_ps(
        f'$c = Get-NetTCPConnection -LocalPort {api_port} -State Listen '
        '-ErrorAction SilentlyContinue | '
        'Select-Object -First 1; '
        'if ($c) { "$($c.OwningProcess)" }'
    )
    try:
        return int(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def _mac_main_pid() -> int | None:
    """The BitBrowser bundle's main process pid (macOS)."""
    out = subprocess.run(
        ['ps', '-Ao', 'pid,command'],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    ).stdout
    for line in out.splitlines():
        if 'BitBrowser.app/Contents/MacOS' in line and '--type=' not in line:
            try:
                return int(line.strip().split()[0])
            except (ValueError, IndexError):
                continue
    return None


def make_session(platform: str | None = None) -> InspectorSession:
    """The platform's inspector session (seam for tests)."""
    if (platform or get_platform()) == 'mac':
        return mac_session()
    return windows_session()


# ── the API-poll success check ───────────────────────────────────────


async def poll_until_signed_in(
    api_call, attempts: int = _LOGIN_POLL_ATTEMPTS
) -> bool:
    """True once ``/browser/list`` answers ``success:true``.

    ``api_call`` is the client's own (rate-limited) call helper, so
    the poll respects the vendor's rate limit.
    """
    for _ in range(attempts):
        await asyncio.sleep(_LOGIN_POLL_DELAY_S)
        try:
            status, payload = await api_call(
                'POST',
                '/browser/list',
                json_body={'page': 0, 'pageSize': 1},
            )
        except Exception:
            continue
        if isinstance(payload, dict) and payload.get('success'):
            return True
    return False
