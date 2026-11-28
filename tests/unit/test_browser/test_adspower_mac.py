"""The AdsPower macOS layer (unit tests on Linux, seams everywhere).

§A of ADDENDUM-2: the layer is chosen by platform; the mac local_api
path; the launch path (GUI via the shared locator's open — R22: no
headless form is ever started); kernel evidence and the per-profile
tree kill through psutil; the platform gate.
"""

from unittest import mock

import pytest

import app.browser.adapters.adspower as ads_mod
import app.browser.adapters.adspower_backend as ads_backend
from app.browser.adapters.adspower_backend import AdsPowerBackend
import app.browser.adapters.adspower_mac as mac_mod
from app.browser.inject.errors import UnsupportedPlatform

pytestmark = pytest.mark.unit


# ── the layer is chosen by platform ────────────────────────────────


def test_platform_picks_the_mac_layer(monkeypatch):
    monkeypatch.setattr(ads_mod, 'get_platform', lambda: 'mac')
    layer = ads_mod._platform_layer_module()
    assert layer is mac_mod
    proc = layer.make_process_layer('/x/AdsPower Global.app')
    assert isinstance(proc, mac_mod.MacAdsPowerLayer)


def test_platform_picks_the_win_layer(monkeypatch):
    for platform in ('windows', 'wsl'):
        monkeypatch.setattr(ads_mod, 'get_platform', lambda p=platform: p)
        assert ads_mod._platform_layer_module() is ads_mod.adspower_win


# ── the mac local_api path ─────────────────────────────────────────


def test_local_api_file_path_under_home(monkeypatch, tmp_path):
    monkeypatch.setenv('HOME', str(tmp_path))
    assert mac_mod.local_api_file_path() == str(
        tmp_path
        / 'Library/Application Support/adspower_global'
        / 'cwd_global/source/local_api'
    )


def test_read_port_from_mac_file(monkeypatch, tmp_path):
    f = (
        tmp_path
        / 'Library/Application Support/adspower_global'
        / 'cwd_global/source'
    )
    f.mkdir(parents=True)
    (f / 'local_api').write_text(
        'http://local.adspower.com:51234/', encoding='utf-8'
    )
    monkeypatch.setenv('HOME', str(tmp_path))
    assert mac_mod.read_local_api_port() == 51234


def test_read_port_missing_file_is_none(monkeypatch, tmp_path):
    monkeypatch.setenv('HOME', str(tmp_path / 'nope'))
    assert mac_mod.read_local_api_port() is None


# ── launch: GUI via the locator ────────────────────────────────────


def _located(
    exe='/Applications/AdsPower Global.app/Contents/MacOS/AdsPower Global',
):
    return type(
        'L',
        (),
        {
            'app_path': '/Applications/AdsPower Global.app',
            'exe_path': exe,
            'bundle_id': 'com.adspower.global',
        },
    )()


def test_gui_launch_uses_the_shared_locator(monkeypatch):
    calls = []
    monkeypatch.setattr(
        mac_mod.mac_locator, 'locate', lambda spec, **kw: _located()
    )
    monkeypatch.setattr(
        mac_mod.mac_locator,
        'launch',
        lambda located, args=(): calls.append((located.app_path, args)),
    )
    assert mac_mod.MacAdsPowerLayer().launch() is True
    ((app, args),) = calls
    assert app == '/Applications/AdsPower Global.app'
    assert args == ()  # the GUI takes no flags


def test_gui_launch_failure_is_false(monkeypatch):
    monkeypatch.setattr(
        mac_mod.mac_locator, 'locate', lambda spec, **kw: _located()
    )

    def boom(located, args=()):
        raise RuntimeError('open rc=1')

    monkeypatch.setattr(mac_mod.mac_locator, 'launch', boom)
    assert mac_mod.MacAdsPowerLayer().launch() is False


# ── kernel evidence (psutil) and the per-profile tree kill ─────────


class FakeProc:
    def __init__(self, pid, name, cmdline):
        self.info = {'pid': pid, 'name': name, 'cmdline': cmdline}


def _iter_procs(monkeypatch, procs):
    monkeypatch.setattr(
        mac_mod.psutil,
        'process_iter',
        lambda attrs=None: iter(procs),
    )


def test_kernel_evidence_attributes_by_user_data_dir(monkeypatch):
    # Name-agnostic on purpose: attribution comes from the
    # user-data-dir, so an unexpected macOS image name cannot
    # misattribute a tree (names verified live in §B).
    _iter_procs(
        monkeypatch,
        [
            FakeProc(
                11,
                'WhateverBrowser',
                [
                    '/x/FlowerBrowser',
                    '--user-data-dir=/d/cache/k1_a1_flower',
                    '--marionette',
                ],
            ),
            FakeProc(
                12,
                'OtherName',
                ['--user-data-dir=/d/cache/k2_a2', '-foreground'],
            ),
            FakeProc(13, 'Chrome Helper', ['--nope']),
        ],
    )
    kernels = mac_mod.list_kernel_processes()
    assert [(k.pid, k.is_firefox) for k in kernels] == [
        (11, True),  # the _flower suffix marks Firefox
        (12, False),
    ]


def test_kernel_evidence_matches_paths_with_spaces(monkeypatch):
    # The real macOS kernel path carries a space ("Application
    # Support" — verified live on macOS, client 8.9.23). psutil hands
    # us ONE argv element at a time, so a space inside the path cannot
    # cross an argument boundary — the match must tolerate it. (The
    # shared Windows pattern's \S* could not, which left the kernel
    # evidence — D7's orphan retry, the FlowerBrowser cleanup — blind
    # on macOS until this test's fix.)
    path = (
        '/Users/demo/Library/Application Support/adspower_global/'
        'cwd_global/source/cache/p1_i1'
    )
    _iter_procs(
        monkeypatch,
        [
            FakeProc(
                21,
                'SunBrowser',
                [
                    '/Applications/Some Client.app/Contents/MacOS/SunBrowser',
                    f'--user-data-dir={path}',
                    '--no-sandbox',
                ],
            ),
        ],
    )
    kernels = mac_mod.list_kernel_processes()
    assert [k.user_data_dir for k in kernels] == [path]
    # And the D7 attribution still keys off the LAST path segment.
    assert ads_mod.AdsPowerClient._kernels_for(kernels, 'p1') == kernels
    assert ads_mod.AdsPowerClient._kernels_for(kernels, 'p') == []


def test_kill_targets_only_that_tree(monkeypatch):
    killed = []

    class P:
        def __init__(self, pid, children=()):
            self.pid = pid
            self._children = children

        def children(self, recursive=True):
            out = list(self._children)
            if recursive:  # like psutil: the whole subtree
                for c in self._children:
                    out.extend(c.children(recursive=True))
            return out

        def kill(self):
            killed.append(self.pid)

    grandchild = P(3)
    child = P(2, [grandchild])
    parent = P(1, [child])
    # pid 9 is ANOTHER profile's kernel: never a child of 1, so the
    # tree walk must never reach it.
    monkeypatch.setattr(
        mac_mod.psutil,
        'Process',
        lambda pid: parent if pid == 1 else P(pid),
    )
    monkeypatch.setattr(
        mac_mod.psutil, 'wait_procs', lambda procs, timeout=None: []
    )
    assert mac_mod.kill_process_tree(1) is True
    assert 9 not in killed
    assert sorted(killed) == [1, 2, 3]


# ── the platform gate (gating rule) ────────────────────────────────


def test_mac_is_in_the_platform_set():
    # The Mac live pass is recorded (task a3f8c7d6, baidu.com in the
    # AdsPower store browser): 'mac' is in the spec's platforms.
    assert ads_backend.ADSPOWER_VENDOR.platforms == frozenset({
        'windows',
        'mac',
        'wsl',
    })


async def test_mac_platform_passes_the_backend_gate():
    # 'mac' is a supported platform now (the live pass is recorded:
    # task a3f8c7d6). The gate passes; the flow then fails on Linux
    # with anything OTHER than UnsupportedPlatform.
    with mock.patch(
        'app.browser.adapters.adspower_backend.get_platform',
        return_value='mac',
    ):
        with pytest.raises(Exception) as ei:
            await AdsPowerBackend().start({'browser_ref': 'x'})
        assert not isinstance(ei.value, UnsupportedPlatform)
