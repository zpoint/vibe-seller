from pydantic import BaseModel, ConfigDict


class EmailAccountCreate(BaseModel):
    email: str
    password: str
    imap_host: str | None = None
    imap_port: int = 993
    use_ssl: bool = True
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_use_tls: bool = True


class EmailAccountUpdate(BaseModel):
    email: str | None = None
    password: str | None = None
    imap_host: str | None = None
    imap_port: int | None = None
    use_ssl: bool | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_use_tls: bool | None = None


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
