"""Personal API tokens — generation, hashing and validity rules.

The plaintext token exists for exactly one moment: the response that
created it. What the database keeps is its SHA-256 hash, so the table
can be read (or dumped) without yielding a usable credential.

Tokens are prefixed with ``vst_`` so the auth path can tell an API
token from a JWT without a database round-trip: a credential that
starts with ``vst_`` is an API token, full stop, and is always
validated against the token table.
"""

from datetime import UTC, datetime, timedelta
import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_token import ApiToken

#: Marker that identifies a credential as a personal API token.
TOKEN_PREFIX = 'vst_'
#: Random bytes behind the token (base64url-encoded into the plaintext).
TOKEN_ENTROPY_BYTES = 32
#: How many leading characters of the plaintext are kept for display.
DISPLAY_PREFIX_LEN = 12
#: Default lifetime of a new token, in days. ``None`` means "never".
DEFAULT_EXPIRY_DAYS = 90
#: ``last_used_at`` is written at most this often.
LAST_USED_THROTTLE = timedelta(minutes=1)


def generate_token() -> str:
    """Return a fresh plaintext token: ``vst_`` + 32 random bytes."""
    return TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_ENTROPY_BYTES)


def hash_token(token: str) -> str:
    """Return the SHA-256 hex digest of a plaintext token."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def display_prefix(token: str) -> str:
    """Return the leading slice of a token that is safe to store/show."""
    return token[:DISPLAY_PREFIX_LEN]


def expiry_for_days(
    days: int | None, now: datetime | None = None
) -> str | None:
    """Absolute expiry timestamp for a token, or ``None`` = never."""
    if days is None:
        return None
    now = now or datetime.now(UTC)
    return (now + timedelta(days=days)).isoformat()


def _parse_ts(value: str | None) -> datetime | None:
    """Parse one of our ISO timestamps; ``None`` if unusable."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def is_expired(row: ApiToken, now: datetime | None = None) -> bool:
    """Whether a token row is past its expiry.

    A missing or unparseable expiry is treated as "no expiry" only when
    the column is empty; a column that holds garbage fails closed (the
    token does not work), because an unreadable deadline on a
    credential must never widen access.
    """
    if not row.expires_at:
        return False
    expiry = _parse_ts(row.expires_at)
    if expiry is None:
        return True
    return expiry <= (now or datetime.now(UTC))


def is_revoked(row: ApiToken) -> bool:
    """Whether a token row has been revoked."""
    return bool(row.revoked_at)


def should_touch(last_used_at: str | None, now: datetime | None = None) -> bool:
    """Whether ``last_used_at`` should be stamped now.

    Throttled to once a minute: the value only feeds a "last used"
    label, and a write on every request would turn an agent's request
    stream into a write storm.
    """
    now = now or datetime.now(UTC)
    last = _parse_ts(last_used_at)
    if last is None:
        return True
    return (now - last) >= LAST_USED_THROTTLE


async def find_token(db: AsyncSession, plaintext: str) -> ApiToken | None:
    """Look a token up by its plaintext. ``None`` when unknown."""
    result = await db.execute(
        select(ApiToken).where(ApiToken.token_hash == hash_token(plaintext))
    )
    return result.scalar_one_or_none()


async def create_token_row(
    db: AsyncSession,
    user_id: str,
    name: str,
    expires_in_days: int | None = DEFAULT_EXPIRY_DAYS,
) -> tuple[ApiToken, str]:
    """Create a token for ``user_id``.

    Returns the stored row and the plaintext. The plaintext is never
    persisted — the caller returns it to the client once and drops it.
    """
    plaintext = generate_token()
    row = ApiToken(
        user_id=user_id,
        name=name,
        token_hash=hash_token(plaintext),
        prefix=display_prefix(plaintext),
        created_at=datetime.now(UTC).isoformat(),
        last_used_at=None,
        expires_at=expiry_for_days(expires_in_days),
        revoked_at=None,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row, plaintext


async def touch_last_used(db: AsyncSession, row: ApiToken) -> None:
    """Stamp ``last_used_at`` (throttled) and persist it."""
    now = datetime.now(UTC)
    if not should_touch(row.last_used_at, now):
        return
    row.last_used_at = now.isoformat()
    await db.commit()
