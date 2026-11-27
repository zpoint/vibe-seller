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

from sqlalchemy.ext.asyncio import AsyncSession

from app.browser.vendors import BrowserVendorSpec, VendorCredentialField
from app.models.app_settings import AppSettings
from app.utils.crypto import decrypt_password, encrypt_password


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

    Secret fields are never included — the Settings form can show/prefill
    only what this returns.
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
