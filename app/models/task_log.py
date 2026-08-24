import time
import uuid

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.text_utils import SafeText


class TaskLog(Base):
    __tablename__ = 'task_logs'

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    task_id: Mapped[str] = mapped_column(
        String, ForeignKey('tasks.id'), nullable=False
    )
    log_type: Mapped[str] = mapped_column(
        String, nullable=False, default='info'
    )
    content: Mapped[str] = mapped_column(SafeText, nullable=False)
    timestamp_ms: Mapped[int] = mapped_column(
        Integer, default=lambda: int(time.time() * 1000)
    )
