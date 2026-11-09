"""Which marketplaces an ad task owes, and what history says about them.

``AUDIT_TARGETS.json`` is written by the SERVER into the task dir: the
(platform, country) combos the store is configured for, which an
``audit`` declared over the whole store owes (``ad_declaration.
owed_combos``). The agent cannot shrink it.

:func:`prior_campaign_tsvs` counts the campaigns recent audits drilled
per marketplace — the only independent evidence the server has about a
console it cannot browse, used to challenge a list that suddenly shrank.
"""

from __future__ import annotations

import json
from pathlib import Path
import time

from app.ai.stop_gates.ad_tables import ads_dirs
from app.config import VIBE_SELLER_DIR

TARGETS_FILENAME = 'AUDIT_TARGETS.json'

# How far back a TSV still counts as evidence of "recently had campaigns"
# (~6 weekly audits); without a window the count only ever grows.
PRIOR_TSV_WINDOW_DAYS = 45

# Ad platforms with a per-marketplace console, and a ``## <Platform> <CC>``
# heading matched by SHAPE (marketplace codes come from store config and
# are not enumerable). Used to recognise an audit report file.
AD_PLATFORMS = ('amazon', 'noon')
COMBO_HEAD_PATTERN = r'((?i:' + '|'.join(AD_PLATFORMS) + r'))\s+([A-Z]{2,3})\b'
AUDIT_SECTION_PATTERN = r'(?m)^##.*' + COMBO_HEAD_PATTERN


def targets_path(task_id: str):
    """Path to a task's AUDIT_TARGETS.json (task-workspace root)."""
    return VIBE_SELLER_DIR / 'tasks' / task_id / TARGETS_FILENAME


def prior_campaign_tsvs(
    slug: str | None,
    platform: str,
    country: str,
    within_days: int = PRIOR_TSV_WINDOW_DAYS,
) -> int:
    """Distinct campaigns this store's recent audits drilled in a combo.

    An UPPER bound on today's live count (campaigns get paused), so a
    caller challenges only a collapse, never any shortfall. Counts
    campaigns, not files: each drilled campaign leaves ``<id>.tsv`` and
    ``<id>.searchterms.tsv``. Only this store's own directories count.
    """
    if not slug:
        return 0
    cutoff = time.time() - within_days * 86400
    newest: dict[str, float] = {}
    for d in ads_dirs(slug, platform, country):
        try:
            files = [p for p in d.iterdir() if p.suffix == '.tsv']
        except OSError:
            continue
        for p in files:
            try:
                m = p.stat().st_mtime
            except OSError:
                continue
            cid = p.name.split('.', 1)[0]
            newest[cid] = max(newest.get(cid, 0.0), m)
    return sum(1 for m in newest.values() if m >= cutoff)


def write_declared_targets(
    task_dir,
    platform_countries: dict[str, list[str]],
    slug: str | None = None,
) -> None:
    """Write the SERVER's combo obligation for a task (best-effort).

    ``AUDIT_SCOPE.json`` grounds *how many* campaigns a combo owes, but
    the agent also writes *which combos exist* — so the one number it
    could still shrink was the combo count itself. Observed live: a store
    configured for 5 combos got a one-combo scope, the gate agreed the
    report was complete, and four marketplaces were never audited.

    The server already knows the answer (``Store.platform_countries`` —
    user-configured in Settings, and a monotonic union of what past tasks
    observed, so it never shrinks). Writing it into the workspace at task
    start makes it BOTH halves of the contract: the reviewer checks the
    scope against it, and the agent can read it to see what it owes
    instead of inferring the list from "reference only" prompt context.

    The agent may still declare a combo EMPTY (``active_ids: []`` /
    ``total_active: 0``) — "no live campaigns here" is a legitimate
    finding. What it can no longer do is omit the combo silently, nor
    assert emptiness for a marketplace that recently had campaigns (see
    :func:`prior_campaign_tsvs`).

    ``slug`` is recorded so the reviewer can find this store's own audit
    history without globbing ``stores/*`` and borrowing another store's.

    Best-effort: an unwritable workspace degrades to the previous
    behaviour (agent-chosen combo set) rather than blocking the task.
    """
    combos = [
        {'platform': str(p).strip().lower(), 'country': str(c).strip().upper()}
        for p, cs in (platform_countries or {}).items()
        if isinstance(cs, list)
        for c in cs
        if str(p).strip() and str(c).strip()
    ]
    if not combos:
        return  # ensure_targets_file covers it once an ad task declares
    payload: dict = {'combos': combos}
    if slug:
        payload['slug'] = slug
    try:
        (task_dir / TARGETS_FILENAME).write_text(
            json.dumps(payload, indent=2) + '\n',
            encoding='utf-8',
        )
    except OSError:  # pragma: no cover — best-effort
        pass


def ensure_targets_file(task_dir) -> None:
    """Give a declared ad task an explicit empty menu if it has none.

    The ad skills say "declare, then read ``AUDIT_TARGETS.json``", but a
    store with no configured marketplace never got the file, and its
    absence read as "the server hasn't finished yet": in CI audits on
    such a store re-read the spec dozens of times hunting for it,
    rewrote the store's ``metadata.json`` to invent a menu, and ran out
    of time. Written at DECLARATION, not at task start, so only work
    that reads a menu ever sees one — a store task that merely looks
    something up is not handed an ad artefact to wonder about. An empty
    menu owes exactly what an absent one did (see
    :func:`load_declared_targets`); the skill docs give its rule.
    """
    path = Path(task_dir) / TARGETS_FILENAME
    if path.exists():
        return
    try:
        path.write_text(json.dumps({'combos': []}) + '\n', encoding='utf-8')
    except OSError:  # pragma: no cover — best-effort
        pass


def load_declared_targets(task_id: str | None) -> list[dict]:
    """``[{platform, country}, …]`` the SERVER says this task must audit.

    Empty when the file is absent (a task from before this contract, or a
    store with no recorded platforms) — callers then fall back to the
    agent-declared combo set, i.e. the previous behaviour. Absence is
    never itself a gap: only a MISSING combo that the server did declare
    is one.
    """
    if not task_id:
        return []
    try:
        raw = targets_path(task_id).read_text(encoding='utf-8')
    except OSError:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    out: list[dict] = []
    for c in data.get('combos') or []:
        if not isinstance(c, dict):
            continue
        platform = str(c.get('platform') or '').strip()
        country = str(c.get('country') or '').strip()
        if platform and country:
            out.append({'platform': platform, 'country': country})
    return out
