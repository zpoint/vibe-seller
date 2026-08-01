"""Reading and enforcing an ad task phase's declared kind + scope.

The DB row is the authority (see ``app.models.ad_declaration``); this
module is the vocabulary everything else shares — the scope shape, the
whole-store rule, the in-scope predicates the gate and the console both
need, and the workspace mirror the gates actually read.

**Why a file at all when the row exists.** Stop gates run in-process and
could query the DB, but the AGENT also needs to see what it committed to
— re-reading its own declaration is how it stays consistent across a long
run. So the server writes the accepted declaration to ``AD_TASK.json``
after validating it. The file is a mirror, never an input: nothing
accepts a declaration that only exists on disk, which is what stops an
agent from editing its way to a different obligation.

**Scope shape** (all fields optional)::

    {
        'combos': [{'platform': 'amazon', 'country': 'AE'}],
        'campaigns': ['A1234567'],
        'products': ['WIDGET-006'],
    }

Absent or empty ``combos`` means THE WHOLE STORE. That is the
conservative reading of an unqualified "audit the ads", and it fails
toward more coverage rather than less — an agent that omits the field
gets asked for everything, not excused from everything.

Absent or empty ``campaigns`` means every campaign inside those combos.
``products`` is a human-facing anchor ("the widget-006 family") the agent
resolves to campaigns; it never gates anything on its own, because a
product name is not something the server can verify against an account.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.config import VIBE_SELLER_DIR
from app.models.ad_declaration import AD_TASK_KINDS, KIND_AUDIT

logger = logging.getLogger(__name__)

DECLARATION_FILENAME = 'AD_TASK.json'


def declaration_path(task_id: str) -> Path:
    """Path to a task's ``AD_TASK.json`` (task-workspace root)."""
    return VIBE_SELLER_DIR / 'tasks' / task_id / DECLARATION_FILENAME


def normalise_scope(raw: object) -> dict:
    """Coerce an agent-supplied scope into the canonical shape.

    Tolerant on purpose: this runs on the write path, where rejecting a
    nearly-right scope costs a whole run. Anything unrecognised is
    dropped rather than carried through as a value later code has to
    defend against.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict = {}

    combos = []
    for c in raw.get('combos') or []:
        if not isinstance(c, dict):
            continue
        platform = str(c.get('platform') or '').strip().lower()
        country = str(c.get('country') or '').strip().upper()
        if platform and country:
            combos.append({'platform': platform, 'country': country})
    if combos:
        out['combos'] = combos

    for key in ('campaigns', 'products'):
        vals = [
            str(v).strip() for v in (raw.get(key) or []) if str(v or '').strip()
        ]
        if vals:
            out[key] = vals

    return out


def is_whole_store(scope: dict | None) -> bool:
    """True when the declaration claims every marketplace the store has.

    Absence means whole-store — see the module docstring. This is the
    ONLY condition under which a report owes marketplace coverage.
    """
    return not (scope or {}).get('combos')


def combo_in_scope(scope: dict | None, platform: str, country: str) -> bool:
    """Is this (platform, country) inside the declared scope?"""
    if is_whole_store(scope):
        return True
    platform = (platform or '').strip().lower()
    country = (country or '').strip().upper()
    return any(
        c['platform'] == platform and c['country'] == country
        for c in (scope or {}).get('combos') or []
    )


def campaign_in_scope(scope: dict | None, campaign_id: str) -> bool:
    """Is this campaign inside the declared scope?

    An empty ``campaigns`` list means "every campaign in the declared
    combos", so it cannot narrow anything on its own — the combo check
    is the caller's job and is deliberately kept separate, because a
    report row often knows its campaign id without knowing its market.
    """
    ids = (scope or {}).get('campaigns') or []
    if not ids:
        return True
    cid = (campaign_id or '').strip()
    return any(cid == str(i).strip() for i in ids)


def scope_summary(scope: dict | None) -> str:
    """One human-readable line, for gate denials and log lines."""
    if is_whole_store(scope):
        return '全店所有市场'
    scope = scope or {}
    combos = '、'.join(
        f'{c["platform"]} {c["country"]}' for c in scope.get('combos') or []
    )
    campaigns = scope.get('campaigns') or []
    if campaigns:
        shown = '、'.join(str(c) for c in campaigns[:4])
        more = f' 等 {len(campaigns)} 个' if len(campaigns) > 4 else ''
        return f'{combos}（活动 {shown}{more}）'
    return f'{combos}（该市场全部活动）'


def write_declaration_file(task_id: str, kind: str, scope: dict) -> None:
    """Mirror an ACCEPTED declaration into the task workspace.

    Best-effort: the row is the authority, so an unwritable workspace
    degrades to gates that cannot see the declaration rather than to a
    failed task. Those gates then fail closed on their own terms.
    """
    path = declaration_path(task_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {'kind': kind, 'scope': scope}, ensure_ascii=False, indent=2
            ),
            encoding='utf-8',
        )
    except OSError:
        logger.warning(
            'could not mirror AD_TASK.json for task %s',
            task_id,
            exc_info=True,
        )


def load_declaration(task_id: str | None) -> dict | None:
    """The task's current declaration as ``{kind, scope}``, or None.

    Read from the workspace mirror so stop gates stay synchronous and
    free of a DB session. None means "never declared" — which for an ad
    report is itself a finding, not a free pass.
    """
    if not task_id:
        return None
    try:
        raw = json.loads(declaration_path(task_id).read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get('kind') or '').strip().lower()
    if kind not in AD_TASK_KINDS:
        return None
    return {'kind': kind, 'scope': normalise_scope(raw.get('scope'))}


def declared_kind(task_id: str | None) -> str | None:
    """Just the kind, or None when nothing was declared."""
    decl = load_declaration(task_id)
    return decl['kind'] if decl else None


def owes_marketplace_coverage(decl: dict | None) -> bool:
    """Does this phase owe every marketplace the store is configured for?

    Only a whole-store AUDIT does. A scoped audit owes its own combos; a
    create/execute/investigate phase owes none. This single predicate is
    what stops a one-product task from being told it must cover five
    marketplaces — the failure that made an agent transcribe a
    four-day-old report to satisfy a gate.
    """
    if not decl:
        return False
    return decl['kind'] == KIND_AUDIT and is_whole_store(decl.get('scope'))
