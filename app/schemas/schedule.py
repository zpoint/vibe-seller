from pydantic import BaseModel, ConfigDict


class ScheduleCreate(BaseModel):
    title: str
    description: str | None = None
    store_id: str | None = None
    schedule_type: str  # 'minutes'|'hours'|'days'|'weekly'|'monthly'
    schedule_time: str = '09:00'  # HH:MM (ignored for minutes/hours)
    schedule_day: int | None = None
    interval_value: int = 1  # every N units
    # None → server-resolved default (AppSettings['default_schedule_timezone']
    # or tzlocal). Router normalizes to a concrete IANA name before insert.
    timezone: str | None = None
    # User-created schedules are always plan-mode — the plan-at-creation
    # lifecycle (app/plan_states.py) is the single UX for them. The field
    # is kept for API back-compat but the router force-overrides it below.
    plan_mode: bool = True
    ai_profile_id: str | None = 'default'
    # 'fanout' | 'single'. Only meaningful when store_id is None;
    # store-bound schedules always resolve to 'single'. When omitted,
    # the server reads the global default from AppSettings.
    phase_mode: str | None = None


class ScheduleUpdate(BaseModel):
    # Reject unknown fields so a client attempt to mutate
    # phase_mode/store_id surfaces as a 422 instead of a silent drop.
    model_config = ConfigDict(extra='forbid')

    title: str | None = None
    description: str | None = None
    schedule_type: str | None = None
    schedule_time: str | None = None
    schedule_day: int | None = None
    interval_value: int | None = None
    timezone: str | None = None
    # Kept in the schema for back-compat with older clients, but
    # the router rejects non-null values for plan_mode because the
    # plan lifecycle (app/plan_states.py) would be bypassed by a
    # toggle-off.
    plan_mode: bool | None = None
    ai_profile_id: str | None = None
    # Optimistic lock: clients pass the plan_version they last saw.
    # Router returns 412 if the server's version is higher.
    plan_version: int | None = None


class ScheduleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    store_id: str | None
    title: str
    description: str | None
    platform: str | None
    country: str | None
    plan: str | None
    schedule_type: str
    schedule_time: str
    schedule_day: int | None
    interval_value: int
    timezone: str
    is_active: bool
    is_system: bool = False
    phase_mode: str = 'fanout'
    staleness_check: str | None = None
    skip_reflection: bool = False
    plan_mode: bool
    ai_profile_id: str | None
    created_by: str
    created_at: str
    updated_at: str
    # Plan lifecycle (see app/plan_states.py).
    plan_status: str = 'none'
    plan_version: int = 0
    plan_error: str | None = None
    current_planning_task_id: str | None = None
    # Computed fields (filled in by router, not from ORM directly)
    next_run: str | None = None
    child_task_count: int = 0
    last_run_status: str | None = None
    pending_questions_count: int = 0


class SchedulePlanTaskSummary(BaseModel):
    """One row in the planning-task history list."""

    id: str
    status: str
    created_at: str
    completed_at: str | None = None
    error: str | None = None


class SchedulePlanResponse(BaseModel):
    """Payload for GET /api/schedules/{id}/plan.

    Consolidates the four plan-lifecycle fields from ``Schedule`` with
    a short history of prior ``is_plan_only`` Tasks so the frontend's
    plan panel can render every state without a second query.
    """

    plan_status: str
    plan_version: int
    plan_text: str | None = None
    plan_error: str | None = None
    current_planning_task_id: str | None = None
    planning_task_history: list[SchedulePlanTaskSummary] = []
