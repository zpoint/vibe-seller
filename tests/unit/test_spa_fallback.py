"""SPAStaticFiles: client-side-routing history fallback.

A hard load of a client route (refresh / deep-link / bookmark) must
serve index.html so the SPA boots and its router renders the route —
while real assets are still served and API 404s are never masked.

Uses httpx AsyncClient + ASGITransport (not Starlette's TestClient,
which the installed Starlette deprecates in favor of httpx2).
"""

from fastapi.responses import Response
from httpx import ASGITransport, AsyncClient
import pytest
from starlette.applications import Starlette

from app import main
from app.main import SPAStaticFiles, cache_control_for, url_form

pytestmark = pytest.mark.unit


def _app(tmp_path):
    (tmp_path / 'index.html').write_text(
        '<!doctype html><title>SPA-SHELL</title>'
    )
    (tmp_path / 'assets').mkdir(exist_ok=True)
    (tmp_path / 'assets' / 'app.js').write_text('console.log("app")')
    app = Starlette()
    app.mount('/', SPAStaticFiles(directory=str(tmp_path), html=True), name='s')
    return app


async def _get(tmp_path, path):
    transport = ASGITransport(app=_app(tmp_path))
    async with AsyncClient(
        transport=transport, base_url='http://test'
    ) as client:
        return await client.get(path)


@pytest.mark.asyncio
async def test_real_asset_is_served(tmp_path):
    r = await _get(tmp_path, '/assets/app.js')
    assert r.status_code == 200
    assert 'console.log' in r.text


@pytest.mark.asyncio
async def test_client_route_falls_back_to_index(tmp_path):
    # No file at /settings/ai — must serve the SPA shell, not 404.
    r = await _get(tmp_path, '/settings/ai')
    assert r.status_code == 200
    assert 'SPA-SHELL' in r.text


@pytest.mark.asyncio
async def test_deep_task_route_falls_back(tmp_path):
    r = await _get(tmp_path, '/tasks/some-uuid')
    assert r.status_code == 200
    assert 'SPA-SHELL' in r.text


@pytest.mark.asyncio
async def test_api_404_is_not_masked(tmp_path):
    # An unknown /api path must stay a 404, never the SPA shell.
    r = await _get(tmp_path, '/api/does-not-exist')
    assert r.status_code == 404
    assert 'SPA-SHELL' not in r.text


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['/', '/index.html', '/settings/ai'])
async def test_html_shell_is_always_revalidated(tmp_path, path):
    # A browser must not serve a stale shell after an upgrade: it names
    # the hashed bundle, so a heuristically cached one boots the OLD
    # frontend against the NEW API.
    r = await _get(tmp_path, path)
    assert r.headers['cache-control'] == 'no-cache'


@pytest.mark.asyncio
async def test_hashed_asset_is_cached_for_good(tmp_path):
    # A new release ships new names, so the name IS the invalidation.
    (tmp_path / 'assets').mkdir(exist_ok=True)
    (tmp_path / 'assets' / 'index-CAD1mfOD.js').write_text('x')
    r = await _get(tmp_path, '/assets/index-CAD1mfOD.js')
    assert 'immutable' in r.headers['cache-control']


@pytest.mark.asyncio
@pytest.mark.parametrize('name', ['app.js', 'logo.svg'])
async def test_unhashed_file_is_revalidated(tmp_path, name):
    # Same name across releases (icons, an unhashed script): cached,
    # but checked by ETag so a changed file is never hidden.
    (tmp_path / 'assets').mkdir(exist_ok=True)
    (tmp_path / 'assets' / 'logo.svg').write_text('<svg/>')
    r = await _get(tmp_path, f'/assets/{name}')
    assert r.headers['cache-control'] == 'no-cache'
    assert r.headers.get('etag')


@pytest.mark.asyncio
async def test_plugin_bundle_route_is_revalidated(monkeypatch):
    # Plugin JS sits at a fixed route, outside the SPA mount.
    monkeypatch.setattr(
        main, '_plugin_bundle_paths', lambda: {'/api/test-plugin.js'}
    )
    main.app.add_api_route(
        '/api/test-plugin.js',
        lambda: Response('x', media_type='application/javascript'),
        methods=['GET'],
    )
    transport = ASGITransport(app=main.app)
    async with AsyncClient(
        transport=transport, base_url='http://test'
    ) as client:
        r = await client.get('/api/test-plugin.js')
    assert r.headers['cache-control'] == 'no-cache'


@pytest.mark.asyncio
async def test_missing_build_asset_is_a_real_404(tmp_path):
    # Never the HTML shell under a hashed .js URL (it would be cached
    # as immutable JavaScript).
    r = await _get(tmp_path, '/assets/index-GONE1234.js')
    assert r.status_code == 404


def test_cache_policy_is_decided_on_the_url_form_of_a_windows_path():
    # StaticFiles passes an OS path to get_response: backslashes on
    # Windows. On the real Windows box the hashed bundle then got
    # no-cache and a missing asset fell back to the HTML shell.
    assert url_form('assets\\index-CAD1mfOD.js') == 'assets/index-CAD1mfOD.js'
    assert 'immutable' in cache_control_for(
        url_form('assets\\index-CAD1mfOD.js')
    )
    assert cache_control_for(url_form('assets\\logo.svg')) == 'no-cache'
