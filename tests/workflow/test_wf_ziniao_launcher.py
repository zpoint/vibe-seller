"""The /api/ziniao/launcher download is generated, not static.

The launcher a WSL user double-clicks must carry the exe path the
server's locator found (custom / Chinese install dirs included); the
locator runs in a worker thread (it shells out to PowerShell).
"""

from unittest import mock

from app.browser.win_app_locator import LocatedApp

CUSTOM = r'C:\紫鸟\ziniao\ziniao.exe'


class TestZiniaoLauncherDownload:
    async def test_bakes_the_located_custom_path(self, admin_client):
        hit = LocatedApp(exe_path=CUSTOM, hint='uninstall', detail='test')
        with mock.patch(
            'app.routers.browser.locate', return_value=hit
        ) as locate_mock:
            resp = await admin_client.get('/api/ziniao/launcher')
        assert resp.status_code == 200, resp.text
        assert resp.headers['content-type'].startswith(
            'application/octet-stream'
        )
        assert 'ziniao_webdriver.bat' in resp.headers['content-disposition']
        body = resp.content.decode('utf-8')
        assert f'set "ZINIAO_BAKED={CUSTOM}"' in body
        assert 'chcp 65001' in body
        # Blocking discovery never ran on the event loop.
        locate_mock.assert_called_once()

    async def test_nothing_located_serves_the_fallback_script(
        self, admin_client
    ):
        with mock.patch('app.routers.browser.locate', return_value=None):
            resp = await admin_client.get('/api/ziniao/launcher')
        assert resp.status_code == 200, resp.text
        body = resp.content.decode('utf-8')
        assert 'ZINIAO_BAKED' not in body
        assert r'C:\Program Files\ziniao\ziniao.exe' in body
