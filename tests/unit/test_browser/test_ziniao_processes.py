"""The one rule for "this process belongs to the Ziniao client".

Kill-all (Ziniao's own doc: stop the main client AND its kernels before a
launch) is only safe if this rule is exact: every Ziniao process — found
on a real Windows install: ``ziniao.exe`` ×6, ``ziniao-gateway.exe`` and
``env-kit.exe`` in the install dir, ``ziniaobrowser.exe`` kernels and a
``node.exe`` under ``%APPDATA%\\ziniaobrowser\\env-kit`` — and nothing
else: never the updater mid-update, never this server (which may run
from a checkout named ``*ziniao*``), never an unrelated node/chrome.
"""

import os
from unittest import mock

import pytest

from app.browser import win_app_locator as win_locator, ziniao_processes as zp
from app.browser.ziniao_processes import install_dirs_of, is_ziniao_process

pytestmark = pytest.mark.unit

INSTALL = 'C:\\Program Files\\ziniao'
KIT = 'C:\\Users\\u\\AppData\\Roaming\\ziniaobrowser\\env-kit'


@pytest.mark.parametrize(
    'name,exe',
    [
        ('ziniao.exe', INSTALL + '\\ziniao.exe'),
        ('ziniao-gateway.exe', INSTALL + '\\resources\\ziniao-gateway.exe'),
        (
            'env-kit.exe',
            INSTALL + '\\resources\\app.asar.unpacked\\envkit\\env-kit.exe',
        ),
        ('ziniaobrowser.exe', KIT + '\\Core\\chrome_64_142\\ziniaobrowser.exe'),
        ('node.exe', KIT + '\\agentic\\node.exe'),
        (
            'superbrowser.exe',
            'C:\\Users\\u\\AppData\\Local\\SuperBrowserCores\\Chromium_x64_134\\superbrowser.exe',
        ),
        (
            'chrome_proxy.exe',
            'C:\\Users\\u\\AppData\\Local\\SuperBrowserCores\\Chromium_x64_134\\chrome_proxy.exe',
        ),
        ('ziniao', '/Applications/ziniao.app/Contents/MacOS/ziniao'),
        (
            'ziniao Helper',
            '/Applications/ziniao.app/Contents/Frameworks/ziniao Helper.app/Contents/MacOS/ziniao Helper',
        ),
        (
            'Chromium',
            '/Volumes/Data/Library/Application Support/ziniaobrowser/env-kit/Core/chrome_x/Chromium',
        ),
        ('ziniaobrowser', '/opt/ziniao/ziniaobrowser'),
        ('superbrowser.exe', None),  # path unreadable: the name decides
        ('ziniao.exe', None),
    ],
)
def test_every_ziniao_process_matches(name, exe):
    assert is_ziniao_process(name, exe)


@pytest.mark.parametrize(
    'name,exe',
    [
        # Not ours, even with "ziniao" somewhere in the path.
        (
            'python.exe',
            'C:\\work\\vibe-seller-ziniao-fix\\.venv\\Scripts\\python.exe',
        ),
        ('python3', '/opt/src/vibe-seller-ziniao-fix/.venv/bin/python3'),
        ('node.exe', 'C:\\Program Files\\nodejs\\node.exe'),
        (
            'chrome.exe',
            'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
        ),
        # A process merely NAMED like an old kernel, elsewhere.
        ('superbrowser.exe', 'D:\\games\\superbrowser.exe'),
    ],
)
def test_bystanders_never_match(name, exe):
    assert not is_ziniao_process(name, exe)


def test_the_updater_folder_is_never_touched():
    """An update stages its own ``ziniao.exe``: killing it mid-update
    leaves the install broken. Excluded by folder, whatever its name."""
    staged = 'C:\\Users\\u\\AppData\\Local\\ziniaobrowser-updater\\pending\\ziniao.exe'
    assert not is_ziniao_process('ziniao.exe', staged)


@pytest.mark.parametrize(
    'name',
    [
        'ziniao_x64_6.26.6.26_production.exe',
        'Uninstall ziniao.exe',
        'ziniao-setup.exe',
    ],
)
def test_installers_are_never_touched_even_without_a_path(name):
    """Elevated installers hide their path; their name still excludes
    them."""
    assert not is_ziniao_process(name, None)


def test_a_custom_install_folder_is_learned_from_the_main_client():
    """Installed to ``D:\\紫鸟``: helpers there carry no Ziniao name."""
    main = zp.ZiniaoProcess(1, 'ziniao.exe', 'D:\\紫鸟\\ziniao.exe')
    dirs = install_dirs_of([main])
    helper = 'D:\\紫鸟\\resources\\app.asar.unpacked\\envkit\\env-kit.exe'
    assert is_ziniao_process('env-kit.exe', helper, dirs)
    assert not is_ziniao_process('env-kit.exe', helper)


def test_this_server_and_its_parents_are_never_listed():
    me = os.getpid()
    rows = [
        mock.MagicMock(info={'pid': me, 'name': 'ziniao.exe', 'exe': None}),
        mock.MagicMock(info={'pid': 99999, 'name': 'ziniao.exe', 'exe': None}),
    ]
    with mock.patch('psutil.process_iter', return_value=rows):
        found = zp.list_ziniao_processes(wsl=False)
    assert [p.pid for p in found] == [99999]


def test_describe_counts_without_listing_every_pid():
    procs = [zp.ZiniaoProcess(i, 'ziniao.exe', None) for i in range(3)]
    procs.append(zp.ZiniaoProcess(9, 'env-kit.exe', None))
    assert zp.describe(procs) == 'env-kit.exe, ziniao.exe ×3'


def test_kill_all_remembers_the_main_exe_it_stopped():
    """The launch that follows the kill needs the install path when no
    process answers anymore — kill_all_ziniao must remember the main
    exe it observed BEFORE stopping it (helpers and kernels do not
    count; only the main client does)."""
    win_locator.clear_locator_cache()
    main = zp.ZiniaoProcess(21, 'ziniao.exe', 'D:\\紫鸟\\ziniao\\ziniao.exe')
    kernel = zp.ZiniaoProcess(
        22, 'ziniaobrowser.exe', 'C:\\Users\\u\\ziniaobrowser\\kernel.exe'
    )
    with mock.patch(
        'app.browser.ziniao_processes.list_ziniao_processes'
    ) as list_mock:
        # First listing: main + kernel alive; second: none (killed).
        list_mock.side_effect = [[main, kernel], []]
        report = zp.kill_all_ziniao(wsl=False)
    assert report.killed == [main, kernel]
    assert win_locator.remembered_process_path('ziniao') == (
        'D:\\紫鸟\\ziniao\\ziniao.exe'
    )
    win_locator.clear_locator_cache()


def test_kill_all_with_no_ziniao_running_remembers_nothing():
    win_locator.clear_locator_cache()
    with mock.patch(
        'app.browser.ziniao_processes.list_ziniao_processes',
        return_value=[],
    ):
        zp.kill_all_ziniao(wsl=False)
    assert win_locator.remembered_process_path('ziniao') is None
