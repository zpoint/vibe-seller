from pydantic import BaseModel, ConfigDict


class StoreCreate(BaseModel):
    name: str
    browser_backend: str = 'chrome'
    browser_config: dict = {}
    ziniao_account_id: str | None = None
    browser_oauth: str | None = None
    platforms: list[str] = ['amazon']
    countries: list[str] = ['US']
    platform_countries: dict[str, list[str]] = {}


class StoreUpdate(BaseModel):
    name: str | None = None
    browser_config: dict | None = None
    platforms: list[str] | None = None
    countries: list[str] | None = None
    platform_countries: dict[str, list[str]] | None = None


class StoreResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    browser_backend: str
    browser_config: dict
    ziniao_account_id: str | None
    browser_oauth: str | None
    platforms: list[str]
    countries: list[str]
    platform_countries: dict[str, list[str]]
    created_at: str
    updated_at: str
