"""Gate: proposed and executed ad changes obey the store's rules.

Reads the fixed-header TSVs (``ad_tables``), never the report prose.
Everything here concerns money, so every finding is a *contradiction*:
refused repeatedly, and banner-marked rather than silently accepted if a
run ends without resolving it (see ``routers/tasks.set_task_result``).

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
   an approved CHANGES.tsv row (frozen when the audit was accepted, so it
   cannot be edited mid-execution), bids within 0.01 (the live
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
from app.ai.stop_gates.ad_task import parent_task_id, store_slug_for, task_dir
from app.config import DATA_DIR
from app.models.ad_declaration import KIND_INVESTIGATE

GATE_NAME = 'ad_changes'
APPROVED_DIR = DATA_DIR / 'approved_changes'
BID_TOLERANCE = 0.01


def _key(row: dict) -> tuple[str, str, str, str]:
    return (
        row.get('campaign_id', ''),
        row.get('ad_group', '').lower(),
        row.get('target', '').lower(),
        row.get('match_type', '').lower(),
    )


def _label(row: dict) -> str:
    return (
        f'{row.get("campaign_id")} / {row.get("target")} '
        f'({row.get("match_type") or "-"})'
    )


# ── approval snapshot ───────────────────────────────────────────────


def approved_path(task_id: str) -> Path:
    return APPROVED_DIR / f'{task_id}.tsv'


def freeze_approved_changes(task_id: str) -> Path | None:
    """Copy the task's CHANGES.tsv out of the agent's reach.

    Called when an audit's result is accepted: that is the list the user
    reviews and approves. Execution is checked against this copy, so
    editing CHANGES.tsv during execution changes nothing.
    """
    src = task_dir(task_id) / CHANGES_FILE
    if not src.exists():
        return None
    APPROVED_DIR.mkdir(parents=True, exist_ok=True)
    dest = approved_path(task_id)
    shutil.copyfile(src, dest)
    return dest


def find_approved(task_id: str) -> Path | None:
    """The frozen approval for this task, or its nearest ancestor's."""
    seen: set[str] = set()
    cur: str | None = task_id
    while cur and cur not in seen and len(seen) < 5:
        seen.add(cur)
        p = approved_path(cur)
        if p.exists():
            return p
        cur = parent_task_id(cur)
    return None


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
        if _key({**t, 'campaign_id': row['campaign_id']}) != _key(row):
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


def rule_violations(
    rows: tuple[dict, ...], rules: dict, slug: str | None
) -> list[str]:
    acos_floor = rules['acos_no_lower']
    roas_equiv = 100.0 / acos_floor if acos_floor else None
    out = []
    for row in rows:
        action = row['action'].lower()
        if action not in ACTIONS:
            out.append(
                f'{_label(row)}: action "{row["action"]}" — use one of '
                f'{" / ".join(ACTIONS)}'
            )
            continue
        acos, roas = number(row.get('acos')), number(row.get('roas'))
        cpc, target = number(row.get('cpc')), number(row.get('target_bid'))
        if action == 'lower' and (
            (acos is not None and acos < acos_floor)
            or (
                acos is None
                and roas is not None
                and roas_equiv
                and roas > roas_equiv
            )
        ):
            out.append(
                f'{_label(row)}: lower with ACOS {row.get("acos") or "-"} / '
                f'ROAS {row.get("roas") or "-"} — under {acos_floor:g}% ACOS '
                'a bid is only raised or held'
            )
        if action in BID_ACTIONS:
            if target is None:
                out.append(f'{_label(row)}: {action} without a target_bid')
            elif cpc:
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


def execution_violations(
    executed: tuple[dict, ...], approved: tuple[dict, ...], slug: str | None
) -> list[str]:
    by_key = {_key(r): r for r in approved}
    out = []
    for row in executed:
        action = row['action'].lower()
        want = by_key.get(_key(row))
        if want is None or want['action'].lower() != action:
            out.append(
                f'{_label(row)}: {action} was not in the approved changes'
            )
            continue
        if action in BID_ACTIONS:
            got, target = number(row.get('to_bid')), number(want['target_bid'])
            if (
                got is None
                or target is None
                or abs(got - target) > (BID_TOLERANCE)
            ):
                out.append(
                    f'{_label(row)}: bid set to {row.get("to_bid")}, approved '
                    f'{want["target_bid"]}'
                )
        out.extend(_writeback_mismatch(slug, want, row))
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
    for t in table.rows:
        if _key({**t, 'campaign_id': want['campaign_id']}) != _key(row):
            continue
        if t.get('applied_action', '').lower() != row['action'].lower():
            return [
                f'{_label(row)}: {path.name} records applied_action '
                f'"{t.get("applied_action")}", the log says {row["action"]}'
            ]
        return []
    return []


def check(final_result: str, task_id: str, rules: dict | None = None):
    rules = {**DEFAULT_RULES, **(rules or {})}
    d = task_dir(task_id)
    changes = read_table(d / CHANGES_FILE, CHANGE_COLUMNS)
    executed = read_table(d / EXECUTION_LOG_FILE, EXECUTION_COLUMNS)
    if changes is None and executed is None:
        return None
    slug = store_slug_for(task_id)
    problems: list[str] = []
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
        problems.extend(rule_violations(changes.rows, rules, slug))
    if executed is not None and not executed.error and executed.rows:
        approved_file = find_approved(task_id)
        if approved_file is None:
            problems.append(
                f'{EXECUTION_LOG_FILE} records changes but no approved '
                f'{CHANGES_FILE} exists for this task or its parent'
            )
        else:
            approved = read_table(approved_file, CHANGE_COLUMNS)
            problems.extend(
                execution_violations(
                    executed.rows, approved.rows if approved else (), slug
                )
            )
    if not problems:
        return None
    return GateDeny(
        gate=GATE_NAME,
        reason=(
            'These ad changes break a store rule or the approval. Fix the '
            'TSV (or revert the live change and correct the log), then call '
            'set_task_result again:\n'
            + '\n'.join(f'- {p}' for p in problems[:20])
        ),
        gaps=tuple(problems),
        contradictions=tuple(problems),
    )
