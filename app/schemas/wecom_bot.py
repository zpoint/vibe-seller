from pydantic import BaseModel, ConfigDict


class WeComBotCreate(BaseModel):
    name: str
    webhook_url: str


class WeComBotUpdate(BaseModel):
    name: str | None = None
    webhook_url: str | None = None


class WeComBotResponse(BaseModel):
    """Full bot row — includes the raw webhook URL.

    Returned by create, update, and the single-bot GET (used by
    the edit form). NEVER returned from the list endpoint — the
    URL embeds a secret key.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    webhook_url: str
    created_at: str
    updated_at: str


class WeComBotSummary(BaseModel):
    """Safe list-endpoint payload — masks the webhook URL."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    webhook_url_masked: str
    created_at: str
    updated_at: str


class WeComBotTestRequest(BaseModel):
    content: str | None = None


class WeComBotSendRequest(BaseModel):
    content: str
    msgtype: str = 'text'


class WeComBotSendFileRequest(BaseModel):
    """Send a local file through the bot.

    `path` is an absolute path on the server host (same host as the
    agent task). The server reads it and uploads to WeCom — the agent
    never handles the webhook secret.
    """

    path: str
