"""The ad-audit data contract: fixed-header TSVs the server reads.

An ad task's numbers and decisions live in tab-separated files, not in
the report's prose, so every check on them is a field comparison:

- ``CAMPAIGNS.tsv`` (task dir, audit): one row per campaign per
  marketplace — what was enumerated and how far each was drilled.
- ``<campaign_id>.tsv`` / ``<campaign_id>.searchterms.tsv`` under
  ``store-data/<slug>/ads/<platform>/<country>/``: the per-campaign
  targeting and search-term layers, plus what was applied to each row.
- ``CHANGES.tsv`` (task dir, audit): the proposed changes. The ONLY
  place a recommendation lives; the user approves this file.
- ``EXECUTION_LOG.tsv`` (task dir, execute): the changes actually made.

Headers are checked by NAME (order and extra columns are tolerated), so
older files that still carry a retired column read fine.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from app.config import VIBE_SELLER_DIR

CAMPAIGNS_FILE = 'CAMPAIGNS.tsv'
CHANGES_FILE = 'CHANGES.tsv'
EXECUTION_LOG_FILE = 'EXECUTION_LOG.tsv'

CAMPAIGN_COLUMNS = (
    'platform',
    'country',
    'campaign_id',
    'name',
    'type',
    'state',
    'currency',
    'window_start',
    'window_end',
    'spend',
    'clicks',
    'orders',
    'sales',
    'drill_status',
    'note',
)
DRILL_STATUSES = ('drilled', 'inactive', 'blocked')

TARGETING_COLUMNS = (
    'ad_group',
    'target',
    'match_type',
    'state',
    'bid',
    'currency',
    'clicks',
    'spend',
    'orders',
    'sales',
    'acos',
    'roas',
    # Written by the execution pass, never by the audit: what was done
    # to this row and when. ``previous_bid`` is the value replaced.
    'applied_action',
    'applied_at',
    'previous_bid',
)
SEARCHTERM_COLUMNS = (
    'ad_group',
    'search_term',
    'source_target',
    'match_type',
    'currency',
    'clicks',
    'spend',
    'orders',
    'sales',
    'roas',
    'applied_action',
    'applied_at',
    'previous_bid',
)

CHANGE_COLUMNS = (
    'platform',
    'country',
    'campaign_id',
    'ad_group',
    'target',
    'match_type',
    'action',
    'current_bid',
    'target_bid',
    'acos',
    'roas',
    'cpc',
    'reason',
)
ACTIONS = ('raise', 'lower', 'pause', 'negate', 'hold')
BID_ACTIONS = ('raise', 'lower')

EXECUTION_COLUMNS = (
    'campaign_id',
    'ad_group',
    'target',
    'match_type',
    'action',
    'from_bid',
    'to_bid',
    'applied_at',
)


@dataclass(frozen=True)
class Table:
    """Rows of one TSV, or the reason it could not be read."""

    rows: tuple[dict, ...] = ()
    error: str | None = None


def read_table(path: Path, columns: tuple[str, ...]) -> Table | None:
    """Read a TSV whose header must name every column in ``columns``.

    ``None`` when the file does not exist. A pipe- or comma-delimited
    file is reported, not parsed as one wide column.
    """
    try:
        text = path.read_text(encoding='utf-8-sig')
    except FileNotFoundError:
        return None
    except OSError as e:
        return Table(error=f'{path.name}: unreadable ({e})')
    lines = text.splitlines()
    if not lines or not lines[0].strip():
        return Table(error=f'{path.name}: empty')
    if '\t' not in lines[0]:
        return Table(error=f'{path.name}: not tab-separated')
    reader = csv.DictReader(lines, delimiter='\t')
    header = [h.strip().lower() for h in reader.fieldnames or []]
    missing = [c for c in columns if c not in header]
    if missing:
        return Table(error=f'{path.name}: missing columns {", ".join(missing)}')
    rows = []
    for raw in reader:
        row = {
            (k or '').strip().lower(): (v or '').strip()
            for k, v in raw.items()
            if k is not None
        }
        if any(row.values()):
            rows.append(row)
    return Table(rows=tuple(rows))


def number(value: str | None) -> float | None:
    """A cell as a float; ``None`` for blank, ``-``, or not a number.

    Tolerates a trailing ``%`` and thousands separators.
    """
    if value is None:
        return None
    v = value.replace(',', '').replace('%', '').strip()
    if v in ('', '-', '—'):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def ads_dirs(slug: str, platform: str, country: str) -> list[Path]:
    """Per-campaign TSV dirs for one marketplace, newest home first.

    ``store-data/`` is where runs write today; ``stores/<slug>/ads`` holds
    older history. Directory names are matched case-insensitively — the
    agent-created casing varies, and Linux does not fold case.
    """
    out = []
    want_p, want_c = platform.strip().lower(), country.strip().lower()
    for base in (
        VIBE_SELLER_DIR / 'store-data' / slug / 'ads',
        VIBE_SELLER_DIR / 'stores' / slug / 'ads',
    ):
        try:
            plats = [d for d in base.iterdir() if d.name.lower() == want_p]
        except OSError:
            continue
        for plat in plats:
            try:
                out.extend(
                    d
                    for d in plat.iterdir()
                    if d.is_dir() and d.name.lower() == want_c
                )
            except OSError:
                continue
    return out


def campaign_files(
    slug: str, platform: str, country: str, campaign_id: str
) -> tuple[Path | None, Path | None]:
    """``(targeting, searchterms)`` paths for a campaign, if they exist."""
    for d in ads_dirs(slug, platform, country):
        targeting = d / f'{campaign_id}.tsv'
        terms = d / f'{campaign_id}.searchterms.tsv'
        if targeting.exists() or terms.exists():
            return (
                targeting if targeting.exists() else None,
                terms if terms.exists() else None,
            )
    return None, None
