"""SPAStaticFiles: client-side-routing history fallback.

A hard load of a client route (refresh / deep-link / bookmark) must
serve index.html so the SPA boots and its router renders the route —
while real assets are still served and API 404s are never masked.

Uses httpx AsyncClient + ASGITransport (not Starlette's TestClient,
which the installed Starlette deprecates in favor of httpx2).
"""

from httpx import ASGITransport, AsyncClient
import pytest
from starlette.applications import Starlette

from app.main import SPAStaticFiles

pytestmark = pytest.mark.unit


def _app(tmp_path):
    (tmp_path / 'index.html').write_text(
        '<!doctype html><title>SPA-SHELL</title>'
    )
    (tmp_path / 'assets').mkdir()
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
