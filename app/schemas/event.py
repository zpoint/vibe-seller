from pydantic import BaseModel, ConfigDict


class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    channel_message_id: str | None
    channel_type: str | None
    store_id: str | None
    title: str
    description: str | None
    event_date: str | None
    deadline: str | None
    platform: str | None
    source_text: str | None
    status: str
    sync_backend: str | None
    sync_id: str | None
    sync_error: str | None
    case_id: str | None
    assignees: str | None
    created_by: str | None
    priority: int
    created_at: str
    updated_at: str


class EventCreate(BaseModel):
    title: str
    description: str | None = None
    event_date: str | None = None
    deadline: str | None = None
    platform: str | None = None
    store_id: str | None = None
    case_id: str | None = None
    assignees: str | None = None  # JSON array of user IDs
    priority: int = 0
    sync_backend: str | None = None


class EventUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    event_date: str | None = None
    deadline: str | None = None
    platform: str | None = None
    sync_backend: str | None = None
    case_id: str | None = None
    assignees: str | None = None
    priority: int | None = None
    store_id: str | None = None


class EventStatusChange(BaseModel):
    status: str


class BackendConfigRequest(BaseModel):
    backend: str
    config: dict


class EventActivityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_id: str
    user_id: str | None
    actor_type: str
    action: str
    content: str
    extra_data: str | None
    created_at: str


class EventActivityCreate(BaseModel):
    content: str
    action: str = 'note_added'
