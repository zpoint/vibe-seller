from pydantic import BaseModel, ConfigDict


class TaskCreate(BaseModel):
    store_id: str | None = None
    parent_task_id: str | None = None
    title: str
    description: str | None = None
    platform: str | None = None
    country: str | None = None
    plan_mode: bool | None = None
    skip_reflection: bool | None = None
    profile_id: str | None = None
    schedule_id: str | None = None
    # When true, create the task PENDING but do NOT auto-start it — the
    # client uploads attachments into the workspace first, then POSTs
    # /start, so the files are present before the agent reads its prompt.
    defer_start: bool = False


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    parent_task_id: str | None = None
    store_id: str | None
    schedule_id: str | None = None
    title: str
    description: str | None
    platform: str | None
    country: str | None
    status: str
    plan: str | None
    plan_history: str | None = None
    result: str | None
    todos: str | None
    error: str | None
    error_category: str | None = None
    wait_condition: str | None = None
    batch_id: str | None = None
    plan_mode: bool
    # True for creation-time planning tasks owned by a Schedule. The
    # frontend uses this to show the plan-approve button on a Task
    # that has a schedule_id set (normally auto-approved fires).
    is_plan_only: bool = False
    ai_profile_id: str | None
    session_id: str | None = None
    started_at: str | None
    completed_at: str | None
    created_by_name: str | None = None
    created_at: str
    updated_at: str


class TaskStepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    step_index: int
    name: str
    action_type: str
    status: str
    screenshot_id: str | None
    error: str | None
    started_at: str | None
    completed_at: str | None
