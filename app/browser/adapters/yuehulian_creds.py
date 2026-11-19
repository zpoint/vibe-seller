"""Yuehulian team-login credential storage (one login per machine).

Yuehulian is a single GUI login shared by all of this machine's yuehulian
stores, so its team credentials live in ``app_settings`` (not per store).
The password is stored encrypted; the decrypted value is only ever placed
into the in-memory browser config for the login eval, never returned by the
API or logged. Both the browser manager and the enumerate router use
:func:`inject_yuehulian_config` so the keys + crypto live in one place.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_settings import AppSettings
from app.utils.crypto import decrypt_password, encrypt_password

TEAM_KEY = 'yuehulian_team_name'
ACCT_KEY = 'yuehulian_subaccount'
PW_KEY = 'yuehulian_password'
PATH_KEY = 'yuehulian_client_path'


async def _get(db: AsyncSession, key: str) -> str:
    row = await db.get(AppSettings, key)
    return row.value if row and row.value else ''


async def _set(db: AsyncSession, key: str, value: str) -> None:
    row = await db.get(AppSettings, key)
    if row:
        row.value = value
    else:
        db.add(AppSettings(key=key, value=value))


async def inject_yuehulian_config(cfg: dict, db: AsyncSession) -> None:
    """Add the client path + decrypted team creds to a browser config.

    No-op for any field that isn't configured; the client treats missing
    creds as "no auto-login" (and raises LoginRequired when logged out).
    """
    path = await _get(db, PATH_KEY)
    if path:
        cfg['client_path'] = path
    team = await _get(db, TEAM_KEY)
    acct = await _get(db, ACCT_KEY)
    pw = await _get(db, PW_KEY)
    if team and acct and pw:
        cfg['yuehulian_team'] = team
        cfg['yuehulian_account'] = acct
        cfg['yuehulian_password'] = decrypt_password(pw)


async def save_credentials(
    db: AsyncSession, team: str, account: str, password: str
) -> None:
    """Persist the machine's team-login credentials (password encrypted)."""
    await _set(db, TEAM_KEY, team.strip())
    await _set(db, ACCT_KEY, account.strip())
    await _set(db, PW_KEY, encrypt_password(password))
    await db.commit()


async def clear_credentials(db: AsyncSession) -> None:
    """Remove the machine's team-login credentials (the [×] delete button).

    Leaves any bound yuehulian stores in place; they simply report
    ``login_required`` until a login is configured again.
    """
    for key in (TEAM_KEY, ACCT_KEY, PW_KEY):
        row = await db.get(AppSettings, key)
        if row:
            await db.delete(row)
    await db.commit()


async def credentials_status(db: AsyncSession) -> dict:
    """Whether team-login is configured + the non-secret fields (never the
    password)."""
    team = await _get(db, TEAM_KEY)
    acct = await _get(db, ACCT_KEY)
    pw = await _get(db, PW_KEY)
    return {
        'configured': bool(team and acct and pw),
        'team_name': team,
        'subaccount': acct,
    }
