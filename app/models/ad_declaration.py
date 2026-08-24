"""What an ad task PHASE is for — declared before the work, not inferred after.

A task's purpose is not fixed for its lifetime. "Create a listing for
widget-006" can be followed, in the same conversation and the same
workspace, by "now audit the ads for the listing we just made". The
second turn is genuinely an audit; the first genuinely was not. Modelled
as one flag per task, that conversation is unrepresentable.

So a declaration belongs to an agent RUN, not to the task. Each run gets
at most one, they accumulate, and the console binds to the run whose
declaration says ``audit`` rather than to the task as a whole.

**Why this exists at all.** Both the completeness gate and the frontend
used to answer "is this an audit?" by pattern-matching the agent's own
prose — a ``## noon AE`` heading plus a ``drilled N/N`` line. Observed
live: a task asked to create two campaigns for one product tripped that
heuristic, was told by the gate it therefore owed all five of the store's
marketplaces, had no data for four of them, and satisfied the gate by
transcribing the previous week's audit file. The console then rendered
51 campaigns of mostly four-day-old numbers as actionable decisions.

The inference ran AFTER the work and WIDENED the obligation. A
declaration runs BEFORE the work and NARROWS it. Same actor deciding —
the agent is the only thing that can read a natural-language request —
but a mis-declaration now shows too little, which someone notices,
instead of silently showing 25x too much with stale data.

**Append-only, and a new one needs a new user turn.** Rows are never
updated. A run cannot re-declare to match what it ended up doing, and an
agent cannot re-declare mid-run to escape a gate: ``user_turn`` records
how many user messages the task had when the declaration was accepted,
and the next declaration must see a higher count. Only the person typing
can open a new phase.
"""

from datetime import UTC, datetime
import uuid

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.text_utils import SafeText

# What an ad task can be. ``edit`` is deliberately absent: "change these
# three bids" is a one-campaign AUDIT, and the scope carries the
# difference. Two kinds that mean almost the same thing drift apart —
# one console path and one obligation rule is the whole point.
KIND_AUDIT = 'audit'
KIND_CREATE = 'create'
KIND_EXECUTE = 'execute'
KIND_INVESTIGATE = 'investigate'

AD_TASK_KINDS = frozenset({
    KIND_AUDIT,
    KIND_CREATE,
    KIND_EXECUTE,
    KIND_INVESTIGATE,
})


class AdTaskDeclaration(Base):
    """One phase's declared purpose and scope. Never updated in place."""

    __tablename__ = 'ad_task_declarations'

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    task_id: Mapped[str] = mapped_column(
        String,
        ForeignKey('tasks.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    # 1-based phase number within the task. The console binds a result
    # item to the declaration that was current when that result landed.
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    # JSON. See app.ai.ad_declaration for the shape and the
    # whole-store rule (absent combos = the whole store).
    scope: Mapped[str] = mapped_column(SafeText, nullable=False, default='{}')
    # How many user messages the task had when this was accepted. The
    # next declaration must see MORE — that is what makes re-declaring
    # something only the user can trigger.
    user_turn: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )
