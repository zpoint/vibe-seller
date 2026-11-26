"""E2E: a failed in-app download sends the admin to the manual route.

The real app — the built frontend, served and answered by the real
server — in a real browser. What cannot be real here is the install: a
test server runs from a checkout, so it is a dev build that never
downloads anything (``supported`` is false). So each scenario TWISTS the
server's own answers: the browser's ``/api/system/update-status`` and
``/api/system/update-check`` requests go to the server as usual, and only
the fields that describe the install and its update are overwritten on
the way back (version, ``supported``, the state, the failure). The
payload shape is always the server's — a field the backend stopped
sending would surface here, not be papered over by a fixture.

What a person sees, scenario by scenario:

- Windows, the background download failed → the manual update popup
  pops (the one an install that cannot update itself gets: what is new,
  the installer link) with one line saying the automatic update failed
  and why; "稍后" leaves a red chip that reopens
  it; a retry that downloads shows progress instead; failing again pops
  it again; a retry that succeeds hands over to the "重启并更新" popup.
- The admin updated by hand → the server comes back on the new version,
  the page reloads itself, and nothing about the failure is left.
- macOS, where update-check itself came back empty → the guide still
  gives the ``vibe-seller upgrade`` command, from the status alone.

Screenshots land in the evidence dir (uploaded by CI) for review.
"""

import copy

from playwright.sync_api import Page, Route, expect
import pytest

from tests.e2e.evidence import Evidence

pytestmark = [pytest.mark.e2e]

STATUS_URL = '**/api/system/update-status'
CHECK_URL = '**/api/system/update-check'
INFO_URL = '**/api/system/info'
RELEASES = 'https://github.com/zpoint/vibe-seller/releases/latest'
#: One idle poll is 10 s; a state change shows within the next one.
POLL_MS = 25_000

ERROR = 'installer size mismatch: expected 720487430, got 41943040'


def _failure(attempts=1, path='installer', stage='download'):
    return {
        'version': '0.3.6',
        'path': path,
        'stage': stage,
        'error': ERROR,
        'attempts': attempts,
        'at': 1764118908.0,
    }


def _windows_failed(attempts=1):
    """What a Windows install on 0.3.4 answers after a failed download."""
    return {
        'supported': True,
        'state': 'error',
        'version': '0.3.6',
        'current_version': '0.3.4',
        'platform': 'windows',
        'path': 'installer',
        'error': ERROR,
        'downloaded_bytes': None,
        'total_bytes': None,
        'popup_due': False,
        'ready_since': None,
        'download_failure': _failure(attempts),
        'manual_due': True,
        'manual_update': {'upgrade_command': None, 'download_url': RELEASES},
    }


def _check(**over):
    doc = {
        'dev': False,
        'update_available': True,
        'current_version': '0.3.4',
        'latest_version': '0.3.6',
        'channel': 'stable',
        'platform': 'windows',
        'upgrade_command': None,
        'download_url': RELEASES,
        'releases_page_url': RELEASES,
        'in_app_update': True,
        'releases': [
            {
                'version': '0.3.6',
                'name': 'v0.3.6',
                'body': '',
                'url': RELEASES,
                'published_at': '2026-11-25T08:47:46Z',
            }
        ],
        'highlights': [
            {
                'version': '0.3.6',
                'zh': '- 示例亮点：更新失败时给出手动更新指引',
                'en': '- Example highlight: a manual route when an update '
                'cannot download',
            }
        ],
        'notes': [],
    }
    doc.update(over)
    return doc


class Twist:
    """The server's answers, with the install's update fields replaced.

    Mutate ``status`` / ``check`` mid-test: the page's next poll gets
    the new answer, exactly as if the machine had moved on.
    """

    def __init__(self, page: Page, status: dict, check: dict):
        self.status = copy.deepcopy(status)
        self.check = copy.deepcopy(check)
        #: update-status answers served so far — how a test knows the
        #: page has heard the latest twist (an absence can't be awaited).
        self.served = 0
        page.route(STATUS_URL, self._status)
        page.route(CHECK_URL, self._check)
        # The sidebar's version label: the same install, so the same
        # version — a dev build would show its commit there instead.
        page.route(INFO_URL, self._info)

    def wait_for_answer(self, page: Page, after: int) -> None:
        for _ in range(int(POLL_MS / 200)):
            if self.served > after:
                return
            page.wait_for_timeout(200)
        raise AssertionError('the page never asked for update-status')

    def _overlay(self, route: Route, fields: dict) -> None:
        response = route.fetch()
        body = response.json()
        body.update(fields)
        route.fulfill(response=response, json=body)

    def _status(self, route: Route) -> None:
        self._overlay(route, self.status)
        self.served += 1

    def _check(self, route: Route) -> None:
        self._overlay(route, self.check)

    def _info(self, route: Route) -> None:
        self._overlay(
            route,
            {
                'version': self.status['current_version'],
                'platform': self.status['platform'],
                'release_channel': 'stable',
                'commit': None,
            },
        )


def _zh(page: Page) -> None:
    page.evaluate("() => localStorage.setItem('vibe-seller-language', 'zh')")


def _guide(page: Page):
    """The one line the manual popup gains when a download failed —
    present exactly when the popup is up for that reason."""
    return page.get_by_test_id('update-auto-failed-hint')


def _chip(page: Page):
    return page.get_by_test_id('update-chip')


def _shot(ev: Evidence, page: Page, name: str) -> None:
    page.wait_for_timeout(400)  # let colour transitions finish
    ev.shot(page, name)


def _first_status(page: Page) -> None:
    """Load the app and wait for its first update-status answer."""
    with page.expect_response(lambda r: '/api/system/update-status' in r.url):
        page.reload()


def test_windows_download_failure_walk(authenticated_page: Page):
    page = authenticated_page
    ev = Evidence('e2e-update-download-failed-windows')
    twist = Twist(page, _windows_failed(), _check())
    _zh(page)
    _first_status(page)

    # 1. The download failed: the manual popup pops by itself.
    guide = _guide(page)
    expect(guide).to_be_visible(timeout=POLL_MS)
    expect(guide).to_have_text(f'自动更新失败，请手动更新。失败原因：{ERROR}')
    expect(page.get_by_role('heading', name='有可用更新')).to_be_visible()
    expect(
        page.get_by_text('当前版本 v0.3.4 已过时，最新版本为 v0.3.6。')
    ).to_be_visible()
    expect(
        page.get_by_text('示例亮点：更新失败时给出手动更新指引')
    ).to_be_visible()
    link = page.get_by_role('link', name='下载最新版本')
    expect(link).to_have_attribute('href', RELEASES)
    _shot(ev, page, '01-guide-pops-after-failed-download')

    # 2. "稍后": the guide goes, a red chip stays and reopens it.
    page.get_by_role('button', name='稍后提醒').click()
    expect(guide).to_have_count(0)
    chip = _chip(page)
    expect(chip).to_have_text('v0.3.6 自动更新失败 · 点此手动更新')
    _shot(ev, page, '02-later-leaves-the-chip')
    chip.click()
    expect(guide).to_be_visible()
    page.get_by_role('button', name='稍后提醒').click()
    expect(guide).to_have_count(0)

    # 3. Six hours on, the cycle retries: progress, not the guide.
    twist.status.update({
        'state': 'downloading',
        'error': None,
        'manual_due': False,
        'downloaded_bytes': 439_500_000,
        'total_bytes': 720_487_430,
    })
    expect(chip).to_have_text('正在下载 v0.3.6 · 61%', timeout=POLL_MS)
    expect(guide).to_have_count(0)
    _shot(ev, page, '03-retry-downloading-shows-progress')

    # 4. It failed again: a NEW failure, so the guide is back even
    #    though "稍后" was clicked for the first one.
    twist.status = _windows_failed(attempts=2)
    expect(guide).to_be_visible(timeout=POLL_MS)
    expect(page.get_by_role('link', name='下载最新版本')).to_be_visible()
    _shot(ev, page, '04-failed-again-pops-again')
    page.get_by_role('button', name='稍后提醒').click()

    # 5. The next retry got it: the in-app flow is back in charge.
    twist.status.update({
        'state': 'ready',
        'error': None,
        'popup_due': True,
        'ready_since': 1764140000.0,
        'download_failure': None,
        'manual_due': False,
    })
    restart = page.get_by_role('button', name='重启并更新')
    expect(restart).to_be_visible(timeout=POLL_MS)
    expect(guide).to_have_count(0)
    expect(chip).to_have_text('v0.3.6 已下载好 · 重启以更新')
    _shot(ev, page, '05-retry-succeeded-ready-popup')


def test_updating_by_hand_ends_the_guide(authenticated_page: Page):
    page = authenticated_page
    ev = Evidence('e2e-update-download-failed-manual-update')
    twist = Twist(page, _windows_failed(), _check())
    _zh(page)
    _first_status(page)
    expect(_guide(page)).to_be_visible(timeout=POLL_MS)
    expect(page.get_by_title('Server: 0.3.4')).to_be_visible()
    _shot(ev, page, '00-before-guide-on-0.3.4')

    # The admin downloaded the installer and ran it: the server comes
    # back on 0.3.6, which is no longer older than the failed release,
    # so the server reports nothing. The page reloads by itself when it
    # sees the new version (the same rule every open tab follows).
    with page.expect_event('load', timeout=POLL_MS):
        twist.status = {
            'supported': True,
            'state': 'idle',
            'version': None,
            'current_version': '0.3.6',
            'platform': 'windows',
            'path': None,
            'error': None,
            'download_failure': None,
            'manual_due': False,
        }
        twist.check = {
            'dev': False,
            'update_available': False,
            'current_version': '0.3.6',
            'channel': 'stable',
        }
    served = twist.served
    expect(page.locator('h1')).to_have_text('Vibe Seller')
    twist.wait_for_answer(page, after=served)
    page.wait_for_timeout(1000)  # one render after the answer
    expect(page.get_by_title('Server: 0.3.6')).to_be_visible()
    expect(_guide(page)).to_have_count(0)
    expect(_chip(page)).to_have_count(0)
    _shot(ev, page, '01-after-manual-update-nothing-left')


def test_mac_guide_gives_the_command_even_without_a_check(
    authenticated_page: Page,
):
    page = authenticated_page
    ev = Evidence('e2e-update-download-failed-mac')
    status = _windows_failed()
    status.update({
        'platform': 'mac',
        'path': 'pip',
        'state': 'idle',
        'error': None,
        'version': None,
        'download_failure': _failure(path='pip', stage='prefetch'),
        'manual_update': {
            'upgrade_command': 'vibe-seller upgrade',
            'download_url': None,
        },
    })
    # The network that broke the download broke the check too: it came
    # back with no release and no route.
    Twist(
        page,
        status,
        {'dev': False, 'update_available': False, 'current_version': '0.3.4'},
    )
    _first_status(page)  # English: the fixture's language

    guide = _guide(page)
    expect(guide).to_be_visible(timeout=POLL_MS)
    expect(guide).to_contain_text(
        'Automatic update failed — please update by hand. Reason: '
    )
    expect(page.get_by_role('heading', name='Update available')).to_be_visible()
    expect(
        page.get_by_text('v0.3.4 is out of date — v0.3.6 is now available.')
    ).to_be_visible()
    expect(page.get_by_text('vibe-seller upgrade')).to_be_visible()
    expect(page.get_by_role('button', name='Copy')).to_be_visible()
    _shot(ev, page, '01-guide-gives-the-command')
    page.get_by_role('button', name='Remind me later').click()
    expect(_chip(page)).to_have_text(
        'v0.3.6 automatic update failed · update by hand'
    )
    _shot(ev, page, '02-chip-after-later')
