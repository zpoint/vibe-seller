import re

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator


def _validate_username(v: str) -> str:
    v = v.strip().lower()
    if len(v) < 3 or len(v) > 50:
        msg = 'Username must be 3-50 characters'
        raise ValueError(msg)
    if '@' in v:
        msg = 'Username must not contain @'
        raise ValueError(msg)
    if not re.fullmatch(r'[a-z0-9_-]+', v):
        msg = 'Username: only a-z, 0-9, _, -'
        raise ValueError(msg)
    return v


class LoginRequest(BaseModel):
    identifier: str
    password: str


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    email: str | None
    role: str
    is_active: bool
    avatar_url: str | None
    plan_mode_default: bool
    debug_mode: bool
    default_profile_id: str
    created_at: str


class UserCreate(BaseModel):
    username: str
    email: EmailStr | None = None
    password: str
    role: str = 'member'

    @field_validator('username')
    @classmethod
    def check_username(cls, v: str) -> str:
        return _validate_username(v)


class UserUpdate(BaseModel):
    username: str | None = None
    email: EmailStr | None = None
    password: str | None = None
    role: str | None = None
    is_active: bool | None = None

    @field_validator('username')
    @classmethod
    def check_username(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_username(v)


class PasswordChange(BaseModel):
    current_password: str | None = None
    new_password: str


class ProfileUpdate(BaseModel):
    username: str | None = None
    email: EmailStr | None = None
    plan_mode_default: bool | None = None

    @field_validator('username')
    @classmethod
    def check_username(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_username(v)


class TaskModeToggle(BaseModel):
    plan_mode: bool | None = None
