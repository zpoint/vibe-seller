"""What an ad task PHASE is for — declared before the work, not inferred after.

A task's purpose is not fixed for its lifetime. "Create a listing for
widget-006" can be followed, in the same conversation and the same
workspace, by "now audit the ads for the listing we just made". The
second turn is genuinely an audit; the first genuinely was not. Modelled
as one flag per task, that conversation is unrepresentable.

So a declaration belongs to an agent RUN, not to the task. Each run gets
at most one, they accumulate, and a report is judged against the
declaration its run made rather than against the task as a whole.

**Why this exists at all.** Both the ad report gate and the frontend
used to answer "is this an audit?" by pattern-matching the agent's own
prose — a ``## noon AE`` heading plus a ``drilled N/N`` line. Observed
live: a task asked to create two campaigns for one product tripped that
heuristic, was told by the gate it therefore owed all five of the store's
marketplaces, had no data for four of them, and satisfied the gate by
transcribing the previous week's audit file. What passed was 51
campaigns of mostly four-day-old numbers presented as actionable
decisions.

The inference ran AFTER the work and WIDENED the obligation. A
declaration runs BEFORE the work and NARROWS it. Same actor deciding —
the agent is the only thing that can read a natural-language request —
but a mis-declaration now shows too little, which someone notices,
instead of silently showing 25x too much with stale data.

**Append-only.** Rows are never updated, so the phase history shows
what each phase declared. ``user_turn`` records how many user messages
the task had when the declaration was accepted: a same-turn
re-declaration that widens or lightens what the phase owes is asked
about once (``routers/tasks_ad_declaration``), and ``execute`` normally
follows a user message after a declared ``audit`` or ``change`` — the
phases that end by showing the user a list of changes.
"""

from datetime import UTC, datetime
import uuid

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.text_utils import SafeText

# What an ad task can be. ``edit`` is deliberately absent as a word —
# the distinction that matters is not "audit or edit", it is WHOSE
# judgement the change is:
#
#   ``audit``        the agent judges the ads and recommends changes. It
#                    owes coverage of what it declared, and its proposals
#                    are checked against the store's rules.
#   ``change``       the USER named the change ("lower widget-006 to
#                    1.00"). Nothing is being judged, so nothing is owed
#                    beyond the row itself: no coverage, no drilling, and
#                    no store-rule check on their value. It is still
#                    confirmed before it is applied, and executed exactly
#                    as approved — the money guards are the same.
#
# A third kind for the same job was refused for years on the reasoning
# that "two kinds meaning almost the same thing drift apart". They do —
# and here they had drifted into ONE, which is why a one-row instruction
# was made to compute a whole audit (CI, PR #232: 108k output tokens
# deliberating over whether the store rule would refuse the user's own
# number, then a 1200 s timeout).
KIND_AUDIT = 'audit'
KIND_CHANGE = 'change'
KIND_CREATE = 'create'
KIND_EXECUTE = 'execute'
KIND_INVESTIGATE = 'investigate'

AD_TASK_KINDS = frozenset({
    KIND_AUDIT,
    KIND_CHANGE,
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
    # 1-based phase number within the task: declarations accumulate one
    # per phase, and each accepted declaration takes the next number.
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
