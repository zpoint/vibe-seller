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
    # "Review the plan before it runs". False (default) = auto: every
    # fire runs the prompt. True = a planner writes a plan and the
    # schedule waits for the owner's approval. See app/plan_states.py.
    plan_mode: bool = False
    ai_profile_id: str | None = 'default'
    # 'fanout' | 'single'. Only meaningful when store_id is None;
    # store-bound schedules always resolve to 'single'. When omitted,
    # the server reads the global default from AppSettings.
    phase_mode: str | None = None
    # Optional parent reduce step: after every per-store child of a
    # fanout batch is terminal, ONE no-store task runs this prompt,
    # handed the children's results. See app/scheduler/finalize_reaper.
    finalize_description: str | None = None
    # Run the fanout's stores one at a time (see Schedule.fanout_serial).
    fanout_serial: bool = False


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
    # Switch plan review on/off (see app/plan_states.py): on → a
    # planner starts and the schedule waits for an approved plan;
    # off → the plan is dropped and fires run the prompt.
    plan_mode: bool | None = None
    ai_profile_id: str | None = None
    finalize_description: str | None = None
    fanout_serial: bool | None = None
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
    finalize_description: str | None = None
    fanout_serial: bool = False
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
    proposed_plan: str | None = None
    plan_feedback: str | None = None
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
    # Review schedules: the plan awaiting approval, and the owner's
    # last requested changes.
    proposed_plan: str | None = None
    plan_feedback: str | None = None
    plan_error: str | None = None
    current_planning_task_id: str | None = None
    planning_task_history: list[SchedulePlanTaskSummary] = []
    # Parent finalize/reduce step the plan agent registered (or null).
    finalize_description: str | None = None


class PlanApproveRequest(BaseModel):
    """Body for POST /api/schedules/{id}/plan/approve."""

    model_config = ConfigDict(extra='forbid')

    # The plan_version the reviewer read; 412 if a newer proposal
    # replaced it. Omit to approve whatever is pending.
    plan_version: int | None = None


class PlanChangesRequest(BaseModel):
    """Body for POST /api/schedules/{id}/plan/request-changes."""

    model_config = ConfigDict(extra='forbid')

    feedback: str
    plan_version: int | None = None


class PlanApprovalItem(BaseModel):
    """One schedule whose plan waits for the owner's approval."""

    schedule_id: str
    title: str
    store_id: str | None = None
    plan_version: int
    proposed_at: str


class ScheduleAttentionResponse(BaseModel):
    """Payload for GET /api/schedules/attention (the top banner)."""

    plan_approvals: list[PlanApprovalItem] = []
