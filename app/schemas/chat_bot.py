from pydantic import BaseModel, ConfigDict

from app.text_utils import SafeStr


class ChatBotCreate(BaseModel):
    """Create a chat bot.

    There is deliberately no ``platform`` field: the server derives it
    from the webhook URL host, so a client cannot claim a platform its
    URL does not belong to. An extra ``platform`` in the request body
    is ignored by pydantic.
    """

    name: str
    webhook_url: str
    secret: str | None = None
    #: Security keyword (DingTalk 自定义关键词 / Feishu 关键词). Not a
    #: secret — the UI shows it and the server appends it to any
    #: outgoing message that doesn't already contain it.
    keyword: str | None = None
    #: Which of the platform's security settings the bot uses — a
    #: comma-joined list of names served by /platforms
    #: ('sign', 'keyword', 'ip'). Omit it and the server reads the
    #: settings off the fields; send it and it decides, so switching
    #: off 加签 actually clears the stored secret. Required for a
    #: platform whose registry entry says ``security_required``
    #: (DingTalk) when no field carries one.
    security_mode: str | None = None


class ChatBotUpdate(BaseModel):
    name: str | None = None
    webhook_url: str | None = None
    #: ``""`` clears the stored secret / keyword.
    secret: str | None = None
    keyword: str | None = None
    #: See ``ChatBotCreate.security_mode``.
    security_mode: str | None = None


class ChatBotResponse(BaseModel):
    """Full bot row — includes the raw webhook URL and the secret.

    Returned by create, update, and the single-bot GET (used by
    the edit form). NEVER returned from the list endpoint — the
    URL embeds a secret key.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    webhook_url: str
    platform: str
    secret: str | None = None
    keyword: str | None = None
    #: The settings this bot relies on — the stored column, or the one
    #: the stored fields imply for a row that predates it. Empty for a
    #: platform with no security settings (and for a DingTalk row whose
    #: setting was never recorded).
    security_mode: str | None = None
    has_secret: bool = False
    can_send_file: bool = False
    created_at: str
    updated_at: str


class ChatBotSummary(BaseModel):
    """Safe list-endpoint payload — masks the webhook URL.

    The keyword IS included: it is not a secret (the UI shows it next
    to the row) — only the URL and the signing secret are masked.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    platform: str
    webhook_url_masked: str
    has_secret: bool
    keyword: str | None = None
    can_send_file: bool
    created_at: str
    updated_at: str


class ChatBotTestRequest(BaseModel):
    content: SafeStr | None = None


class ChatBotSendRequest(BaseModel):
    content: SafeStr
    msgtype: str = 'text'


class ChatBotSendFileRequest(BaseModel):
    """Send a local file through the bot.

    `path` is an absolute path on the server host (same host as the
    agent task). The server reads it and uploads to the platform — the
    agent never handles the webhook secret.
    """

    path: str
