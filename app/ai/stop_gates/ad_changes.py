"""Gate: proposed and executed ad changes obey the store's rules.

Reads the fixed-header TSVs (``ad_tables``), never the report prose.
One round, like every check (``task_submission.apply_round``): refused
once, then through with caveats. Execution that differs from the
approval, and an unreadable table, are *contradictions*: also marked
with a banner at the top of the result.

1. **Owner rules on CHANGES.tsv** — thresholds from ``ad_rules``
   (store notes override the defaults):
   - no ``lower`` while ACOS is under ``acos_no_lower`` (or ROAS above
     its equivalent) — cutting a converting keyword throws away profit;
   - a new bid not below ``cpc * cpc_floor_multiplier`` — under the
     price it already clears, the auction stops serving it (the live
     ``1.30 → 0.05`` class);
   - no bare ``hold`` on a row with ROAS above ``scale_roas`` — a proven
     winner is raised, or the reason says why not;
   - nothing but ``hold`` on a target changed within
     ``change_cooldown_days`` — yesterday's move has not produced data yet.
2. **investigate proposes nothing** — a phase declared read-only owes no
   coverage, so it may not hand out decisions.
3. **Execution matches the approval** — every EXECUTION_LOG.tsv row is
   an approved CHANGES.tsv row (frozen when the task declared
   ``execute``, so it cannot be edited mid-execution), bids within 0.01 (the live
   ``1.30 → 11.3`` 10x overspend), and the per-campaign TSV's
   ``applied_*`` columns agree with the log.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import shutil

from app.ai import ad_declaration
from app.ai.stop_gates import GateDeny
from app.ai.stop_gates.ad_rules import DEFAULT_RULES
from app.ai.stop_gates.ad_tables import (
    ACTIONS,
    BID_ACTIONS,
    CHANGE_COLUMNS,
    CHANGES_FILE,
    EXECUTION_COLUMNS,
    EXECUTION_LOG_FILE,
    TARGETING_COLUMNS,
    campaign_files,
    number,
    read_table,
)
from app.ai.stop_gates.ad_task import (
    child_task_ids,
    parent_task_id,
    store_slug_for,
    task_dir,
    task_started_at,
)
from app.config import DATA_DIR
from app.models.ad_declaration import KIND_EXECUTE, KIND_INVESTIGATE

GATE_NAME = 'ad_changes'
APPROVED_DIR = DATA_DIR / 'approved_changes'
BID_TOLERANCE = 0.01


def _same_target(a: dict, b: dict) -> bool:
    """Whether two rows name the same keyword/target.

    The ad group may be blank on one side (a keyword in a single ad
    group needs none, and the drilled TSV usually records it): a blank
    then matches any. Callers that must not guess WHICH copy use
    ``_matched`` instead — a blank names one only while the target sits
    in one ad group.
    """
    ka, kb = _key(a), _key(b)
    if (ka[0], ka[2], ka[3]) != (kb[0], kb[2], kb[3]):
        return False
    return not ka[1] or not kb[1] or ka[1] == kb[1]


def _key(row: dict) -> tuple[str, str, str, str]:
    return (
        row.get('campaign_id', ''),
        row.get('ad_group', '').lower(),
        row.get('target', '').lower(),
        row.get('match_type', '').lower(),
    )


def _campaign_rows(
    slug: str | None, row: dict, where: dict | None = None
) -> tuple[dict, ...]:
    """The campaign TSV's rows for this target/match_type, if drilled.

    ``where`` locates the campaign for *row*: an EXECUTION_LOG row (and
    a drilled one) carries no platform/country, so it is found through
    the row it is matched against. Neither side carrying a platform, or
    no drilled TSV, means there is nothing to count.
    """
    if not slug:
        return ()
    at = next((r for r in (where, row) if r and r.get('platform')), None)
    if at is None:
        return ()
    cid = at.get('campaign_id') or row.get('campaign_id', '')
    path, _ = campaign_files(slug, at['platform'], at.get('country', ''), cid)
    table = read_table(path, TARGETING_COLUMNS) if path else None
    if not table or table.error:
        return ()
    key = _key(row)
    return tuple(
        t for t in table.rows if _key({**t, 'campaign_id': cid})[2:] == key[2:]
    )


def _matched(slug: str | None, a: dict, b: dict) -> bool:
    """``_same_target``, but a blank ad_group names one copy only.

    One keyword can sit in several ad groups of a campaign, and a blank
    then names none of them: which copy it means has to be said (the
    same rule ``ads_bulk --from-changes`` enforces). With no drilled TSV
    there is nothing to count, so a blank matches as it always did —
    otherwise a correctly applied change read as "not approved". The
    other side locates the campaign for a blank that has no
    platform/country of its own (an executed row).
    """
    if not _same_target(a, b):
        return False
    if _key(a)[1] and _key(b)[1]:
        return True
    blank, other = (a, b) if not _key(a)[1] else (b, a)
    return len(_campaign_rows(slug, blank, other)) <= 1


def _label(row: dict) -> str:
    return (
        f'{row.get("campaign_id")} / {row.get("target")} '
        f'({row.get("match_type") or "-"})'
    )


# ── approval snapshot ───────────────────────────────────────────────


def approved_path(task_id: str) -> Path:
    """The current (latest) approval of *task_id*."""
    return APPROVED_DIR / f'{task_id}.tsv'


def _earlier_approvals(task_id: str) -> list[Path]:
    """Approvals this task already executed under, oldest first."""
    found = []
    for p in APPROVED_DIR.glob(f'{task_id}.*.tsv'):
        n = p.name[len(task_id) + 1 : -len('.tsv')]
        if n.isdigit():
            found.append((int(n), p))
    return [p for _, p in sorted(found)]


def approval_files(task_id: str) -> list[Path]:
    """Every approval this task's EXECUTION_LOG may be held to."""
    current = approved_path(task_id)
    return _earlier_approvals(task_id) + ([current] if current.exists() else [])


def remove_approvals(task_id: str) -> None:
    """Drop every approval frozen for *task_id* (blocking)."""
    for path in approval_files(task_id):
        path.unlink(missing_ok=True)


def _changes_source(task_id: str) -> Path | None:
    """The CHANGES.tsv an execution works from: this task's or the
    nearest ancestor's (an execution task spawned from an audit)."""
    seen: set[str] = set()
    cur: str | None = task_id
    while cur and cur not in seen and len(seen) < 5:
        seen.add(cur)
        p = task_dir(cur) / CHANGES_FILE
        if p.exists():
            return p
        cur = parent_task_id(cur)
    return None


def freeze_approved_changes(task_id: str) -> Path | None:
    """Copy the CHANGES.tsv execution starts from out of the agent's reach.

    Called when the task declares ``execute``, which takes a new user
    message: the user has just said what to apply, and the agent has
    brought ``CHANGES.tsv`` in line with it — including a value the user
    amended ("do it, but at 1.35"). Freezing at the audit instead held
    execution to the audit's proposal, and the gate then steered the
    agent into moving a live bid the user had set to a value they had
    overruled. From here on, editing ``CHANGES.tsv`` changes nothing.

    With no ``CHANGES.tsv`` to start from, the current freeze is removed,
    so a new execution can never be checked against a stale approval. A
    freeze that changes were already executed under is kept beside the
    new one: the log still holds those changes, and the user approved
    them (a second approval in the same task made the first round's
    logged changes read as unapproved).
    """
    dest = approved_path(task_id)
    if dest.exists() and execution_logged(task_id):
        n = len(_earlier_approvals(task_id)) + 1
        dest.rename(APPROVED_DIR / f'{task_id}.{n}.tsv')
    src = _changes_source(task_id)
    if src is None:
        dest.unlink(missing_ok=True)
        return None
    APPROVED_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    return dest


def execution_logged(task_id: str) -> bool:
    """True once this task's EXECUTION_LOG.tsv records a change."""
    return bool(_execution_rows(task_id))


def _execution_rows(task_id: str) -> tuple[dict, ...]:
    """The changes recorded in one task's EXECUTION_LOG.tsv."""
    table = read_table(
        task_dir(task_id) / EXECUTION_LOG_FILE, EXECUTION_COLUMNS
    )
    if table is None or table.error:
        return ()
    return table.rows


def family_execution_rows(task_id: str) -> tuple[dict, ...]:
    """This task's EXECUTION_LOG rows plus its children's.

    The log a parent audit's execution child wrote is the only record
    that a stamped ``applied_at`` on the campaign TSV is a change that
    really happened: the parent's own dir has no log of it.
    """
    rows = _execution_rows(task_id)
    for child in child_task_ids(task_id):
        rows += _execution_rows(child)
    return rows


def approved_summary(task_id: str) -> list[str]:
    """The frozen approval's changes, one line each (holds left out).

    Returned to the agent when it declares ``execute``, so a freeze that
    does not match what the user approved is visible before any change
    is made, while it can still be corrected by declaring again.
    """
    path = find_approved(task_id)
    table = read_table(path, CHANGE_COLUMNS) if path else None
    if not table or table.error:
        return []
    return [
        f'{r["campaign_id"]} / {r["target"]} ({r["match_type"]}): '
        f'{r["action"]} {r.get("current_bid") or "-"} -> '
        f'{r.get("target_bid") or "-"}'
        for r in table.rows
        if r['action'].lower() != 'hold'
    ]


def find_approved(task_id: str) -> Path | None:
    """The approval frozen when this task's execution started."""
    p = approved_path(task_id)
    return p if p.exists() else None


# ── checks ──────────────────────────────────────────────────────────


def _recently_applied(
    slug: str | None, row: dict, cooldown_days: float
) -> str | None:
    """``applied_at`` of the target's last change if inside the window."""
    if not slug or cooldown_days <= 0:
        return None
    path, _ = campaign_files(
        slug, row['platform'], row['country'], row['campaign_id']
    )
    table = read_table(path, TARGETING_COLUMNS) if path else None
    if not table or table.error:
        return None
    now = datetime.now(UTC)
    for t in table.rows:
        if not _same_target({**t, 'campaign_id': row['campaign_id']}, row):
            continue
        stamp = t.get('applied_at', '')
        try:
            when = datetime.fromisoformat(stamp)
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        if (now - when).total_seconds() < cooldown_days * 86400:
            return stamp
    return None


def _drilled(slug: str | None, row: dict) -> dict | None:
    """The target's row in its campaign's own TSV, if drilled.

    A blank ad_group is resolved only while the target sits in one ad
    group; in several the lookup gives up, so the proposal is judged on
    its own numbers instead of one copy's.
    """
    rows = _campaign_rows(slug, row)
    if not _key(row)[1] and len(rows) > 1:
        return None
    for t in rows:
        if _same_target({**t, 'campaign_id': row['campaign_id']}, row):
            return t
    return None


def _evidence(slug: str | None, row: dict) -> dict:
    """The numbers a proposal is judged on.

    Each one comes from the campaign's own TSV when the drilled row has
    it, and only otherwise from the copy the proposal carries — the
    copy is what a mistaken or motivated row gets wrong (observed class:
    a ``lower 1.30 → 0.05`` with blank metrics skipped every check).
    CPC from the drilled row is spend / clicks.
    """
    t = _drilled(slug, row) or {}
    spend, clicks = number(t.get('spend')), number(t.get('clicks'))
    drilled_cpc = spend / clicks if spend is not None and clicks else None

    def pick(drilled, proposed):
        return drilled if drilled is not None else proposed

    return {
        'acos': pick(number(t.get('acos')), number(row.get('acos'))),
        'roas': pick(number(t.get('roas')), number(row.get('roas'))),
        # A proposal's own CPC of 0 is no evidence (it would void the
        # floor); a drilled row with no clicks has none either.
        'cpc': pick(drilled_cpc, number(row.get('cpc')) or None),
        'bid': pick(number(t.get('bid')), number(row.get('current_bid'))),
    }


def _changed_since(drilled: dict | None, since: datetime | None) -> bool:
    """Whether the drilled target was applied at or after *since*."""
    if since is None or not drilled:
        return False
    try:
        when = datetime.fromisoformat(drilled.get('applied_at') or '')
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when >= since


def rule_violations(
    rows: tuple[dict, ...],
    rules: dict,
    slug: str | None,
    executed: tuple[dict, ...] = (),
    since: datetime | None = None,
    logged: tuple[dict, ...] = (),
) -> list[str]:
    """Owner rules on proposed changes.

    *executed* are this task's EXECUTION_LOG rows: their targets were
    just changed by this very execution, so the cooldown — which keeps a
    proposal off a target changed recently — does not apply to them
    (observed: an execution refused because its own change was "inside
    the cooldown").

    *logged* are the EXECUTION_LOG rows that can back a stamp this task
    did not write itself — its own plus its children's (an audit whose
    execution child applied the change and rewrote the campaign TSV).
    A target stamped applied after *since* is excused only while one of
    them records it: the stamp is text in the agent's own file, and an
    unbacked one would exempt the row from every rule below.
    """
    done = tuple(executed)
    backing = tuple(logged) or done
    acos_floor = rules['acos_no_lower']
    roas_equiv = 100.0 / acos_floor if acos_floor else None
    out = []
    for row in rows:
        if any(_matched(slug, row, d) for d in done) or (
            _changed_since(_drilled(slug, row), since)
            and any(_matched(slug, row, d) for d in backing)
        ):
            # Already executed — by this task, or since it started by an
            # execution child of it whose log backs the stamp: no longer
            # a proposal, and its campaign TSV now carries the new bid,
            # so judging it again would compare the change with itself.
            continue
        action = row['action'].lower()
        if action not in ACTIONS:
            out.append(
                f'{_label(row)}: action "{row["action"]}" — use one of '
                f'{" / ".join(ACTIONS)}'
            )
            continue
        ev = _evidence(slug, row) if action != 'hold' else {}
        if action == 'hold':
            ev = {
                'acos': number(row.get('acos')),
                'roas': number(row.get('roas')),
            }
        acos, roas = ev.get('acos'), ev.get('roas')
        cpc, target = ev.get('cpc'), number(row.get('target_bid'))
        if action in BID_ACTIONS:
            # Without the numbers the owner rules cannot be applied, and
            # a proposal that skips them is the one that must not pass.
            missing = [
                name
                for name, value in (
                    ('ACOS or ROAS', acos if acos is not None else roas),
                    # The floor only bounds a cut; a raise (often on a
                    # target with no clicks yet) needs no CPC.
                    ('CPC', cpc if action == 'lower' else 0.0),
                )
                if value is None
            ]
            if missing:
                out.append(
                    f'{_label(row)}: {action} with no {" / ".join(missing)} '
                    f'in its campaign TSV or CHANGES.tsv — the store rules cannot be checked'
                )
            current = ev.get('bid')
            if (
                current is not None
                and target is not None
                and (
                    (action == 'raise' and target <= current)
                    or (action == 'lower' and target >= current)
                )
            ):
                out.append(
                    f'{_label(row)}: {action} from {current:g} to '
                    f'{target:g} goes the other way'
                )
        # A pause throws away the same converting traffic a cut would.
        if action in ('lower', 'pause') and (
            (acos is not None and acos < acos_floor)
            or (
                acos is None
                and roas is not None
                and roas_equiv
                and roas > roas_equiv
            )
        ):
            out.append(
                f'{_label(row)}: {action} with ACOS '
                f'{"-" if acos is None else f"{acos:g}"} / ROAS '
                f'{"-" if roas is None else f"{roas:g}"} — under '
                f'{acos_floor:g}% ACOS '
                'a target is only raised or held'
            )
        if action in BID_ACTIONS:
            if target is None:
                out.append(f'{_label(row)}: {action} without a target_bid')
            elif cpc is not None:
                floor = cpc * rules['cpc_floor_multiplier']
                if target < floor - 1e-9:
                    out.append(
                        f'{_label(row)}: target_bid {target:g} is below '
                        f'CPC {cpc:g} x {rules["cpc_floor_multiplier"]:g} '
                        f'= {floor:.2f}'
                    )
        if (
            action == 'hold'
            and roas is not None
            and roas > rules['scale_roas']
            and not row.get('reason')
        ):
            out.append(
                f'{_label(row)}: hold on ROAS {roas:g} (> '
                f'{rules["scale_roas"]:g}) needs a reason, or raise it'
            )
        if action != 'hold':
            stamp = _recently_applied(slug, row, rules['change_cooldown_days'])
            if stamp:
                out.append(
                    f'{_label(row)}: changed at {stamp}, inside the '
                    f'{rules["change_cooldown_days"]:g}-day cooldown — hold '
                    'it and say so in the reason'
                )
    return out


def _bid_matches(row: dict, want: dict) -> bool:
    got, target = number(row.get('to_bid')), number(want['target_bid'])
    return (
        got is not None
        and target is not None
        and abs(got - target) <= BID_TOLERANCE
    )


def execution_violations(
    executed: tuple[dict, ...], approved: tuple[dict, ...], slug: str | None
) -> list[str]:
    """Each logged change against the approvals (oldest first).

    A change matches when ANY approval holds it — a task approved twice
    executed each round under its own freeze — and is reported against
    the latest one for its target when none does.
    """
    out = []
    for row in executed:
        action = row['action'].lower()
        wants = [
            w
            for w in approved
            if _matched(slug, w, row) and w['action'].lower() == action
        ]
        if not wants:
            out.append(
                f'{_label(row)}: {action} was not in the approved changes'
            )
            continue
        if action in BID_ACTIONS:
            held = [w for w in wants if _bid_matches(row, w)]
            if not held:
                out.append(
                    f'{_label(row)}: bid set to {row.get("to_bid")}, '
                    f'approved {wants[-1]["target_bid"]}'
                )
                continue
            wants = held
        out.extend(_writeback_mismatch(slug, wants[-1], row))
    return out


def _writeback_mismatch(slug: str | None, want: dict, row: dict) -> list[str]:
    """The per-campaign TSV must record what the log says was applied."""
    if not slug or row['action'].lower() not in BID_ACTIONS + ('pause',):
        return []
    path, _ = campaign_files(
        slug, want['platform'], want['country'], want['campaign_id']
    )
    table = read_table(path, TARGETING_COLUMNS) if path else None
    if not table or table.error:
        return []
    # Neither the drilled rows nor the log carry platform/country: the
    # approval row is where they are located from.
    at = {
        'campaign_id': want['campaign_id'],
        'platform': want['platform'],
        'country': want['country'],
    }
    for t in table.rows:
        if not _matched(slug, {**t, **at}, row):
            continue
        if t.get('applied_action', '').lower() != row['action'].lower():
            return [
                f'{_label(row)}: {path.name} records applied_action '
                f'"{t.get("applied_action")}", the log says {row["action"]}'
            ]
        return []
    return []


def _started(task_id: str) -> datetime | None:
    raw = task_started_at(task_id)
    try:
        when = datetime.fromisoformat(raw) if raw else None
    except ValueError:
        return None
    if when is not None and when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when


def check(final_result: str, task_id: str, rules: dict | None = None):
    rules = {**DEFAULT_RULES, **(rules or {})}
    d = task_dir(task_id)
    changes = read_table(d / CHANGES_FILE, CHANGE_COLUMNS)
    executed = read_table(d / EXECUTION_LOG_FILE, EXECUTION_COLUMNS)
    if changes is None and executed is None:
        return None
    slug = store_slug_for(task_id)
    # Same round for all; two weights for what is left after it. A broken
    # store rule in a PROPOSAL spends nothing — the user sees it and
    # decides (their value wins over a default) — so it ships beside the
    # proposal. What touched money already, or a file the checks cannot
    # read, is a contradiction: it also gets the banner.
    problems: list[str] = []
    advisories: list[str] = []
    for table in (changes, executed):
        if table is not None and table.error:
            problems.append(table.error)
    decl = ad_declaration.load_declaration(task_id)
    if changes is not None and not changes.error:
        proposals = [r for r in changes.rows if r['action'].lower() != 'hold']
        if decl and decl['kind'] == KIND_INVESTIGATE and proposals:
            problems.append(
                f'declared investigate (read-only) but {CHANGES_FILE} '
                f'proposes {len(proposals)} change(s): drop them, or '
                're-declare kind "audit" with the same or a narrower scope'
            )
        # The owner rules shape what an audit PROPOSES. Once the user has
        # approved and the phase executes, the frozen approval is their
        # decision and wins over a default (a cut inside the cooldown, a
        # bid under the floor) — checked below, not re-judged here.
        if not (decl and decl['kind'] == KIND_EXECUTE):
            done = (
                executed.rows
                if executed is not None and not executed.error
                else ()
            )
            advisories.extend(
                rule_violations(
                    changes.rows,
                    rules,
                    slug,
                    done,
                    _started(task_id),
                    family_execution_rows(task_id),
                )
            )
    if executed is not None and not executed.error and executed.rows:
        files = approval_files(task_id)
        if not files:
            problems.append(
                f'{EXECUTION_LOG_FILE} records changes but no approved '
                f'{CHANGES_FILE} was frozen: declare kind "execute" after '
                f'the user approves, with {CHANGES_FILE} matching what '
                'they approved'
            )
        else:
            approved: list[dict] = []
            for f in files:
                table = read_table(f, CHANGE_COLUMNS)
                approved.extend(table.rows if table else ())
            problems.extend(
                execution_violations(executed.rows, tuple(approved), slug)
            )
    if not problems and not advisories:
        return None
    reason = (
        'These ad changes break a store rule or the approval. If a file '
        'is wrong, correct it to what is true and call set_task_result '
        'again. Never make another live change to satisfy this check: '
        'when what is live differs from what was approved, ask the user '
        'whether to keep or revert it, and record what they decide.'
    )
    if advisories:
        reason += (
            ' A proposal that breaks a store rule because the user asked '
            'for it stays as they said; say so in the result, where the '
            'rule it breaks is shown beside it.'
        )
    findings = problems + advisories
    return GateDeny(
        gate=GATE_NAME,
        reason=reason + '\n' + '\n'.join(f'- {p}' for p in findings[:20]),
        gaps=tuple(findings),
        contradictions=tuple(problems),
    )
