from datetime import UTC, datetime
import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.utils.timezone import get_server_timezone


def _default_timezone() -> str:
    return get_server_timezone()


class Schedule(Base):
    __tablename__ = 'schedules'

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    store_id: Mapped[str | None] = mapped_column(
        String, ForeignKey('stores.id'), nullable=True
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    platform: Mapped[str | None] = mapped_column(String, nullable=True)
    country: Mapped[str | None] = mapped_column(String, nullable=True)
    plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    schedule_type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # 'minutes' | 'hours' | 'days' | 'weekly' | 'monthly'
    schedule_time: Mapped[str] = mapped_column(
        String, nullable=False
    )  # 'HH:MM' or 'HH:MM:SS'  (ignored for minutes/hours)
    schedule_day: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # 0-6 for weekly (Mon=0), 1-31 for monthly
    interval_value: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )  # e.g. every N minutes/hours/days
    # Python-side default resolves to the server's IANA zone at insert
    # time (not hardcoded). Router may still pre-resolve to respect
    # AppSettings['default_schedule_timezone'] when set.
    timezone: Mapped[str] = mapped_column(
        String, nullable=False, default=_default_timezone
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    plan_mode: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    ai_profile_id: Mapped[str | None] = mapped_column(
        String(50), nullable=True, default='default'
    )
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    phase_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default='fanout'
    )  # PhaseMode enum: 'fanout' | 'two_phase'
    staleness_check: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default=None
    )  # StalenessCheck enum or None
    skip_reflection: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    # Plan lifecycle — see app/plan_states.py for the state machine.
    plan_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default='none'
    )
    # Monotonic plan commit counter. Bumped on every successful
    # ExitPlanMode approval. Also serves as the optimistic-lock
    # token for PUT (clients send If-Match: <plan_version>).
    plan_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    # Points to the currently-running plan-only Task (if any).
    current_planning_task_id: Mapped[str | None] = mapped_column(
        String, nullable=True
    )
    plan_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(
        String, ForeignKey('users.id'), nullable=False
    )
    created_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )
    updated_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )
