"""Workflow tests for the email system (CRUD, SMTP, info)."""

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.workflow


class TestEmailAccountCRUD:
    async def test_create_with_smtp_autodiscovery(self, admin_client):
        r = await admin_client.post(
            '/api/email-accounts',
            json={
                'email': 'test@gmail.com',
                'password': 'test123',
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data['email'] == 'test@gmail.com'
        assert data['imap_host'] == 'imap.gmail.com'
        assert data['smtp_host'] == 'smtp.gmail.com'
        assert data['smtp_port'] == 587
        assert data['smtp_use_tls'] is True

    async def test_create_with_explicit_smtp(self, admin_client):
        r = await admin_client.post(
            '/api/email-accounts',
            json={
                'email': 'custom@custom.com',
                'password': 'pw',
                'imap_host': 'imap.custom.com',
                'smtp_host': 'smtp.custom.com',
                'smtp_port': 465,
                'smtp_use_tls': False,
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data['smtp_host'] == 'smtp.custom.com'
        assert data['smtp_port'] == 465

    async def test_update_smtp_fields(self, admin_client):
        r = await admin_client.post(
            '/api/email-accounts',
            json={
                'email': 'upd@163.com',
                'password': 'pw',
            },
        )
        acct_id = r.json()['id']

        r2 = await admin_client.put(
            f'/api/email-accounts/{acct_id}',
            json={
                'smtp_host': 'new.smtp.com',
                'smtp_port': 587,
            },
        )
        assert r2.status_code == 200
        assert r2.json()['smtp_host'] == 'new.smtp.com'

    async def test_smtp_discover_endpoint(self, admin_client):
        r = await admin_client.get(
            '/api/email-accounts/discover-smtp',
            params={'email': 'user@gmail.com'},
        )
        assert r.status_code == 200
        data = r.json()
        assert data['smtp_host'] == 'smtp.gmail.com'
        assert data['source'] == 'known'

    async def test_response_includes_smtp_fields(self, admin_client):
        r = await admin_client.post(
            '/api/email-accounts',
            json={
                'email': 'resp@qq.com',
                'password': 'pw',
            },
        )
        data = r.json()
        assert 'smtp_host' in data
        assert 'smtp_port' in data
        assert 'smtp_use_tls' in data


class TestStoreEmailLinkLifecycle:
    async def test_link_and_unlink(self, admin_client):
        # Create store
        sr = await admin_client.post(
            '/api/stores', json={'name': 'Email Store'}
        )
        store_id = sr.json()['id']

        # Create email account
        er = await admin_client.post(
            '/api/email-accounts',
            json={
                'email': 'link@gmail.com',
                'password': 'pw',
            },
        )
        acct_id = er.json()['id']

        # Link
        lr = await admin_client.post(
            f'/api/stores/{store_id}/emails',
            json={'email_account_id': acct_id},
        )
        assert lr.status_code == 200
        link_id = lr.json()['id']
        assert lr.json()['email'] == 'link@gmail.com'

        # List
        lsr = await admin_client.get(f'/api/stores/{store_id}/emails')
        assert len(lsr.json()) == 1

        # Unlink
        dr = await admin_client.delete(
            f'/api/stores/{store_id}/emails/{link_id}'
        )
        assert dr.json()['ok'] is True

        # Verify gone
        lsr2 = await admin_client.get(f'/api/stores/{store_id}/emails')
        assert len(lsr2.json()) == 0


class TestEmailInfoByStore:
    async def test_returns_db_paths(self, admin_client):
        # Setup store + account + link
        sr = await admin_client.post('/api/stores', json={'name': 'Info Store'})
        store_id = sr.json()['id']

        er = await admin_client.post(
            '/api/email-accounts',
            json={
                'email': 'info@gmail.com',
                'password': 'pw',
            },
        )
        acct_id = er.json()['id']

        await admin_client.post(
            f'/api/stores/{store_id}/emails',
            json={'email_account_id': acct_id},
        )

        # Get info
        r = await admin_client.get(
            f'/api/email-accounts/info-by-store/{store_id}'
        )
        assert r.status_code == 200
        data = r.json()
        assert data['store_id'] == store_id
        assert len(data['accounts']) == 1
        assert data['accounts'][0]['email'] == 'info@gmail.com'
        assert 'db_path' in data['accounts'][0]
        assert 'schema' in data
        assert 'sample_queries' in data

    async def test_empty_store(self, admin_client):
        sr = await admin_client.post(
            '/api/stores', json={'name': 'Empty Store'}
        )
        store_id = sr.json()['id']

        r = await admin_client.get(
            f'/api/email-accounts/info-by-store/{store_id}'
        )
        assert r.status_code == 200
        assert len(r.json()['accounts']) == 0


class TestSendEndpoint:
    async def test_send_404_bad_account(self, admin_client):
        r = await admin_client.post(
            '/api/email-accounts/nonexistent/send',
            json={
                'to': 'x@x.com',
                'subject': 'Hi',
                'body': 'Hello',
            },
        )
        assert r.status_code == 404

    async def test_send_no_smtp(self, admin_client):
        # Create account without SMTP
        er = await admin_client.post(
            '/api/email-accounts',
            json={
                'email': 'nosmtp@unknown-domain-xyz.com',
                'password': 'pw',
                'imap_host': 'imap.unknown.com',
            },
        )
        acct_id = er.json()['id']

        # Clear SMTP via update
        await admin_client.put(
            f'/api/email-accounts/{acct_id}',
            json={'smtp_host': ''},
        )

        # The send should fail with 502
        # (smtp_host is empty string which is falsy)
        r = await admin_client.post(
            f'/api/email-accounts/{acct_id}/send',
            json={
                'to': 'x@x.com',
                'subject': 'Hi',
                'body': 'Hello',
            },
        )
        assert r.status_code == 502


class TestSyncNow:
    async def test_account_not_found(self, admin_client):
        r = await admin_client.post(
            '/api/email-accounts/sync-now',
            json={'account_email': 'nobody@example.com'},
        )
        assert r.status_code == 404

    async def test_happy_path(self, admin_client):
        # Create account
        er = await admin_client.post(
            '/api/email-accounts',
            json={
                'email': 'sync@gmail.com',
                'password': 'pw',
            },
        )
        assert er.status_code == 200

        # Mock sync to avoid real IMAP
        with (
            patch(
                'app.routers.email_accounts.sync_account_emails',
                new_callable=AsyncMock,
                return_value=3,
            ),
            patch(
                'app.routers.email_accounts.maybe_seed_sync_state',
                new_callable=AsyncMock,
            ),
            patch(
                'app.routers.email_accounts.get_sync_state',
                return_value={
                    'last_polled_at': '2020-01-01T00:00:00+00:00',
                },
            ),
        ):
            r = await admin_client.post(
                '/api/email-accounts/sync-now',
                json={'account_email': 'sync@gmail.com'},
            )

        assert r.status_code == 200
        data = r.json()
        assert data['ok'] is True
        assert data['account_email'] == 'sync@gmail.com'
        assert data['new_emails'] == 3
        assert 'last_polled_at' in data

    async def test_imap_failure(self, admin_client):
        # Create account
        er = await admin_client.post(
            '/api/email-accounts',
            json={
                'email': 'fail@gmail.com',
                'password': 'pw',
            },
        )
        assert er.status_code == 200

        # Mock sync to raise
        with (
            patch(
                'app.routers.email_accounts.sync_account_emails',
                new_callable=AsyncMock,
                side_effect=Exception('IMAP connection refused'),
            ),
            patch(
                'app.routers.email_accounts.maybe_seed_sync_state',
                new_callable=AsyncMock,
            ),
            patch(
                'app.routers.email_accounts.get_sync_state',
                return_value={},
            ),
        ):
            r = await admin_client.post(
                '/api/email-accounts/sync-now',
                json={'account_email': 'fail@gmail.com'},
            )

        assert r.status_code == 502
        assert 'IMAP connection refused' in r.json()['detail']
