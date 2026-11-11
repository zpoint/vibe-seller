"""Gate: an ad audit covered what it declared, with numbers that add up.

Reads ``CAMPAIGNS.tsv`` and the per-campaign TSVs (``ad_tables``); the
report's prose is never parsed. What a phase owes comes from its
declaration (``ad_declaration.owed_combos``), never from the shape of
its output — only ``audit`` owes coverage, and no declaration means no
inferred obligation.

Gaps (refused once, then shipped as caveats on the result):
- a marketplace the declaration owes has no rows in CAMPAIGNS.tsv
  (a 5-market store once got a 1-market audit);
- a live campaign not drilled, or drilled without both TSVs (12
  campaigns claimed, 4 files on disk);
- search terms far short of the targeting spend (noon's top-15 tab read
  instead of the export: 0.27 of the real total).

Contradictions (numbers that cannot both be true; banner-marked if left):
- search-term spend above the targeting spend it is attributed to, or a
  campaign row whose spend disagrees with its own targeting layer — the
  signature of joining one campaign's figures to another's id.
"""

from __future__ import annotations

from app.ai import ad_declaration
from app.ai.stop_gates import GateDeny
from app.ai.stop_gates.ad_rules import DEFAULT_RULES
from app.ai.stop_gates.ad_scope import load_declared_targets
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
from app.ai.stop_gates.ad_task import store_slug_for, task_dir
from app.models.ad_declaration import KIND_AUDIT

GATE_NAME = 'ad_coverage'
DECLARATION_GATE = 'ad_declaration'
LIVE_STATES = ('enabled', 'active', 'live', 'running', 'delivering')

# Names the tool exactly and says it is already loaded: observed live, an
# agent searched for it with ToolSearch, which lists only DEFERRED tools,
# read "no matching tools" as "not available", and shipped an undeclared
# audit as a caveat while the tool sat in its tool list.
MISSING_DECLARATION = (
    'No ad task was declared. Declare what this phase did with '
    'mcp__vibe-seller__vibe_seller_declare_ad_task (audit / create / '
    'execute / investigate, and which marketplaces and campaigns) — it is '
    'already in your tool list (always loaded, so ToolSearch does not show '
    'it) — then submit again.'
)


def _combo(row: dict) -> tuple[str, str]:
    return (row.get('platform', '').lower(), row.get('country', '').upper())


def _sum(rows, column: str) -> float:
    return sum(number(r.get(column)) or 0.0 for r in rows)


def _drill_problems(
    slug: str, row: dict, rules: dict
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
    for (platform, country), rows in sorted(by_combo.items()):
        if not ad_declaration.combo_in_scope(scope, platform, country):
            gaps.append(
                f'{platform} {country}: outside the declared scope — leave it '
                'out, or ask the user to widen the scope'
            )
            continue
        real = [r for r in rows if r.get('campaign_id')]
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
            live = row.get('state', '').lower() in LIVE_STATES
            if status == 'blocked':
                if not row.get('note'):
                    gaps.append(f'{label}: blocked without a note saying why')
            elif status == 'inactive':
                if live:
                    gaps.append(f'{label}: live campaign marked inactive')
            elif slug:
                g, c = _drill_problems(slug, row, rules)
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
        # Its own name, so its round is its own: an agent that declares
        # after this and resubmits still hears about coverage once.
        return GateDeny(
            gate=DECLARATION_GATE,
            # The reason tells the AGENT what to do; the gap is what
            # ships to the seller as a caveat, and an instruction is
            # noise there.
            reason=MISSING_DECLARATION,
            gaps=(
                'The agent did not declare what kind of ad task this '
                'was, so its coverage was not checked.',
            ),
        )
    if decl['kind'] != KIND_AUDIT:
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
