"""The login runner drives a CLI's own login with no terminal.

Runs the real ``LoginSession`` against ``fake_workplace_cli.py``: the
link / code / QR reach the view, an approval moves to the next step,
a refusal / timeout / cancel ends it — and never leaves the CLI process
behind.
"""

import asyncio
import os
import sys

import psutil
import pytest

from app.cli_integrations import download, paths
from app.cli_integrations.base import AuthStatus, LoginStep
from app.cli_integrations.login import LoginManager
from tests.fixtures.fake_cli_integration import FakeIntegration

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(sys.platform == 'win32', reason='shebang script CLI'),
]


@pytest.fixture
def fake(tmp_path) -> FakeIntegration:
    integ = FakeIntegration('fakecli', tmp_path)
    home = tmp_path / 'home'
    paths.tool_dir('fakecli', home).mkdir(parents=True)
    paths.write_manifest('fakecli', {'version': '1.2.3'}, home)
    exe = paths.binary_path('fakecli', 'fakecli', home)
    exe.parent.mkdir(parents=True)
    exe.write_bytes(
        download.archive_member(
            integ.release.read_bytes(), lambda n: n.endswith('/fakecli')
        )
    )
    exe.chmod(0o755)
    integ.exe = exe
    return integ


async def _until(predicate, timeout: float = 10.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError('condition not reached')
        await asyncio.sleep(0.02)


async def _start(mgr, fake, tmp_path, status_reason='not_configured'):
    steps = fake.login_steps(AuthStatus(False, status_reason))
    return await mgr.start(
        fake,
        fake.exe,
        steps,
        tmp_path / 'session',
        {**os.environ, **fake.env(tmp_path)},
    )


async def test_two_step_login_shows_each_link_then_succeeds(fake, tmp_path):
    mgr = LoginManager()
    done = []
    session = await _start(mgr, fake, tmp_path)
    session._on_done = lambda s: _record(done, s)

    await _until(lambda: session.view.url is not None)
    v = session.view
    assert (v.state, v.step, v.steps) == ('waiting', 1, 2)
    assert v.url == 'https://fake.example/setup?app=APP1'
    assert v.hint == 'fake_app'

    fake.approve()
    await _until(lambda: session.view.step == 2 and session.view.user_code)
    assert session.view.url == 'https://fake.example/device'
    assert session.view.user_code == 'WXYZ-1234'
    assert session.view.hint == 'open_url'

    fake.approve()
    await session.task
    assert session.view.state == 'succeeded'
    assert session.view.url is None and session.view.hint is None
    assert (fake.state / 'token').read_text() == 'Alice'
    assert done == ['succeeded']


async def _record(done, session):
    done.append(session.view.state)


async def test_outcome_is_not_final_until_on_done_finished(fake, tmp_path):
    """``on_done`` enables the platform after the sign-in, which takes
    seconds. A page that read the row in that window saw "signed in,
    AI can't use it" and stopped polling, so the view says it is still
    finishing until ``on_done`` returned."""
    mgr = LoginManager()
    session = await mgr.start(
        fake,
        fake.exe,
        fake.login_steps(AuthStatus(False, 'not_logged_in')),
        tmp_path / 'session',
        {**os.environ, **fake.env(tmp_path)},
    )
    release = asyncio.Event()
    seen = []

    async def enable(s):
        seen.append((s.view.state, s.view.finishing))
        await release.wait()

    session._on_done = enable
    await _until(lambda: session.view.user_code)
    fake.approve()
    await _until(lambda: seen)
    assert seen == [('succeeded', True)]
    assert session.view.public()['finishing'] is True
    release.set()
    await session.task
    assert session.view.finishing is False


async def test_qr_login_publishes_the_image(fake, tmp_path):
    fake.qr = True
    mgr = LoginManager()
    session = await _start(mgr, fake, tmp_path, 'not_logged_in')
    await _until(lambda: session.view.qr)
    assert session.view.state == 'waiting'
    qr = session.qr_path()
    assert qr == tmp_path / 'session' / 'qr.png'
    # The QR signs the server's CLI in: the session dir is private.
    assert (tmp_path / 'session').stat().st_mode & 0o777 == 0o700
    assert qr.read_bytes().startswith(b'\x89PNG')
    fake.approve()
    await session.task
    assert session.view.state == 'succeeded'


async def test_refusal_fails_with_the_cli_output(fake, tmp_path):
    mgr = LoginManager()
    session = await _start(mgr, fake, tmp_path, 'not_logged_in')
    await _until(lambda: session.view.user_code)
    fake.deny()
    await session.task
    assert session.view.state == 'failed'
    assert 'denied' in session.view.message


async def test_cancel_kills_the_waiting_cli(fake, tmp_path):
    mgr = LoginManager()
    session = await _start(mgr, fake, tmp_path, 'not_logged_in')
    pid_file = fake.state / 'login-pid'
    await _until(pid_file.exists)
    pid = int(pid_file.read_text())
    assert psutil.pid_exists(pid)
    assert await mgr.cancel('fakecli') is True
    assert session.view.state == 'cancelled'
    await _until(lambda: not _alive(pid))


async def test_timeout_fails_and_kills(fake, tmp_path, monkeypatch):
    mgr = LoginManager()
    steps = [
        LoginStep(('auth', 'login'), lambda line, view: None, timeout_s=0.5)
    ]
    session = await mgr.start(
        fake,
        fake.exe,
        steps,
        tmp_path / 'session',
        {**os.environ, **fake.env(tmp_path)},
    )
    await session.task
    assert session.view.state == 'failed'
    assert 'timed out' in session.view.message
    assert session.view.error == 'timeout'  # the page's own sentence
    pid = int((fake.state / 'login-pid').read_text())
    await _until(lambda: not _alive(pid))


async def test_restart_replaces_the_previous_session(fake, tmp_path):
    mgr = LoginManager()
    first = await _start(mgr, fake, tmp_path, 'not_logged_in')
    await _until(lambda: first.view.user_code)
    second = await _start(mgr, fake, tmp_path, 'not_logged_in')
    assert first.view.state == 'cancelled'
    assert mgr.get('fakecli') is second
    await mgr.shutdown()
    assert second.view.state == 'cancelled'


def _alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def test_cancel_leaves_reaping_to_the_event_loop(fake, tmp_path, monkeypatch):
    """Under uvloop — what uvicorn runs — cancel returns promptly.

    uvloop reaps its children itself, in C, and only then resolves
    ``proc.wait()``. A Python-level ``waitpid`` on the CLI (psutil's
    ``wait_procs`` does one for a child) can steal that exit status;
    ``proc.wait()`` then never returns, so the DELETE that cancels the
    login hangs, the page keeps showing the QR, and the server cannot
    shut down. The kill path must never reap the CLI itself.
    """
    uvloop = pytest.importorskip('uvloop')  # POSIX only, like uvicorn's
    reaped: list[int] = []
    real_waitpid = os.waitpid

    def spy(pid, options):
        reaped.append(pid)
        return real_waitpid(pid, options)

    monkeypatch.setattr(os, 'waitpid', spy)

    async def scenario():
        mgr = LoginManager()
        session = await _start(mgr, fake, tmp_path, 'not_logged_in')
        pid_file = fake.state / 'login-pid'
        await _until(pid_file.exists)
        pid = int(pid_file.read_text())
        await asyncio.wait_for(mgr.cancel('fakecli'), 15)
        return session, pid

    session, pid = uvloop.run(scenario())
    assert session.view.state == 'cancelled'
    assert pid not in reaped, 'the kill path reaped the CLI itself'
    assert not _alive(pid)


async def test_signed_in_despite_a_non_zero_exit_is_a_success(fake, tmp_path):
    """The outcome is the CLI's sign-in state, not its exit code: lark-cli
    signs in and still exits 3 when the org admin withheld a scope — and
    first prints one JSON line far past asyncio's 64 KiB line default."""
    fake.login_exit = 3
    fake.long_line = True
    mgr = LoginManager()
    session = await mgr.start(
        fake,
        fake.exe,
        fake.login_steps(AuthStatus(False, 'not_logged_in')),
        tmp_path / 'session',
        {**os.environ, **fake.env(tmp_path)},
        verify=_signed_in(fake),
    )
    await _until(lambda: session.view.user_code)
    fake.approve()
    await session.task
    assert session.view.state == 'succeeded', session.view
    # Kept as a readable line, never the JSON dump.
    assert session.view.message == 'logged in'


async def test_exit_zero_but_not_signed_in_is_a_failure(fake, tmp_path):
    mgr = LoginManager()
    session = await mgr.start(
        fake,
        fake.exe,
        fake.login_steps(AuthStatus(False, 'not_logged_in')),
        tmp_path / 'session',
        {**os.environ, **fake.env(tmp_path)},
        verify=lambda: _false(),
    )
    await _until(lambda: session.view.user_code)
    fake.approve()
    await session.task
    assert session.view.state == 'failed'
    assert session.view.message == 'logged in'  # its last readable line


def _signed_in(fake):
    async def verify() -> bool:
        return (fake.state / 'token').exists()

    return verify


async def _false() -> bool:
    return False


# ── paste-back: the approval arrives as a localhost redirect ──────────


@pytest.fixture
def listening(fake) -> FakeIntegration:
    """``fake`` signing in the gws way: a link whose redirect is a
    local port the CLI listens on."""
    fake.listen = True
    return fake


async def _waiting_for_redirect(mgr, fake, tmp_path):
    session = await _start(mgr, fake, tmp_path, 'not_logged_in')
    await _until(lambda: session.view.callback is not None)
    return session


async def test_the_view_offers_paste_back_with_the_local_address(
    listening, tmp_path
):
    mgr = LoginManager()
    session = await _waiting_for_redirect(mgr, listening, tmp_path)
    v = session.view
    assert v.state == 'waiting' and v.paste_back is True
    assert v.url.startswith('https://accounts.fake.example/o/auth?')
    assert v.callback.startswith('http://localhost:')
    await mgr.shutdown()


async def test_a_pasted_address_reaches_the_cli_and_signs_in(
    listening, tmp_path
):
    """The browser elsewhere ended on ``http://localhost:<port>/?code=…``
    (its own machine: "cannot be reached"). Pasted, it reaches the CLI
    exactly as the redirect would have — and the sign-in finishes."""
    mgr = LoginManager()
    session = await _waiting_for_redirect(mgr, listening, tmp_path)
    address = f'{session.view.callback}/?state=s1&code=4/abc&scope=a%20b'

    await session.paste_back(address)
    await session.task

    assert session.view.state == 'succeeded'
    assert (listening.state / 'callback').read_text() == (
        '/?state=s1&code=4/abc&scope=a%20b'
    )
    assert session.view.paste_back is False
    assert session.view.callback is None


async def test_a_refusal_pasted_back_ends_the_sign_in_as_failed(
    listening, tmp_path
):
    mgr = LoginManager()
    session = await _waiting_for_redirect(mgr, listening, tmp_path)
    await session.paste_back(f'{session.view.callback}/?error=access_denied')
    await session.task
    assert session.view.state == 'failed'
    assert session.view.message == 'OAuth flow failed: access_denied'


@pytest.mark.parametrize(
    'address',
    [
        'https://localhost:{port}/?code=x',  # not what the CLI serves
        'http://evil.example:{port}/?code=x',  # not this computer
        'http://localhost:{other}/?code=x',  # another local port
        'http://localhost:{port}/?state=only',  # no approval in it
        'not an address',
    ],
)
async def test_only_this_sign_ins_own_redirect_is_relayed(
    listening, tmp_path, address
):
    """Nothing but the redirect the CLI waits for is ever sent: another
    host or port, or an address without the approval, is refused and
    the sign-in keeps waiting."""
    mgr = LoginManager()
    session = await _waiting_for_redirect(mgr, listening, tmp_path)
    port = int(session.view.callback.rsplit(':', 1)[1])
    with pytest.raises(ValueError):
        await session.paste_back(address.format(port=port, other=port + 1))
    assert session.active and session.view.state == 'waiting'
    assert not (listening.state / 'callback').exists()
    await mgr.shutdown()


async def test_paste_back_is_refused_by_a_step_without_it(fake, tmp_path):
    mgr = LoginManager()
    session = await _start(mgr, fake, tmp_path, 'not_logged_in')
    await _until(lambda: session.view.user_code)
    with pytest.raises(ValueError):
        await session.paste_back('http://localhost:1/?code=x')
    await mgr.shutdown()
