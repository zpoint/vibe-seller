"""Store setup's Ziniao store list: Ziniao is relaunched, never a prompt.

The user's spec for the store-setup page: "店铺配置也是直接 Kill 加载，不要
这个是否要关掉的交互" — a Ziniao left in normal mode is killed and relaunched
and the list loads; there is no "close Ziniao / Force Restart" step and no
``/restart`` endpoint. While Ziniao is still starting the page is told so
(503 ``starting``) and asks again.
"""

import json
from unittest import mock
import uuid

from httpx import AsyncClient
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ziniao_account import ZiniaoAccount
from app.utils.crypto import encrypt_password
from tests.unit.test_browser.ziniao_fake_host import (
    MAIN_EXE,
    NORMAL_MODE,
    FakeZiniaoHost,
    installed,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def _account(db: AsyncSession) -> ZiniaoAccount:
    account = ZiniaoAccount(
        id=str(uuid.uuid4()),
        name='acct',
        company='acme',
        username='ops',
        encrypted_password=encrypt_password('pw'),
        socket_port=16851,
        client_path=MAIN_EXE,
    )
    db.add(account)
    await db.commit()
    return account


async def test_normal_mode_is_relaunched_and_the_list_loads(
    authenticated_client: AsyncClient, async_db_session: AsyncSession
):
    account = await _account(async_db_session)
    host = FakeZiniaoHost(api='down', procs=NORMAL_MODE)
    with installed(host):
        r = await authenticated_client.get(
            f'/api/ziniao-accounts/{account.id}/browsers'
        )
    assert r.status_code == 200, r.text
    assert r.json() == [{'browser_name': 'A', 'browser_oauth': ''}]
    assert len(host.launches) == 1
    assert sorted(n for n, _ in host.killed) == sorted(
        n for n, _ in NORMAL_MODE
    )


async def test_there_is_no_restart_endpoint(
    authenticated_client: AsyncClient, async_db_session: AsyncSession
):
    account = await _account(async_db_session)
    r = await authenticated_client.post(
        f'/api/ziniao-accounts/{account.id}/restart'
    )
    assert r.status_code in (404, 405)


async def test_still_starting_answers_starting_for_the_page_to_poll(
    authenticated_client: AsyncClient, async_db_session: AsyncSession
):
    from app.browser.ziniao_startup import ZiniaoStartingError  # noqa: PLC0415

    account = await _account(async_db_session)
    with mock.patch(
        'app.routers.ziniao_accounts.ensure_ziniao_running',
        side_effect=ZiniaoStartingError('Ziniao is starting (47s)', 47.2),
    ):
        r = await authenticated_client.get(
            f'/api/ziniao-accounts/{account.id}/browsers'
        )
    assert r.status_code == 503
    assert json.loads(r.json()['detail']) == {
        'status': 'starting',
        'elapsed_s': 47,
    }


async def test_a_real_start_failure_says_why(
    authenticated_client: AsyncClient, async_db_session: AsyncSession
):
    from app.browser.ziniao_startup import ZiniaoStartError  # noqa: PLC0415

    account = await _account(async_db_session)
    with mock.patch(
        'app.routers.ziniao_accounts.ensure_ziniao_running',
        side_effect=ZiniaoStartError(
            'Ziniao did not start: opened 2 times',
            'startup_timeout',
        ),
    ):
        r = await authenticated_client.get(
            f'/api/ziniao-accounts/{account.id}/browsers'
        )
    assert r.status_code == 502
    detail = json.loads(r.json()['detail'])
    assert detail['status'] == 'start_failed'
    assert 'opened 2 times' in detail['message']


async def test_on_wsl_the_page_is_sent_to_the_launcher(
    authenticated_client: AsyncClient, async_db_session: AsyncSession
):
    account = await _account(async_db_session)
    host = FakeZiniaoHost(api='down', procs=NORMAL_MODE)
    with installed(host, wsl=True):
        r = await authenticated_client.get(
            f'/api/ziniao-accounts/{account.id}/browsers'
        )
    assert r.status_code == 502
    assert json.loads(r.json()['detail'])['status'] == 'not_running'
    assert host.launches == []
    assert host.killed  # the normal-mode client was closed


async def test_a_users_refresh_is_not_held_back_by_the_cooldown(
    authenticated_client: AsyncClient, async_db_session: AsyncSession
):
    from app.browser.ziniao_startup import ZiniaoStartingError  # noqa: PLC0415

    account = await _account(async_db_session)
    with mock.patch(
        'app.routers.ziniao_accounts.ensure_ziniao_running',
        new=mock.AsyncMock(side_effect=ZiniaoStartingError('s', 1)),
    ) as ensure:
        await authenticated_client.get(
            f'/api/ziniao-accounts/{account.id}/browsers'
        )
    assert ensure.await_args.kwargs['retry_failed'] is True
