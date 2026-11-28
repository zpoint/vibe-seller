"""The BitBrowser vendor in the LocalApiClient contract suite's shape.

The shared suite (``local_api_contract.py``) parametrizes its fixture
vendors; a REAL vendor proves the same contract from its own module —
this one — so the shared file stays vendor-free (review round 1). The
fake API and recorded shapes come from ``test_bitbrowser``; the checks
mirror the shared suite's: list parses, open returns the CDP endpoint,
close never raises, not-running is CLIENT_NOT_RUNNING (without any
real launch — the seam is patched), calls are rate-limit spaced, and
the adapters-discovery predicate matches the real class.

Also pins the Windows app locator's exe pick for this vendor's install
dir shape (the client exe is Chinese-named beside an uninstaller and
an elevate helper; the largest-exe rule must choose the client).
"""

import importlib
from pathlib import Path
import pkgutil
import time

import pytest

import app.browser.adapters as adapters_pkg
from app.browser.adapters.bitbrowser import (
    BITBROWSER_WIN_APP,
    BitBrowserClient,
)
from app.browser.inject.errors import ClientNotRunning
from app.browser.local_api_client import LocalApiClient
from app.browser.win_app_locator import main_exe_in_dir
from tests.unit.test_browser.bitbrowser_test_support import (
    _client,
    _default_routes,
    _serve,
    cleanup,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
async def _teardown():
    yield
    await cleanup()


# The real vendor's contract, against its recorded shapes.


async def test_contract_list_stores():
    server = await _serve(_default_routes())
    client = _client(server.port)
    refs = await client.list_stores()
    assert [r.ref for r in refs] == ['100000000001', '100000000002']
    assert [r.name for r in refs] == [
        '#1 demo-store-one',
        '#2 demo-store-two',
    ]


async def test_contract_open_store():
    server = await _serve(_default_routes())
    client = _client(server.port)
    refs = await client.list_stores()
    opened = await client.open_store(refs[0].open_ref)
    assert (opened.cdp_host, opened.cdp_port) == ('127.0.0.1', 54321)


async def test_contract_ensure_running_ok():
    server = await _serve(_default_routes())
    client = _client(server.port)
    await client.ensure_running()  # the API answers


async def test_contract_close_never_raises():
    server = await _serve({})  # every route 404s
    client = _client(server.port)
    await client.close_store('whatever')


async def test_contract_not_running_without_a_real_launch():
    """Nothing listens on port 1 (an unbound loopback port on WSL
    HANGS rather than refusing) → CLIENT_NOT_RUNNING, bounded by the
    connect timeout — and the LAUNCH SEAM is patched so the check can
    never start the real client from a unit test."""

    async def _no_launch():
        raise ClientNotRunning('not launched in tests')

    client = _client(1)
    client.connect_timeout_s = 0.3
    client.launch_client = _no_launch
    with pytest.raises(ClientNotRunning):
        await client.ensure_running()


async def test_contract_rate_limit_spaces_calls():
    server = await _serve(_default_routes())
    client = _client(server.port)
    client.min_interval_s = 0.12
    start = time.monotonic()
    await client.list_stores()
    await client.list_stores()
    await client.list_stores()
    assert time.monotonic() - start >= 2 * 0.12


def test_contract_discovery_finds_the_real_vendor():
    """The shared suite's discovery predicate matches the real
    BitBrowserClient under app/browser/adapters (and not the base or
    the test fixtures) — the vendor is a contract vendor."""

    def is_contract_vendor(cls) -> bool:
        return (
            isinstance(cls, type)
            and issubclass(cls, LocalApiClient)
            and cls is not LocalApiClient
            and getattr(cls, '__module__', '').startswith(
                'app.browser.adapters'
            )
        )

    found = []
    for mod_info in pkgutil.iter_modules(adapters_pkg.__path__):
        mod = importlib.import_module(f'app.browser.adapters.{mod_info.name}')
        found.extend(
            attr for attr in vars(mod).values() if is_contract_vendor(attr)
        )
    # Other real vendors may share the adapter package (AdsPower is on
    # main too). This contract requires BitBrowser to be discovered; it
    # must not turn their presence into a failure.
    assert BitBrowserClient in found


# The Windows app locator's exe pick for this vendor's install dir.


def _write(path: Path, size: int) -> None:
    path.write_bytes(b'x' * size)


def _posix_slashes(p):
    r"""A to_local for the fake dir: the locator builds Windows-style
    candidates (``dir\entry``); on Linux those need the backslashes
    turned into separators before isfile/getsize."""
    return p.replace('\\', '/')


def test_locator_picks_the_client_exe_in_the_install_dir(tmp_path):
    """The install dir shape: the CHINESE-named client exe (large)
    beside `Uninstall 比特浏览器.exe` and `elevate.exe` — the largest
    non-rejected exe directly in the dir must be the client."""
    _write(tmp_path / '比特浏览器.exe', 10 * 1024 * 1024)
    _write(tmp_path / 'Uninstall 比特浏览器.exe', 512 * 1024)
    _write(tmp_path / 'elevate.exe', 256 * 1024)
    exe = main_exe_in_dir(BITBROWSER_WIN_APP, str(tmp_path), _posix_slashes)
    assert exe is not None
    assert exe.endswith('比特浏览器.exe')


def test_locator_twist_an_uninstaller_only_dir_is_rejected(tmp_path):
    """Negative control: the pick is 'largest NON-REJECTED exe' — a
    dir holding only an uninstaller-shaped name yields nothing (never
    launches an uninstaller), even though it is the largest exe."""
    _write(tmp_path / 'Uninstall 比特浏览器.exe', 512 * 1024)
    exe = main_exe_in_dir(BITBROWSER_WIN_APP, str(tmp_path), _posix_slashes)
    assert exe is None


def test_locator_spec_declares_the_bitbrowser_shape():
    assert BITBROWSER_WIN_APP.key == 'bitbrowser'
    assert BITBROWSER_WIN_APP.match_tokens == ('bitbrowser', '比特浏览器')
    assert BITBROWSER_WIN_APP.dir_names == ('bitbrowser',)
    # The exe name is a locale string → discovered inside the dir.
    assert BITBROWSER_WIN_APP.exe_names == ()
