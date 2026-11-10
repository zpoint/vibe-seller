"""Declare what an ad task phase is for, before doing the work.

Backs the ``vibe_seller_declare_ad_task`` MCP tool. Split out of
``app/routers/tasks.py`` so that file stays under the repo's
800-line-per-file cap.

Why it is a server endpoint and not a file the agent writes:

* **Append-only.** A phase's declaration is never updated, so what it
  committed to stays on record next to what it produced.
* **One round.** A same-turn re-declaration that widens or lightens what
  the phase owes, and ``execute`` without a user reply to a declared
  audit, are answered once (409) naming what that normally needs; the
  same call again is recorded as declared. The decision is the agent's;
  the user sees an unconfirmed execution marked as such.
"""

import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import question_cards
from app.ai import ad_declaration
from app.ai.claude_backend_manager import agent_manager
from app.ai.claude_backend_utils import get_next_seq
from app.ai.stop_gates import AD_SKILLS, record_attempt
from app.ai.stop_gates.ad_changes import (
    approved_summary,
    freeze_approved_changes,
    unfrozen_changes,
)
from app.auth import get_current_user
from app.database import get_db
from app.events.bus import event_bus
from app.models.ad_declaration import (
    AD_TASK_KINDS,
    KIND_AUDIT,
    KIND_EXECUTE,
    AdTaskDeclaration,
)
from app.models.task import Task
from app.models.task_message import TaskMessage
from app.models.user import User
from app.telemetry_harness import send_ad_task_declared

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


async def _last_user_message_at(db: AsyncSession, task_id: str) -> str | None:
    return await db.scalar(
        select(func.max(TaskMessage.created_at)).where(
            TaskMessage.task_id == task_id,
            TaskMessage.role == 'user',
        )
    )


async def _user_replied_to_an_audit(db: AsyncSession, task: Task) -> bool:
    """Whether the user has answered proposals they were shown.

    Execution may start only after an ``audit`` phase (this task's, or
    the parent's for an execution task spawned from it) and a USER
    message that came after that audit's turn. Without it a phase could
    apply changes nobody saw: an agent acting on its own audit in the
    same turn, or an orchestrator's child executing a plan the user was
    never asked about. Whether the reply means yes is still the agent's
    reading of it; that the user saw the list and answered is enforced.
    """
    for owner_id in (task.id, task.parent_task_id):
        if not owner_id:
            continue
        audit_turn = await db.scalar(
            select(func.max(AdTaskDeclaration.user_turn)).where(
                AdTaskDeclaration.task_id == owner_id,
                AdTaskDeclaration.kind == KIND_AUDIT,
            )
        )
        if audit_turn is not None and (
            await _user_turns(db, owner_id) > audit_turn
        ):
            return True
    return False


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

    The sequence is the task's phase history: a task that created a
    listing and then audited its ads has two declarations, one per phase.
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
    """Record what this phase is for. Append-only; see the module doc."""
    kind = (body.kind or '').strip().lower()
    scope = ad_declaration.normalise_scope(body.scope)
    try:
        outcome, out = await _declare(task_id, body, db)
    except HTTPException as exc:
        if exc.status_code in (400, 409):
            send_ad_task_declared(task_id, kind, scope, 'refused')
        raise
    approved = out.get('approved')
    send_ad_task_declared(
        task_id,
        kind,
        scope,
        outcome,
        approved_count=None if approved is None else len(approved),
    )
    return out


async def _declare(
    task_id: str, body: DeclareAdTaskRequest, db: AsyncSession
) -> tuple[str, dict]:
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

    # A scope entry NAMES what is in scope; exclusion/negation text in
    # an entry would put "what is not" into the list of what is (CI
    # 201k). Refused at the tool boundary — the agent just re-declares
    # with the clean anchor.
    try:
        ad_declaration.validate_scope(body.scope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    scope = ad_declaration.normalise_scope(body.scope)
    turns = await _user_turns(db, task_id)
    prev = await _latest(db, task_id)

    # Every check here is one round: the agent is told once what the
    # declaration means for this phase, and a second identical call is
    # recorded as declared. What it owes is then judged from what it
    # declared; the decision stays the agent's.
    if kind == KIND_EXECUTE:
        # Execution needs a user reply to proposals they were shown.
        confirmed = await _user_replied_to_an_audit(db, task)
        if not confirmed and record_attempt(task_id, 'declare:execute') == 1:
            raise HTTPException(
                status_code=409,
                detail=(
                    'Nothing the user has seen and answered is approved '
                    'yet — a request to make a change is not the user '
                    'seeing the exact change. Declare kind="audit", list '
                    'the changes in CHANGES.tsv, and end your turn '
                    'showing them to the user; declare "execute" once '
                    'they reply.'
                ),
            )
        # A change the user's message did not name, picked on a question
        # card since: the card is the agent's own multiple choice, not the
        # user approving a money change (observed: "go ahead and apply
        # it", a card whose first option was a raise, and the raise ran).
        # Also after the refusal above: an agent told to show the user the
        # change asked on a card instead, and the card is not a reply.
        picked_on_a_card = question_cards.answered_after(
            task_id, await _last_user_message_at(db, task_id)
        ) and bool(await asyncio.to_thread(unfrozen_changes, task_id))
        if picked_on_a_card:
            # Declared again after being told: recorded, and posted to
            # the user as NOT confirmed by them.
            confirmed = False
        if (
            picked_on_a_card
            and record_attempt(task_id, 'declare:execute_card') == 1
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    'A question card answer does not approve a change to '
                    'ads: CHANGES.tsv now holds a change no earlier '
                    'approval did, and the user did not name it in a '
                    'message. End your turn asking about it in words; '
                    'declare "execute" once they reply.'
                ),
            )
    elif prev is not None and turns <= prev.user_turn:
        # Same turn. Narrowing, or correcting investigate → audit, is
        # always fine (``ad_declaration.supersedes``); a wider or lighter
        # re-declaration is asked about once.
        try:
            prev_scope = json.loads(prev.scope)
        except (ValueError, TypeError):
            prev_scope = {}
        if (
            not ad_declaration.supersedes(prev.kind, prev_scope, kind, scope)
            and record_attempt(task_id, 'declare:in_turn') == 1
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    f'This task already declared kind="{prev.kind}" for '
                    'the current turn. Narrowing it, or correcting '
                    '"investigate" to "audit", is always fine; a wider '
                    'scope or a kind that owes less is normally a new '
                    'request from the USER. If you are sure, declare '
                    'again and it is recorded as you declare it.'
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
    # session. The row above is the authority; this is a copy. It is a
    # JSON write plus ensure_targets_file, so it goes off the loop like
    # the reads on either side of it.
    await asyncio.to_thread(
        ad_declaration.write_declaration_file, task_id, kind, scope
    )
    note = await _skill_note(task_id)

    # Execution starts here: freeze the approval it will be checked
    # against (a re-declaration re-freezes; earlier freezes are kept).
    if kind == KIND_EXECUTE:
        out = await _with_approval(db, task_id, row, confirmed=confirmed)
    else:
        out = _as_dict(row)
    if note:
        out['load_skill'] = note
    return 'accepted', out


async def _skill_note(task_id: str) -> str | None:
    """Name the ad skill when the phase declared without one loaded.

    The declaration is the moment the server knows this is ad work. The
    skill holds the data contract the checks read (CAMPAIGNS.tsv,
    CHANGES.tsv, how a user's standard is written in notes.md); an agent
    that declared and then browsed with only the browser skill wrote the
    user's standard as a sentence and no CHANGES.tsv at all.

    Only the skills that DO keep that contract are named. `qianniu-ads`
    counts as an ad skill above — a 千牛 store with its skill loaded must
    not be nagged to load another platform's — but it documents no
    TSVs and declares no gates, so pointing it at
    CAMPAIGNS.tsv would send the agent to satisfy a contract its own
    skill cannot meet.
    """
    loaded, _workspace = await asyncio.to_thread(
        agent_manager.loaded_skills_and_workspace, task_id
    )
    if AD_SKILLS & set(loaded or ()):
        return None
    return (
        "Load the ad skill for this store's platform now (amazon-ads or "
        'noon-ads) and follow its data contract: '
        'CAMPAIGNS.tsv, CHANGES.tsv, EXECUTION_LOG.tsv, and a standard the '
        'user states written as a `key: value` line in the store notes.'
    )


async def _with_approval(
    db: AsyncSession,
    task_id: str,
    row: AdTaskDeclaration,
    *,
    confirmed: bool,
) -> dict:
    """Freeze CHANGES.tsv as the approval; show the agent and the user
    what froze — the user sees it in the conversation before any change,
    so a list that is not what they approved is caught while it can
    still be stopped."""
    await asyncio.to_thread(freeze_approved_changes, task_id)
    approved = await asyncio.to_thread(approved_summary, task_id)
    head = (
        'Applying the approved ad changes:'
        if confirmed
        else 'Applying ad changes the user has NOT confirmed:'
    )
    text = head + '\n' + ('\n'.join(f'- {a}' for a in approved) or '- (none)')
    db.add(
        TaskMessage(
            task_id=task_id,
            role='assistant',
            content=text,
            seq=await get_next_seq(db, task_id),
        )
    )
    await db.commit()
    await event_bus.emit(
        'task_message',
        {'task_id': task_id, 'role': 'assistant', 'content': text},
    )
    return {
        **_as_dict(row),
        'approved': approved,
        'note': (
            'Execution is checked against exactly these changes (holds '
            'left out). If they are not what the user approved, fix '
            'CHANGES.tsv and declare execute again before changing '
            'anything.'
        ),
    }
