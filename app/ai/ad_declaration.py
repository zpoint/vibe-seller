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
from app.models.ad_declaration import (
    AD_TASK_KINDS,
    KIND_AUDIT,
    KIND_INVESTIGATE,
)

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


def _combo_keys(scope: dict | None) -> set[tuple[str, str]]:
    return {
        (c['platform'], c['country']) for c in (scope or {}).get('combos') or []
    }


def is_narrowing(prev: dict | None, new: dict | None) -> bool:
    """True when ``new`` sits strictly inside ``prev``.

    Exists because the first version of the declaration rule could not be
    obeyed. An agent must declare BEFORE opening a browser, yet a request
    that names a product ("the widget-006 ads") can only be turned into
    campaign ids BY browsing. So it had two bad options: declare
    late, or declare a scope with no campaigns — which means EVERY
    campaign in the market, and pulls the whole market's completeness
    obligation with it. Observed live: a request for one SKU family
    became an audit owing all of Amazon SA.

    Splitting the declaration by what is knowable when fixes it. The
    markets come from the request and stay fixed; the campaigns are a
    refinement, allowed once enumeration reveals them. Refining may only
    ever REMOVE reach:

    * the kind may not change,
    * the markets must be identical — adding one is widening, which is
      the move the whole design exists to prevent,
    * the campaign list must go from "all of them" to a named set, or to
      a subset of an already-named set.
    """
    if prev is None or new is None:
        return False
    if _combo_keys(prev) != _combo_keys(new):
        return False
    prev_c = {str(c).strip() for c in (prev.get('campaigns') or [])}
    new_c = {str(c).strip() for c in (new.get('campaigns') or [])}
    # Empty means "every campaign in these markets" — never a narrowing.
    if not new_c:
        return False
    if not prev_c:
        return True
    return new_c <= prev_c


def _axis_within(prev_vals: set, new_vals: set) -> bool:
    """Does one axis of a scope (combos, campaigns) reach no further?

    The whole subtlety of comparing two scopes lives here, and it is
    that ``set() <= anything`` is True while an EMPTY axis means
    EVERYTHING. So an emptied axis is the widest value there is, not the
    narrowest, and plain subset logic gets it exactly backwards.
    """
    if not prev_vals:
        return True  # prev already reached everything; nothing is wider
    return bool(new_vals) and new_vals <= prev_vals


def reach_within(prev: dict | None, new: dict | None) -> bool:
    """Does ``new`` reach no further than ``prev``?

    Weaker than :func:`is_narrowing` on purpose: identical scopes pass.
    ``is_narrowing`` answers "is this a genuine refinement?" — it is the
    rule for re-declaring the SAME kind, where re-stating an unchanged
    scope is a no-op worth refusing. This answers the different question
    a kind CHANGE needs: "can this touch anything the previous
    declaration did not?"

    Empty means everything on both axes (see :func:`_axis_within`),
    which is what makes the asymmetry matter: whole-store → one market
    is inward, one market → whole-store is the widening move the design
    exists to prevent.
    """
    if prev is None or new is None:
        return False
    prev_c = {str(c).strip() for c in (prev.get('campaigns') or [])}
    new_c = {str(c).strip() for c in (new.get('campaigns') or [])}
    combos_ok = _axis_within(_combo_keys(prev), _combo_keys(new))
    return combos_ok and _axis_within(prev_c, new_c)


# The ONE within-turn kind change that is allowed, and why it is safe.
#
# The ratchet used to refuse every kind change, on the reasoning that an
# agent must not re-declare its way around a gate. True — but it left no
# move at all for the opposite mistake. An agent that declared
# `investigate` ("just read the numbers") and then produced a table of
# bid changes has UNDER-declared what it owes: `investigate` owes no
# marketplace coverage and opens no review console, so the user is handed
# recommendations with no way to act on them. Its only legal option was
# to delete the recommendations.
#
# So the rule is not "the kind may never change", it is **a
# re-declaration may never reduce what the phase owes, and may never
# widen its reach**. `investigate` → `audit` adds the coverage
# obligation and opens the console; it is a tightening, and the reach
# check below is what keeps it from smuggling in a wider scope.
# `audit` → `investigate` is the escape and stays refused, as does
# every other pair.
KIND_UPGRADES = frozenset({(KIND_INVESTIGATE, KIND_AUDIT)})


def supersedes(
    prev_kind: str,
    prev_scope: dict | None,
    new_kind: str,
    new_scope: dict | None,
) -> bool:
    """May this re-declaration replace ``prev`` WITHIN the same turn?

    The whole within-turn ratchet, in one predicate — so the router
    cannot express a rule the gates disagree with.
    """
    if new_kind == prev_kind:
        return is_narrowing(prev_scope, new_scope)
    if (prev_kind, new_kind) in KIND_UPGRADES:
        return reach_within(prev_scope, new_scope)
    return False


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


def owed_combos(decl: dict | None, store_combos: list[dict]) -> list[dict]:
    """Which marketplaces this phase must actually cover.

    **An audit owes what it declared. Declaring nothing declares
    everything.**

    An earlier version asked a narrower question — "is this a whole-store
    audit?" — and treated an omitted combo list as the only way to be
    one. The first live run walked straight through the hole it left: the
    agent declared ``kind=audit`` and then listed all five of the store's
    marketplaces EXPLICITLY. Semantically that is the whole store, but
    the combo list was non-empty, so the phase was judged scoped and owed
    no coverage at all — a full audit could then quietly cover one market.

    Reading the obligation off the declared list instead makes the two
    spellings agree, and keeps the property that mattered: a phase is
    held to exactly what it committed to, never to more.

    ``create`` / ``execute`` / ``investigate`` owe nothing. An undeclared
    phase owes nothing here either — it is refused separately, by
    ``ad_declaration_checks``, which is a better error than silently
    demanding five marketplaces of a task nobody scoped.
    """
    if not decl or decl['kind'] != KIND_AUDIT:
        return []
    if is_whole_store(decl.get('scope')):
        return list(store_combos)
    return list((decl.get('scope') or {}).get('combos') or [])
