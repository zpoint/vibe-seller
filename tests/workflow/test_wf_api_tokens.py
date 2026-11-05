"""Workflow tests for personal API tokens — CRUD and bearer auth.

Two contracts are pinned here:

1. A user manages their OWN tokens. Another user's token id is
   "not found" (404), never "forbidden" (403) — a 403 would confirm
   that the id exists.
2. A ``vst_`` bearer is validated against the token table and
   NOTHING else. A revoked / expired / unknown token is a 401 even
   when auth is disabled, because silently downgrading it to the
   default admin would tell the owner their access was cut when it
   was not.
"""

from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.api_tokens import hash_token
from app.auth import create_token
from app.config import DEFAULT_USER_ID
from app.main import app
from app.models.api_token import ApiToken
from app.models.app_settings import AppSettings
from app.models.user import User
from app.password import hash_password

pytestmark = pytest.mark.workflow

# Distinguishes "field omitted" from "field explicitly null" when
# building a create request.
_UNSET = object()


@pytest_asyncio.fixture
async def ai_bot_client(
    override_async_session,
    install_fake_agent,
    mock_browser_wf,
    mock_knowledge_sync,
    mock_skills_sync,
    mock_workspace,
    isolated_profiles,
    fast_polling,
):
    """A client authenticated as the ``ai_bot`` service user."""
    async with override_async_session() as db:
        bot = User(
            username='ai_bot',
            email='ai@test.local',
            password_hash='disabled',
            role='ai_bot',
            is_active=True,
        )
        db.add(bot)
        await db.commit()
        await db.refresh(bot)
        bot_id = bot.id

    token = create_token(bot_id, 'ai_bot')
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url='http://test'
    ) as client:
        client.cookies.set('auth_token', token)
        yield client


async def _auth_off(override_async_session) -> None:
    """Turn auth off and make sure the default admin exists.

    The default admin is what an unauthenticated request falls back to
    when auth is off — so it has to exist for the "a vst_ token is
    evaluated, not ignored" assertions to mean anything.
    """
    async with override_async_session() as db:
        await db.merge(AppSettings(key='auth_required', value='false'))
        db.add(
            User(
                id=DEFAULT_USER_ID,
                username='default-admin',
                email='default-admin@test.com',
                password_hash=hash_password('admin'),
                role='admin',
                is_active=True,
            )
        )
        await db.commit()


async def _mint(client, name='laptop', expires_in_days=_UNSET) -> dict:
    """Create a token through the real API and return the response."""
    body = {'name': name}
    if expires_in_days is not _UNSET:
        body['expires_in_days'] = expires_in_days
    r = await client.post('/api/auth/tokens', json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def _backdate_expiry(override_async_session, token_id: str) -> None:
    """Push a token's expiry into the past."""
    async with override_async_session() as db:
        row = (
            await db.execute(select(ApiToken).where(ApiToken.id == token_id))
        ).scalar_one()
        row.expires_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        await db.commit()


class TestTokenCrud:
    async def test_create_returns_the_plaintext_once(
        self, admin_client, admin_user
    ):
        data = await _mint(admin_client, 'ci-laptop')
        assert data['token'].startswith('vst_')
        assert data['name'] == 'ci-laptop'
        assert data['prefix'] == data['token'][:12]
        assert data['id']
        assert data['created_at']
        assert data['last_used_at'] is None

    async def test_created_token_defaults_to_90_days(self, admin_client):
        data = await _mint(admin_client)
        expiry = datetime.fromisoformat(data['expires_at'])
        delta = expiry - datetime.now(UTC)
        assert 89 * 24 * 3600 < delta.total_seconds() <= 90 * 24 * 3600

    async def test_expires_in_days_null_never_expires(self, admin_client):
        data = await _mint(admin_client, expires_in_days=None)
        assert data['expires_at'] is None

    async def test_expires_in_days_one_is_one_day_out(self, admin_client):
        data = await _mint(admin_client, expires_in_days=1)
        expiry = datetime.fromisoformat(data['expires_at'])
        delta = expiry - datetime.now(UTC)
        assert 23 * 3600 < delta.total_seconds() <= 24 * 3600

    async def test_list_never_leaks_the_secret(self, admin_client):
        data = await _mint(admin_client, 'ci-laptop')
        r = await admin_client.get('/api/auth/tokens')
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        entry = body[0]
        assert entry['id'] == data['id']
        assert 'token' not in entry
        assert data['token'] not in r.text

    async def test_revoke_removes_the_token_from_the_list(self, admin_client):
        data = await _mint(admin_client)
        r = await admin_client.delete(f'/api/auth/tokens/{data["id"]}')
        assert r.status_code == 200
        r = await admin_client.get('/api/auth/tokens')
        assert r.json() == []

    async def test_revoke_unknown_id_is_404(self, admin_client):
        r = await admin_client.delete('/api/auth/tokens/does-not-exist')
        assert r.status_code == 404

    async def test_revoke_twice_is_404(self, admin_client):
        data = await _mint(admin_client)
        r = await admin_client.delete(f'/api/auth/tokens/{data["id"]}')
        assert r.status_code == 200
        r = await admin_client.delete(f'/api/auth/tokens/{data["id"]}')
        assert r.status_code == 404

    async def test_other_users_token_id_is_404(
        self, admin_client, member_client
    ):
        """A token id belonging to somebody else must be 'not found'."""
        member_token = await _mint(member_client, 'member-token')

        r = await admin_client.delete(f'/api/auth/tokens/{member_token["id"]}')
        assert r.status_code == 404

        # ...and the member's token is untouched.
        r = await member_client.get('/api/auth/tokens')
        assert [t['id'] for t in r.json()] == [member_token['id']]

    async def test_list_shows_only_your_own_tokens(
        self, admin_client, member_client
    ):
        await _mint(admin_client, 'admin-token')
        member_token = await _mint(member_client, 'member-token')

        r = await member_client.get('/api/auth/tokens')
        names = [t['name'] for t in r.json()]
        assert names == ['member-token']
        assert member_token['id'] in [t['id'] for t in r.json()]

    async def test_ai_bot_cannot_mint(self, ai_bot_client):
        r = await ai_bot_client.post(
            '/api/auth/tokens', json={'name': 'bot-token'}
        )
        assert r.status_code == 403

    async def test_the_database_keeps_only_the_hash(
        self, admin_client, override_async_session
    ):
        data = await _mint(admin_client, 'ci-laptop')
        async with override_async_session() as db:
            row = (
                await db.execute(
                    select(ApiToken).where(ApiToken.id == data['id'])
                )
            ).scalar_one()
            assert row.token_hash == hash_token(data['token'])
            assert row.token_hash != data['token']
            # The plaintext exists nowhere on the row.
            for column in row.__table__.columns:
                value = getattr(row, column.key, None)
                assert data['token'] not in str(value)

    async def test_deleting_a_user_removes_their_tokens(
        self, admin_client, member_client, member_user, override_async_session
    ):
        """A deleted user's credentials die with them.

        The token could not authenticate any more (the owner is gone),
        but the row would linger with nobody able to revoke it.
        """
        data = await _mint(member_client, 'member-token')
        r = await admin_client.delete(f'/api/users/{member_user.id}')
        assert r.status_code == 200

        async with override_async_session() as db:
            rows = (
                (
                    await db.execute(
                        select(ApiToken).where(ApiToken.id == data['id'])
                    )
                )
                .scalars()
                .all()
            )
            assert rows == []


class TestBearerAuth:
    async def test_vst_bearer_authenticates_as_its_owner(
        self, admin_client, admin_user, unauthed_client
    ):
        data = await _mint(admin_client, 'ci-laptop')
        r = await unauthed_client.get(
            '/api/auth/me',
            headers={'Authorization': f'Bearer {data["token"]}'},
        )
        assert r.status_code == 200
        assert r.json()['id'] == admin_user.id

    async def test_unknown_vst_token_is_401(self, unauthed_client):
        r = await unauthed_client.get(
            '/api/auth/me',
            headers={'Authorization': 'Bearer vst_not-a-real-token'},
        )
        assert r.status_code == 401

    async def test_revoked_token_is_401(self, admin_client, unauthed_client):
        data = await _mint(admin_client, 'ci-laptop')
        await admin_client.delete(f'/api/auth/tokens/{data["id"]}')
        r = await unauthed_client.get(
            '/api/auth/me',
            headers={'Authorization': f'Bearer {data["token"]}'},
        )
        assert r.status_code == 401

    async def test_expired_token_is_401(
        self, admin_client, unauthed_client, override_async_session
    ):
        data = await _mint(admin_client, 'ci-laptop', expires_in_days=1)
        await _backdate_expiry(override_async_session, data['id'])
        r = await unauthed_client.get(
            '/api/auth/me',
            headers={'Authorization': f'Bearer {data["token"]}'},
        )
        assert r.status_code == 401

    async def test_inactive_owner_is_401(
        self, admin_client, unauthed_client, override_async_session
    ):
        """Deactivating the owner cuts the token's access too."""
        data = await _mint(admin_client, 'ci-laptop')
        async with override_async_session() as db:
            row = (
                await db.execute(
                    select(ApiToken).where(ApiToken.id == data['id'])
                )
            ).scalar_one()
            owner = await db.get(User, row.user_id)
            owner.is_active = False
            await db.commit()

        r = await unauthed_client.get(
            '/api/auth/me',
            headers={'Authorization': f'Bearer {data["token"]}'},
        )
        assert r.status_code == 401

    async def test_last_used_at_is_stamped(
        self, admin_client, unauthed_client, override_async_session
    ):
        data = await _mint(admin_client, 'ci-laptop')
        r = await unauthed_client.get(
            '/api/auth/me',
            headers={'Authorization': f'Bearer {data["token"]}'},
        )
        assert r.status_code == 200
        async with override_async_session() as db:
            row = (
                await db.execute(
                    select(ApiToken).where(ApiToken.id == data['id'])
                )
            ).scalar_one()
            assert row.last_used_at is not None

        r = await admin_client.get('/api/auth/tokens')
        entry = r.json()[0]
        assert entry['last_used_at'] is not None


class TestBearerAuthWhenAuthIsOff:
    """Auth off does NOT turn a broken token into the default admin."""

    async def test_valid_token_still_authenticates_as_its_owner(
        self, admin_client, unauthed_client, override_async_session
    ):
        await _auth_off(override_async_session)
        data = await _mint(admin_client, 'ci-laptop')

        r = await unauthed_client.get(
            '/api/auth/me',
            headers={'Authorization': f'Bearer {data["token"]}'},
        )
        assert r.status_code == 200
        # The token's owner — NOT the default admin the no-auth path
        # would otherwise hand out.
        assert r.json()['id'] != DEFAULT_USER_ID

    async def test_revoked_token_is_401_even_with_auth_off(
        self, admin_client, unauthed_client, override_async_session
    ):
        await _auth_off(override_async_session)
        data = await _mint(admin_client, 'ci-laptop')
        await admin_client.delete(f'/api/auth/tokens/{data["id"]}')

        r = await unauthed_client.get(
            '/api/auth/me',
            headers={'Authorization': f'Bearer {data["token"]}'},
        )
        assert r.status_code == 401

    async def test_expired_token_is_401_even_with_auth_off(
        self, admin_client, unauthed_client, override_async_session
    ):
        await _auth_off(override_async_session)
        data = await _mint(admin_client, 'ci-laptop', expires_in_days=1)
        await _backdate_expiry(override_async_session, data['id'])

        r = await unauthed_client.get(
            '/api/auth/me',
            headers={'Authorization': f'Bearer {data["token"]}'},
        )
        assert r.status_code == 401

    async def test_unknown_vst_token_is_401_even_with_auth_off(
        self, unauthed_client, override_async_session
    ):
        await _auth_off(override_async_session)
        r = await unauthed_client.get(
            '/api/auth/me',
            headers={'Authorization': 'Bearer vst_nope'},
        )
        assert r.status_code == 401

    async def test_non_vst_bearer_still_falls_back_to_default_admin(
        self, unauthed_client, override_async_session
    ):
        """The JWT path is untouched: auth off + junk bearer → admin."""
        await _auth_off(override_async_session)
        r = await unauthed_client.get(
            '/api/auth/me',
            headers={'Authorization': 'Bearer stale.invalid.jwt'},
        )
        assert r.status_code == 200
        assert r.json()['id'] == DEFAULT_USER_ID


class TestMintRestriction:
    """A ``vst_`` token cannot mint API tokens.

    A leaked token that could mint its own replacement would make
    "revoke the leaked token" meaningless — the attacker mints a
    never-expiring successor first. Listing and revoking stay open:
    revoking is exactly what the owner of a leaked token needs to do.
    """

    async def test_token_cannot_mint_a_token(
        self, admin_client, unauthed_client
    ):
        minted = await _mint(admin_client, 'leaked')
        auth = {'Authorization': f'Bearer {minted["token"]}'}

        r = await unauthed_client.post(
            '/api/auth/tokens', json={'name': 'successor'}, headers=auth
        )
        assert r.status_code == 403, r.text
        assert 'mint' in r.json()['detail'].lower()

        # Nothing was created: the owner's token list is unchanged.
        listed = await unauthed_client.get('/api/auth/tokens', headers=auth)
        assert listed.status_code == 200
        names = [row['name'] for row in listed.json()]
        assert names == ['leaked']

    async def test_token_cannot_refresh_into_a_session(
        self, admin_client, unauthed_client
    ):
        """A token cannot trade itself for a session cookie either.

        ``/api/auth/refresh`` hands out a fresh session JWT. Taken with
        a ``vst_`` bearer, that JWT would keep working without the
        header — so it could mint tokens, and it would outlive the
        token being revoked.
        """
        minted = await _mint(admin_client, 'leaked')
        auth = {'Authorization': f'Bearer {minted["token"]}'}

        r = await unauthed_client.post('/api/auth/refresh', headers=auth)
        assert r.status_code == 403, r.text
        assert 'set-cookie' not in r.headers

    async def test_session_can_refresh(self, admin_client):
        r = await admin_client.post('/api/auth/refresh')
        assert r.status_code == 200, r.text

    async def test_session_can_mint(self, admin_client):
        r = await admin_client.post(
            '/api/auth/tokens', json={'name': 'from-the-ui'}
        )
        assert r.status_code == 200, r.text
        assert r.json()['token'].startswith('vst_')

    async def test_token_still_lists_and_revokes_itself(
        self, admin_client, unauthed_client
    ):
        minted = await _mint(admin_client, 'self-cleanup')
        auth = {'Authorization': f'Bearer {minted["token"]}'}

        listed = await unauthed_client.get('/api/auth/tokens', headers=auth)
        assert listed.status_code == 200

        revoked = await unauthed_client.delete(
            f'/api/auth/tokens/{minted["id"]}', headers=auth
        )
        assert revoked.status_code == 200

    async def test_service_bearer_jwt_can_mint(
        self, unauthed_client, admin_user
    ):
        """Only the ``api_token`` credential is blocked: a plain JWT in
        the Authorization header (the wrapper/service classification)
        still mints, as before."""
        jwt = create_token(admin_user.id, 'admin')
        r = await unauthed_client.post(
            '/api/auth/tokens',
            json={'name': 'service-minted'},
            headers={'Authorization': f'Bearer {jwt}'},
        )
        assert r.status_code == 200, r.text

    async def test_no_auth_server_can_mint(
        self, unauthed_client, override_async_session
    ):
        """Auth off + no credential → default admin, and minting from
        such a server stays allowed (there is nothing to escalate to).
        """
        await _auth_off(override_async_session)
        r = await unauthed_client.post(
            '/api/auth/tokens', json={'name': 'no-auth-mint'}
        )
        assert r.status_code == 200, r.text
