"""Builders for ad-gate tests: a task dir, its declaration and TSVs.

All figures are round and invented; ids follow the repo's placeholder
shapes (``A1234567`` campaigns, ``acme`` store).
"""

from __future__ import annotations

import json
from pathlib import Path

from app.ai.stop_gates import ad_changes, ad_coverage
from app.ai.stop_gates.ad_tables import (
    CAMPAIGN_COLUMNS,
    CHANGE_COLUMNS,
    EXECUTION_COLUMNS,
    SEARCHTERM_COLUMNS,
    TARGETING_COLUMNS,
)
from app.config import VIBE_SELLER_DIR


def write_tsv(path: Path, columns, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ['\t'.join(columns)]
    lines += ['\t'.join(str(r.get(c, '')) for c in columns) for r in rows]
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return path


class AdTask:
    """One task's workspace, with helpers to write its ad artefacts."""

    def __init__(self, task_id: str, monkeypatch, started_at: str | None):
        self.id = task_id
        self.slug = f'acme-{task_id}'
        self.dir = VIBE_SELLER_DIR / 'tasks' / task_id
        self.dir.mkdir(parents=True, exist_ok=True)
        for mod in (ad_changes, ad_coverage):
            monkeypatch.setattr(mod, 'store_slug_for', lambda _t: self.slug)
        monkeypatch.setattr(ad_changes, 'parent_task_id', lambda _t: None)
        for mod in (ad_changes, ad_coverage):
            monkeypatch.setattr(mod, 'task_started_at', lambda _t: started_at)
        self.started_at = started_at

    def declare(self, kind: str, scope: dict | None = None) -> None:
        (self.dir / 'AD_TASK.json').write_text(
            json.dumps({'kind': kind, 'scope': scope or {}})
        )

    def targets(self, combos: list[tuple[str, str]]) -> None:
        (self.dir / 'AUDIT_TARGETS.json').write_text(
            json.dumps({
                'combos': [{'platform': p, 'country': c} for p, c in combos],
                'slug': self.slug,
            })
        )

    def changes(self, rows: list[dict]) -> Path:
        return write_tsv(self.dir / 'CHANGES.tsv', CHANGE_COLUMNS, rows)

    def execution(self, rows: list[dict]) -> Path:
        return write_tsv(
            self.dir / 'EXECUTION_LOG.tsv', EXECUTION_COLUMNS, rows
        )

    def campaigns(self, rows: list[dict]) -> Path:
        return write_tsv(self.dir / 'CAMPAIGNS.tsv', CAMPAIGN_COLUMNS, rows)

    def ads_dir(self, platform: str = 'amazon', country: str = 'SA') -> Path:
        return (
            VIBE_SELLER_DIR
            / 'store-data'
            / self.slug
            / 'ads'
            / platform
            / country
        )

    def drill(
        self,
        campaign_id: str,
        targeting: list[dict],
        terms: list[dict],
        platform: str = 'amazon',
        country: str = 'SA',
    ) -> tuple[Path, Path]:
        d = self.ads_dir(platform, country)
        return (
            write_tsv(d / f'{campaign_id}.tsv', TARGETING_COLUMNS, targeting),
            write_tsv(
                d / f'{campaign_id}.searchterms.tsv', SEARCHTERM_COLUMNS, terms
            ),
        )


def change(**kw) -> dict:
    row = {
        'platform': 'amazon',
        'country': 'SA',
        'campaign_id': 'A1234567',
        'ad_group': 'g1',
        'target': 'widget',
        'match_type': 'exact',
        'action': 'raise',
        'current_bid': '1.00',
        'target_bid': '1.20',
        'acos': '40',
        'roas': '2.5',
        'cpc': '0.80',
        'reason': 'converting',
    }
    row.update({k: str(v) for k, v in kw.items()})
    return row
