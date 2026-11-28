"""Generic machine-scope vendor credential storage (app_settings).

For a ``CARDINALITY_SINGLE`` vendor (one login per machine, shared by all
of that vendor's stores): the login's fields live in ``app_settings``
under ``<vendor>_<field>`` — chosen to match the keys the first migrated
vendor's per-vendor code already used, so a machine that saved
credentials before the registry keeps working with no re-entry. Secret
fields are Fernet-encrypted at rest and never returned by any route; the
decrypted value is only ever placed into the in-memory browser config
for the client's login, never logged.

The browser manager and the generic enumerate route both inject through
:func:`inject_vendor_config` so the keys + crypto live in one place.
"""

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.browser.vendors import BrowserVendorSpec, VendorCredentialField
from app.loop_lock import LoopLock
from app.models.app_settings import AppSettings
from app.utils.crypto import decrypt_password, encrypt_password

# One lock per vendor, not one global account lock: unrelated vendors can
# create/delete stores and replace logins independently. LoopLock keeps the
# per-vendor lock usable by each test's event loop as well as the server loop.
_ACCOUNT_LOCKS: dict[str, LoopLock] = {}


def vendor_account_lock(key: str) -> LoopLock:
    """The shared create/delete/credential-save lock for one vendor."""
    return _ACCOUNT_LOCKS.setdefault(key, LoopLock())


def _setting_key(spec: BrowserVendorSpec, field: VendorCredentialField) -> str:
    return f'{spec.key}_{field.key}'


async def _get(db: AsyncSession, key: str) -> str:
    row = await db.get(AppSettings, key)
    return row.value if row and row.value else ''


async def _set(db: AsyncSession, key: str, value: str) -> None:
    row = await db.get(AppSettings, key)
    if row:
        row.value = value
    else:
        db.add(AppSettings(key=key, value=value))


async def credential_snapshot(
    db: AsyncSession, spec: BrowserVendorSpec
) -> dict[str, str]:
    """Opaque stored credential values for create-time revalidation.

    Secrets stay encrypted and never leave this internal comparison. The
    client install path is intentionally not part of the account identity.
    """
    out: dict[str, str] = {}
    for f in spec.credential_fields:
        value = await _get(db, _setting_key(spec, f))
        if value:
            out[f.key] = value
    return out


async def clear_credentials(db: AsyncSession, spec: BrowserVendorSpec) -> None:
    """Delete every stored credential field without committing.

    The store-deletion route calls this in the same transaction as the
    last store's delete. Other settings (for example a client install
    path) are not account credentials and deliberately remain.
    """
    keys = [_setting_key(spec, f) for f in spec.credential_fields]
    if keys:
        await db.execute(delete(AppSettings).where(AppSettings.key.in_(keys)))


async def save_credentials(
    db: AsyncSession, spec: BrowserVendorSpec, values: dict[str, str]
) -> None:
    """Persist the machine login's fields (secrets encrypted)."""
    for f in spec.credential_fields:
        raw = values.get(f.key) or ''
        stored = encrypt_password(raw) if f.secret else raw.strip()
        await _set(db, _setting_key(spec, f), stored)
    await db.commit()


async def credentials_status(db: AsyncSession, spec: BrowserVendorSpec) -> dict:
    """Whether the machine login is configured + the non-secret fields.

    Secret fields are never included. The account row can show these
    non-secret values; the add-account form always starts empty.
    """
    out: dict = {'configured': True}
    for f in spec.credential_fields:
        value = await _get(db, _setting_key(spec, f))
        if f.required and not value:
            out['configured'] = False
        if not f.secret:
            out[f.key] = value
    return out


async def inject_client_path(
    cfg: dict, db: AsyncSession, spec: BrowserVendorSpec
) -> None:
    """The spec's optional install-path override (``client_path``).

    Split out for credential-less vendors (R8): they store nothing, but
    a spec that declares a path override still gets it injected.
    """
    if spec.client_path_setting:
        path = await _get(db, spec.client_path_setting)
        if path:
            cfg['client_path'] = path


async def inject_vendor_config(
    cfg: dict, db: AsyncSession, spec: BrowserVendorSpec
) -> None:
    """Add the client path + decrypted machine creds to a browser config.

    No-op for any field that isn't configured; the client treats missing
    creds as "no auto-login" (and raises LoginRequired when logged out).
    Each field lands under ``<vendor>_<field>``; the optional install-path
    override (``client_path_setting``) lands under ``client_path``.
    """
    await inject_client_path(cfg, db, spec)
    for f in spec.credential_fields:
        value = await _get(db, _setting_key(spec, f))
        if not value:
            continue
        cfg[f'{spec.key}_{f.key}'] = (
            decrypt_password(value) if f.secret else value
        )
