from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api_tokens import DEFAULT_EXPIRY_DAYS


class ApiTokenCreate(BaseModel):
    """Body of ``POST /api/auth/tokens``."""

    name: str = Field(min_length=1, max_length=100)
    # Days until the token expires. ``None`` means "never expires".
    expires_in_days: int | None = Field(
        default=DEFAULT_EXPIRY_DAYS, ge=1, le=3650
    )

    @field_validator('name')
    @classmethod
    def strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError('Name must not be empty')
        return v


class ApiTokenResponse(BaseModel):
    """An API token as shown in the list. Never carries the secret."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    prefix: str
    created_at: str
    last_used_at: str | None
    expires_at: str | None


class ApiTokenCreatedResponse(ApiTokenResponse):
    """The one and only response that carries the plaintext token."""

    token: str
