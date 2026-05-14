from pydantic import BaseModel, ConfigDict


class ZiniaoAccountCreate(BaseModel):
    name: str
    company: str
    username: str
    password: str
    socket_port: int = 16851
    client_path: str | None = 'ziniao'


class ZiniaoAccountUpdate(BaseModel):
    name: str | None = None
    company: str | None = None
    username: str | None = None
    password: str | None = None
    socket_port: int | None = None
    client_path: str | None = None


class ZiniaoAccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    company: str
    username: str
    socket_port: int
    client_path: str | None
    created_at: str
    updated_at: str


class ZiniaoBrowserProfile(BaseModel):
    browser_name: str
    browser_oauth: str
