"""Declare what an ad task phase is for, before doing the work.

Backs the ``vibe_seller_declare_ad_task`` MCP tool. Split out of
``app/routers/tasks.py`` so that file stays under the repo's
800-line-per-file cap.

The rules enforced here are the whole reason the declaration is a server
endpoint and not a file the agent writes:

* **Append-only.** A phase's declaration is never updated. An agent
  cannot rewrite what it committed to so that it matches whatever it
  ended up producing.
* **A new declaration needs a new user turn.** Re-declaring is how a
  conversation legitimately changes purpose ("create a listing" →
  "now audit the ads for it"), so it must be possible — but only the
  person typing may open a new phase. Enforced by comparing the task's
  user-message count against the count recorded on the previous
  declaration.

Both were prose-only in the first draft of this design, which is exactly
the class of contract this codebase keeps learning to move into code.
"""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import ad_declaration
from app.auth import get_current_user
from app.database import get_db
from app.models.ad_declaration import AD_TASK_KINDS, AdTaskDeclaration
from app.models.task import Task
from app.models.task_message import TaskMessage
from app.models.user import User

router = APIRouter(prefix='/api/tasks', tags=['tasks'])


class DeclareAdTaskRequest(BaseModel):
    kind: str
    # Free-shaped on the wire, normalised server-side. See
    # ``ad_declaration.normalise_scope`` for the canonical form and the
    # rule that absent combos mean the whole store.
    scope: dict | None = None


async def _user_turns(db: AsyncSession, task_id: str) -> int:
    """How many user messages this task has had."""
    return (
        await db.scalar(
            select(func.count())
            .select_from(TaskMessage)
            .where(
                TaskMessage.task_id == task_id,
                TaskMessage.role == 'user',
            )
        )
    ) or 0


async def _latest(db: AsyncSession, task_id: str) -> AdTaskDeclaration | None:
    return await db.scalar(
        select(AdTaskDeclaration)
        .where(AdTaskDeclaration.task_id == task_id)
        .order_by(AdTaskDeclaration.seq.desc())
        .limit(1)
    )


def _as_dict(row: AdTaskDeclaration) -> dict:
    try:
        scope = json.loads(row.scope)
    except (ValueError, TypeError):
        scope = {}
    return {
        'seq': row.seq,
        'kind': row.kind,
        'scope': scope if isinstance(scope, dict) else {},
        'user_turn': row.user_turn,
        'created_at': row.created_at,
    }


@router.get('/{task_id}/ad-declarations')
async def list_ad_declarations(
    task_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
):
    """Every declaration this task has made, oldest first.

    The frontend needs the whole sequence, not just the latest: a task
    that created a listing and then audited its ads has two result items
    in the conversation, and only the second gets a console.
    """
    rows = (
        await db.scalars(
            select(AdTaskDeclaration)
            .where(AdTaskDeclaration.task_id == task_id)
            .order_by(AdTaskDeclaration.seq.asc())
        )
    ).all()
    return [_as_dict(r) for r in rows]


@router.post('/{task_id}/ad-declaration')
async def declare_ad_task(
    task_id: str,
    body: DeclareAdTaskRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
):
    """Record what this phase is for. Append-only; needs a new user turn."""
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail='Task not found')

    kind = (body.kind or '').strip().lower()
    if kind not in AD_TASK_KINDS:
        raise HTTPException(
            status_code=400,
            detail=(
                f'kind must be one of {sorted(AD_TASK_KINDS)}. '
                'Note there is no "edit" kind — a request to change a few '
                'bids is kind="audit" with those campaigns in scope.'
            ),
        )

    scope = ad_declaration.normalise_scope(body.scope)
    turns = await _user_turns(db, task_id)
    prev = await _latest(db, task_id)

    if prev is not None and turns <= prev.user_turn:
        # Same turn. Two legal moves, both of them ratchets — see
        # ``ad_declaration.supersedes``.
        #
        # 1. NARROW at the same kind. The markets come from the request
        #    and are knowable before any browsing, so they are fixed at
        #    the first declaration. Campaign ids are not: a request
        #    naming a product ("the widget-006 ads") only becomes ids
        #    by enumerating the account. Requiring both upfront made the
        #    rule unfollowable — observed live, an agent declared the
        #    market with no campaigns, which means EVERY campaign in it,
        #    and a one-family request became an audit owing all of
        #    Amazon SA.
        # 2. UPGRADE `investigate` → `audit` without widening reach. A
        #    phase that said it was only reading, and then produced
        #    decisions, under-declared what it owes; refusing this left
        #    it no move but to delete the recommendations the user
        #    asked for.
        #
        # Neither move can widen and neither can shed an obligation,
        # which is the property that matters: an agent still cannot
        # re-declare its way around a gate.
        try:
            prev_scope = json.loads(prev.scope)
        except (ValueError, TypeError):
            prev_scope = {}
        if ad_declaration.supersedes(prev.kind, prev_scope, kind, scope):
            row = AdTaskDeclaration(
                task_id=task_id,
                seq=prev.seq + 1,
                kind=kind,
                scope=json.dumps(scope, ensure_ascii=False),
                user_turn=turns,
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            ad_declaration.write_declaration_file(task_id, kind, scope)
            return _as_dict(row)
        raise HTTPException(
            status_code=409,
            detail=(
                f'This task already declared kind="{prev.kind}" for the '
                'current turn, and this call neither NARROWS it nor '
                'corrects it upward.\n'
                'The two legal in-turn moves are:\n'
                '1. NARROW at the same kind — same marketplaces, and a '
                'campaign list that goes from "all of them" to a named '
                'subset. That is how you record campaign ids you could '
                'only learn by enumerating the account.\n'
                '2. Correct kind="investigate" to kind="audit" when the '
                'work turned out to produce bid/pause/negate decisions '
                'rather than just figures — same marketplaces or fewer, '
                'same campaigns or fewer. This one exists so an '
                'under-declared phase can still hand the user a review '
                'console instead of deleting its recommendations.\n'
                'You cannot add a marketplace, widen the campaign list, '
                'or move to a kind that owes LESS than the one you '
                'declared (audit → investigate is refused). A wider '
                'scope needs a new message from the USER. Work within '
                'what you declared, or say in your result why it does '
                'not fit and let them redirect you.'
            ),
        )

    row = AdTaskDeclaration(
        task_id=task_id,
        seq=(prev.seq + 1) if prev else 1,
        kind=kind,
        scope=json.dumps(scope, ensure_ascii=False),
        user_turn=turns,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    # Mirror for the stop gates, which run synchronously without a DB
    # session. The row above is the authority; this is a copy.
    ad_declaration.write_declaration_file(task_id, kind, scope)

    return _as_dict(row)
