"""Gate: an ad audit covered what it declared, with numbers that add up.

Reads ``CAMPAIGNS.tsv`` and the per-campaign TSVs (``ad_tables``); the
report's prose is never parsed. What a phase owes comes from its
declaration (``ad_declaration.owed_combos``), never from the shape of
its output — only ``audit`` owes coverage, and no declaration means no
inferred obligation.

Gaps (refused once, then shipped as caveats on the result):
- a marketplace the declaration owes has no rows in CAMPAIGNS.tsv
  (a 5-market store once got a 1-market audit);
- a marketplace lists far fewer campaigns than its recent history with
  no note saying why (a lazy list read as 20 of 45; AE declared empty
  while ten campaigns were live);
- a live campaign not drilled, or drilled without both TSVs, or with a
  targeting layer that is one aggregate row (12 campaigns claimed, 4
  files on disk; 18 of 20 single-row "drills");
- search terms far short of the targeting spend (noon's top-15 tab read
  instead of the export: 0.27 of the real total);
- ``name`` equal to the id, or TSVs older than this task (last week's
  report transcribed as this week's).

Contradictions (numbers that cannot both be true; refused like money):
- search-term spend above the targeting spend it is attributed to, or a
  campaign row whose spend disagrees with its own targeting layer — the
  signature of joining one campaign's figures to another's id.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.ai import ad_declaration
from app.ai.stop_gates import GateDeny
from app.ai.stop_gates.ad_rules import DEFAULT_RULES
from app.ai.stop_gates.ad_scope import (
    load_declared_targets,
    prior_campaign_tsvs,
)
from app.ai.stop_gates.ad_tables import (
    CAMPAIGN_COLUMNS,
    CAMPAIGNS_FILE,
    CHANGES_FILE,
    DRILL_STATUSES,
    SEARCHTERM_COLUMNS,
    TARGETING_COLUMNS,
    campaign_files,
    number,
    read_table,
)
from app.ai.stop_gates.ad_task import store_slug_for, task_dir, task_started_at
from app.models.ad_declaration import KIND_AUDIT

GATE_NAME = 'ad_coverage'
LIVE_STATES = ('enabled', 'active', 'live', 'running', 'delivering')
STALE_WINDOW_DAYS = 7
MTIME_SLACK_S = 300

MISSING_DECLARATION = (
    'No ad task was declared. Call vibe_seller_declare_ad_task (audit / '
    'create / execute / investigate, and which marketplaces and campaigns) '
    'before the work; it cannot be back-filled, so say in the result what '
    'this run actually covered.'
)


def _combo(row: dict) -> tuple[str, str]:
    return (row.get('platform', '').lower(), row.get('country', '').upper())


def _sum(rows, column: str) -> float:
    return sum(number(r.get(column)) or 0.0 for r in rows)


def _started_ts(task_id: str) -> float | None:
    raw = task_started_at(task_id)
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when.timestamp()


def _drill_problems(
    slug: str, row: dict, rules: dict, started: float | None
) -> tuple[list[str], list[str]]:
    gaps: list[str] = []
    contradictions: list[str] = []
    cid = row['campaign_id']
    label = f'{row["platform"]} {row["country"]} {cid}'
    targeting_path, terms_path = campaign_files(
        slug, row['platform'], row['country'], cid
    )
    targeting = (
        read_table(targeting_path, TARGETING_COLUMNS)
        if targeting_path
        else None
    )
    terms = read_table(terms_path, SEARCHTERM_COLUMNS) if terms_path else None
    if targeting is None or terms is None:
        missing = [
            n
            for n, t in (
                (f'{cid}.tsv', targeting),
                (f'{cid}.searchterms.tsv', terms),
            )
            if t is None
        ]
        gaps.append(f'{label}: marked drilled but {", ".join(missing)} missing')
        return gaps, contradictions
    for t in (targeting, terms):
        if t.error:
            gaps.append(f'{label}: {t.error}')
    if targeting.error or terms.error:
        return gaps, contradictions
    if started is not None:
        for p in (targeting_path, terms_path):
            if p.stat().st_mtime < started - MTIME_SLACK_S:
                gaps.append(
                    f'{label}: {p.name} predates this task — re-read the '
                    'campaign, an earlier run’s file is not this run’s data'
                )
    targets = [r for r in targeting.rows if r.get('target')]
    if len(targets) <= 1 and (number(row.get('clicks')) or 0) > 0:
        gaps.append(
            f'{label}: targeting layer has {len(targets)} row — drill per '
            'keyword/target, not a campaign total'
        )
    t_spend = _sum(targeting.rows, 'spend')
    s_spend = _sum(terms.rows, 'spend')
    c_spend = number(row.get('spend'))
    if t_spend > 0:
        ratio = s_spend / t_spend
        if ratio > 1 + rules['reconcile_tolerance']:
            contradictions.append(
                f'{label}: search-term spend {s_spend:.2f} exceeds targeting '
                f'spend {t_spend:.2f} (x{ratio:.2f}) — the two layers are '
                'from different campaigns or windows'
            )
        elif ratio < rules['noon_reconcile_floor']:
            gaps.append(
                f'{label}: search terms cover {ratio:.0%} of targeting '
                'spend — capture the full set (use the export)'
            )
    if (
        c_spend
        and t_spend
        and abs(c_spend - t_spend)
        > max(1.0, c_spend * rules['reconcile_tolerance'])
    ):
        contradictions.append(
            f'{label}: CAMPAIGNS.tsv spend {c_spend:.2f} but its targeting '
            f'layer sums to {t_spend:.2f} — figures joined to the wrong id?'
        )
    return gaps, contradictions


def _window_stale(row: dict) -> bool:
    try:
        end = date.fromisoformat(row.get('window_end', ''))
    except ValueError:
        return False
    return end < date.today() - timedelta(days=STALE_WINDOW_DAYS)


def audit_findings(
    task_id: str, decl: dict, rules: dict
) -> tuple[list[str], list[str]]:
    gaps: list[str] = []
    contradictions: list[str] = []
    table = read_table(task_dir(task_id) / CAMPAIGNS_FILE, CAMPAIGN_COLUMNS)
    if table is None:
        return [f'{CAMPAIGNS_FILE} missing — list every campaign you read'], []
    if table.error:
        return [table.error], []
    scope = decl.get('scope') or {}
    owed = ad_declaration.owed_combos(decl, load_declared_targets(task_id))
    by_combo: dict[tuple[str, str], list[dict]] = {}
    for row in table.rows:
        by_combo.setdefault(_combo(row), []).append(row)
    for combo in owed:
        key = (combo['platform'].lower(), combo['country'].upper())
        if key not in by_combo:
            gaps.append(
                f'{key[0]} {key[1]}: owed by the declaration, absent from '
                f'{CAMPAIGNS_FILE} (no campaigns? add one row with an empty '
                'campaign_id and a note)'
            )
    slug = store_slug_for(task_id)
    started = _started_ts(task_id)
    for (platform, country), rows in sorted(by_combo.items()):
        if not ad_declaration.combo_in_scope(scope, platform, country):
            gaps.append(
                f'{platform} {country}: outside the declared scope — leave it '
                'out, or ask the user to widen the scope'
            )
            continue
        real = [r for r in rows if r.get('campaign_id')]
        prior = prior_campaign_tsvs(slug, platform, country) if slug else 0
        if (
            prior
            and len(real) * 2 < prior
            and not any(r.get('note') for r in rows)
        ):
            gaps.append(
                f'{platform} {country}: {len(real)} campaign(s) listed, '
                f'{prior} in the last weeks’ history — read the full list, '
                'or note why it shrank'
            )
        for row in real:
            cid = row['campaign_id']
            if not ad_declaration.campaign_in_scope(scope, cid):
                continue
            label = f'{platform} {country} {cid}'
            status = row.get('drill_status', '').lower()
            if status not in DRILL_STATUSES:
                gaps.append(
                    f'{label}: drill_status "{status}" — use '
                    f'{" / ".join(DRILL_STATUSES)}'
                )
                continue
            if row.get('name', '').strip() in ('', cid):
                gaps.append(f'{label}: name is empty or the id itself')
            if _window_stale(row):
                gaps.append(
                    f'{label}: window_end {row.get("window_end")} is over '
                    f'{STALE_WINDOW_DAYS} days old'
                )
            live = row.get('state', '').lower() in LIVE_STATES
            if status == 'blocked':
                if not row.get('note'):
                    gaps.append(f'{label}: blocked without a note saying why')
            elif status == 'inactive':
                if live:
                    gaps.append(f'{label}: live campaign marked inactive')
            elif slug:
                g, c = _drill_problems(slug, row, rules, started)
                gaps.extend(g)
                contradictions.extend(c)
    return gaps, contradictions


def check(final_result: str, task_id: str, rules: dict | None = None):
    rules = {**DEFAULT_RULES, **(rules or {})}
    d = task_dir(task_id)
    decl = ad_declaration.load_declaration(task_id)
    has_data = (d / CAMPAIGNS_FILE).exists() or (d / CHANGES_FILE).exists()
    if decl is None:
        if not has_data:
            return None
        gaps, contradictions = [MISSING_DECLARATION], []
    elif decl['kind'] != KIND_AUDIT:
        return None
    else:
        gaps, contradictions = audit_findings(task_id, decl, rules)
    if not gaps and not contradictions:
        return None
    items = contradictions + gaps
    return GateDeny(
        gate=GATE_NAME,
        reason=(
            'The audit data is incomplete or inconsistent. Fix what you can '
            'and call set_task_result again; whatever is left ships as '
            'caveats on the result:\n' + '\n'.join(f'- {g}' for g in items[:25])
        ),
        gaps=tuple(items),
        contradictions=tuple(contradictions),
    )
