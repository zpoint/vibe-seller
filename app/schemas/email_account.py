from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

SEND_PERMISSION_ROUTE = 'PUT /api/email-accounts/{id}/send-permission'


class _NoSendPermission(BaseModel):
    """Refuses ``allow_send`` in a general create/update body.

    The send permission has its own route, which only an interactive
    web login may call (``app/interactive_only.py``). Silently ignoring
    the field here would let a caller believe it had been set.
    """

    @model_validator(mode='before')
    @classmethod
    def _refuse_allow_send(cls, data: Any) -> Any:
        if isinstance(data, dict) and 'allow_send' in data:
            raise ValueError(
                'allow_send cannot be set here; the user sets it with '
                f'{SEND_PERMISSION_ROUTE} (Settings → Email in the web UI)'
            )
        return data


class EmailAccountCreate(_NoSendPermission):
    email: str
    password: str
    imap_host: str | None = None
    imap_port: int = 993
    use_ssl: bool = True
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_use_tls: bool = True


class EmailAccountUpdate(_NoSendPermission):
    email: str | None = None
    password: str | None = None
    imap_host: str | None = None
    imap_port: int | None = None
    use_ssl: bool | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_use_tls: bool | None = None


class SendPermissionUpdate(BaseModel):
    """Body of the interactive-only send-permission route."""

    allow_send: bool


class EmailAccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    imap_host: str
    imap_port: int
    use_ssl: bool
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_use_tls: bool = True
    allow_send: bool = True
    created_at: str
    updated_at: str
    # Never expose password


class ImapDiscoverResponse(BaseModel):
    imap_host: str | None
    imap_port: int | None
    source: str  # 'known' | 'heuristic' | 'unknown'


class SmtpDiscoverResponse(BaseModel):
    smtp_host: str | None
    smtp_port: int | None
    smtp_use_starttls: bool | None = None
    source: str  # 'known' | 'heuristic' | 'unknown'


class StoreEmailLinkCreate(BaseModel):
    email_account_id: str


class StoreEmailLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    store_id: str
    email_account_id: str
    email: str
    watermark_date: str | None
    last_polled_at: str | None
