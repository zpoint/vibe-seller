"""External browser-profile binding rules for store creation."""

import asyncio

import pytest

pytestmark = pytest.mark.workflow

_PROFILE_VENDORS = ('ziniao', 'yuehulian', 'adspower', 'wenmai', 'bitbrowser')


def _payload(name: str, backend: str, external_id: str, account=None) -> dict:
    data = {
        'name': name,
        'browser_backend': backend,
        'browser_oauth': external_id,
    }
    if backend == 'ziniao':
        data['ziniao_account_id'] = account or 'account-example-1'
    elif account is not None:
        # Machine-login vendors do not use this column. Supplying different
        # values here proves that they cannot create a second identity scope.
        data['ziniao_account_id'] = account
    return data


@pytest.mark.parametrize('backend', _PROFILE_VENDORS)
async def test_duplicate_external_binding_is_rejected(admin_client, backend):
    """One backend/account/external-id identity can bind only one store."""
    first = await admin_client.post(
        '/api/stores', json=_payload('Original store', backend, 'profile-001')
    )
    duplicate = await admin_client.post(
        '/api/stores', json=_payload('Duplicate store', backend, 'profile-001')
    )

    assert first.status_code == 200
    assert duplicate.status_code == 409, duplicate.text
    detail = duplicate.json()['detail']
    assert isinstance(detail, str)
    assert 'Original store' in detail
    assert 'already bound' in detail
    stores = (await admin_client.get('/api/stores')).json()
    assert [store['name'] for store in stores] == ['Original store']


@pytest.mark.parametrize('backend', _PROFILE_VENDORS)
async def test_different_external_ids_remain_allowed(admin_client, backend):
    """The rule does not limit a vendor or account to one store."""
    first = await admin_client.post(
        '/api/stores', json=_payload('First profile', backend, 'profile-001')
    )
    second = await admin_client.post(
        '/api/stores', json=_payload('Second profile', backend, 'profile-002')
    )

    assert first.status_code == 200
    assert second.status_code == 200


async def test_concurrent_duplicate_creates_have_one_winner(admin_client):
    """The existing create lock closes the check-then-insert race window."""
    responses = await asyncio.gather(
        admin_client.post(
            '/api/stores',
            json=_payload('Concurrent store A', 'wenmai', 'profile-001'),
        ),
        admin_client.post(
            '/api/stores',
            json=_payload('Concurrent store B', 'wenmai', 'profile-001'),
        ),
    )

    assert sorted(response.status_code for response in responses) == [200, 409]
    stores = (await admin_client.get('/api/stores')).json()
    assert len(stores) == 1


async def test_ziniao_duplicate_rule_is_scoped_to_the_account(admin_client):
    """Ziniao profile ids may repeat across accounts, but not within one."""
    first = await admin_client.post(
        '/api/stores',
        json=_payload(
            'Account one store', 'ziniao', 'profile-001', 'account-1'
        ),
    )
    other_account = await admin_client.post(
        '/api/stores',
        json=_payload(
            'Account two store', 'ziniao', 'profile-001', 'account-2'
        ),
    )
    duplicate = await admin_client.post(
        '/api/stores',
        json=_payload(
            'Account one duplicate', 'ziniao', 'profile-001', 'account-1'
        ),
    )

    assert first.status_code == 200
    assert other_account.status_code == 200
    assert duplicate.status_code == 409


async def test_machine_login_scope_ignores_account_metadata(admin_client):
    """Only account-scoped vendors partition profile ids by account id."""
    first = await admin_client.post(
        '/api/stores',
        json=_payload('Login store A', 'adspower', 'profile-001', 'account-1'),
    )
    duplicate = await admin_client.post(
        '/api/stores',
        json=_payload('Login store B', 'adspower', 'profile-001', 'account-2'),
    )

    assert first.status_code == 200
    assert duplicate.status_code == 409


async def test_duplicate_binding_409_detail_is_plain_ui_safe_text(admin_client):
    """storeCreateError displays ordinary details verbatim, not raw JSON."""
    await admin_client.post(
        '/api/stores',
        json=_payload('Readable store', 'wenmai', 'profile-001'),
    )
    response = await admin_client.post(
        '/api/stores',
        json=_payload('Readable duplicate', 'wenmai', 'profile-001'),
    )

    assert response.status_code == 409
    detail = response.json()['detail']
    assert detail == (
        "This browser profile is already bound to store 'Readable store'. "
        'Select a different profile or delete the existing store first.'
    )
    assert not detail.lstrip().startswith(('{', '['))
