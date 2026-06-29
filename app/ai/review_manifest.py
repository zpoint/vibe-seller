"""Read-only inspection of a review-collect run's on-disk output.

The ``review-collect`` skill writes one JSON file per product plus a
``_MANIFEST.json`` index under
``~/.vibe-seller/store-data/<slug>/reviews/<platform>/<country>/``. The two
review stop-gates (``review_completeness_review``,
``review_output_gate``) both need to answer the same question — *did
this run actually collect every product it enumerated, and is each
product file well-formed?* — so the disk-walking lives here once and the
gates only format the verdict.

Why disk and not the report text: the gate's contract is
``check(result_text, task_id, rules)``, but the report is the agent's
human-facing summary and can be fabricated independently of what was
written. The collected JSON is the source of truth the ALC sync reads,
so the gates validate THAT, resolving the store slug from the task's
``store_id`` (read-only DB lookup, same pattern as
``ad_negation_allowlist``). Everything here is best-effort and never
raises — a lookup failure degrades to ``None`` (gate no-ops).

Data contract — ``reviews/v1`` (also documented in
``app/skills/review-collect/references/output-spec.md``):

``_MANIFEST.json``::

    {
        'schema': 'reviews/v1',
        'store_slug': 'example-store',
        'collected_at': '2026-06-17T09:12:00Z',
        'combos': [
            {
                'platform': 'amazon',
                'country': 'us',
                'expected': ['B0AAA', 'B0BBB'],  # enumerated universe (Layer 1)
                'collected': ['B0AAA'],  # product_ids with a JSON file
                'reviews': 218,
                'pages': 17,
            }
        ],
    }

``<platform>/<country>/<product_id>.json`` must carry ``rating`` (not
null), a ``reviews`` list, and ``collected_at``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import sqlite3

from app.browser.wrapper import store_slug
from app.config import VIBE_SELLER_DIR

SCHEMA = 'reviews/v1'
MANIFEST_NAME = '_MANIFEST.json'


def _db_path() -> Path:
    return VIBE_SELLER_DIR / 'data' / 'vibe_seller.db'


def store_slug_for_task(task_id: str) -> str | None:
    """Resolve ``task_id`` → store slug, read-only, or None.

    Mirrors ``ad_negation_allowlist`` DB access: a 2s read-only
    connection, swallow every error. Returns None when the task has no
    store (non-store tasks never run this gate meaningfully) or the DB
    is unavailable.
    """
    db = _db_path()
    if not task_id or not db.exists():
        return None
    try:
        con = sqlite3.connect(f'file:{db}?mode=ro', uri=True, timeout=2)
        try:
            row = con.execute(
                'SELECT s.name, s.id FROM tasks t '
                'JOIN stores s ON s.id = t.store_id WHERE t.id = ?',
                (task_id,),
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    if not row or not row[0]:
        return None
    try:
        return store_slug(row[0], row[1])
    except ValueError:
        return None


def reviews_dir(slug: str) -> Path:
    """``store-data/<slug>/reviews`` under the live vibe-seller dir.

    Review dumps are durable RUN DATA, so they live under ``store-data/``
    (per the workspace contract in ``app/prompts/design_system.md`` — the
    ``stores/`` tree is curated knowledge and ``store_data_migrate`` would
    relocate any run subdir out of it anyway).
    """
    return VIBE_SELLER_DIR / 'store-data' / slug / 'reviews'


def validate_product_file(
    slug: str, platform: str, country: str, product_id: str
) -> str | None:
    """Return a short defect reason, or None when the file is well-formed.

    Well-formed = readable JSON object with a numeric ``rating``, a
    ``reviews`` list, a truthy ``collected_at``, and — for noon, whose
    ratings ride the Amazon ASIN downstream — a non-empty ``asin`` (a
    noon file without it is dropped by the consumer).
    """
    path = reviews_dir(slug) / platform / country / f'{product_id}.json'
    if not path.exists():
        return '文件缺失 (missing)'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return 'JSON 无法解析 (unreadable)'
    if not isinstance(data, dict):
        return 'JSON 不是对象 (not an object)'
    rating = data.get('rating')
    # Must be a real number (a present-but-malformed string would
    # otherwise pass the old `is None` check and reach the consumer).
    if not isinstance(rating, int | float) or isinstance(rating, bool):
        return '缺 rating 或非数字 (rating missing/not a number)'
    if not isinstance(data.get('reviews'), list):
        return 'reviews 不是数组 (reviews not a list)'
    if not data.get('collected_at'):
        return '缺 collected_at'
    if platform.lower() == 'noon' and not (data.get('asin') or '').strip():
        return '缺 asin (noon 需要匹配的 Amazon ASIN)'
    return None


@dataclass
class ReviewAudit:
    """Verdict over a review-collect run's on-disk output.

    ``slug`` resolves but ``manifest_present`` is False → the agent
    loaded the skill (the gate only runs then) yet wrote no
    ``_MANIFEST.json``: a real review task that produced nothing.
    """

    slug: str
    manifest_present: bool
    total_expected: int = 0
    total_ok: int = 0
    # "amazon US: collected 12/46" lines where collected < expected.
    shortfalls: list[str] = field(default_factory=list)
    # "amazon/us/B0XXX: 缺 rating" per expected product that is
    # missing or malformed on disk.
    defects: list[str] = field(default_factory=list)


def audit_run(task_id: str) -> ReviewAudit | None:
    """Walk the run's manifest + product files, or None if not resolvable.

    None means "can't tell this is a review run" (no store slug) — the
    gate must no-op. A resolvable slug with no manifest returns a
    ``ReviewAudit`` with ``manifest_present=False`` so the gate can deny
    a review task that wrote nothing.
    """
    slug = store_slug_for_task(task_id)
    if not slug:
        return None

    manifest_path = reviews_dir(slug) / MANIFEST_NAME
    if not manifest_path.exists():
        return ReviewAudit(slug=slug, manifest_present=False)
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return ReviewAudit(slug=slug, manifest_present=False)

    out = ReviewAudit(slug=slug, manifest_present=True)
    combos = manifest.get('combos') if isinstance(manifest, dict) else None
    for combo in combos or []:
        if not isinstance(combo, dict):
            continue
        platform = str(combo.get('platform') or '')
        country = str(combo.get('country') or '')
        expected = combo.get('expected') or []
        if not isinstance(expected, list):
            continue
        # Coerce defensively: a non-list `collected` (e.g. a stray string)
        # would otherwise become a set of characters and mask a shortfall.
        raw_collected = combo.get('collected')
        collected = (
            {str(c) for c in raw_collected}
            if isinstance(raw_collected, list)
            else set()
        )
        out.total_expected += len(expected)
        if len(collected) < len(expected):
            out.shortfalls.append(
                f'{platform} {country}: collected {len(collected)}/'
                f'{len(expected)} products'
            )
        for product_id in expected:
            reason = validate_product_file(
                slug, platform, country, str(product_id)
            )
            if reason is None:
                out.total_ok += 1
            else:
                out.defects.append(
                    f'{platform}/{country}/{product_id}: {reason}'
                )
    return out
