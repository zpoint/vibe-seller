"""Derive the expected deliverable set for a store-month.

Server-side and input-only: the store row plus the calendar. The agent
never authors this, which is the difference between it and the ad-task
declaration in :mod:`app.ai.ad_declaration` — an ad scope can only be
known after looking at the account, so the agent declares it; a
deliverable list is knowable before the run starts, so deriving it keeps
the denominator out of the agent's hands.

Two ideas carry the whole module.

**Capability, not guesswork.** A store that has no FBA cannot produce a
storage report, and demanding one failed a real run. Capabilities are
read from the store row; an *undeclared* capability makes its
deliverables optional — absent is fine, present is still row-checked.
That is the same fail-safe direction the ad declaration takes: no
declaration means nothing over-wide is demanded. Declaring ``fba: true``
is what turns absence into a gap.

**Accrual versus event.** Zero rows means different things per report.
Storage accrues against held inventory, so a month with inventory and
zero storage rows has not been published yet. Removals are discrete
events, so zero rows can simply be the truth. Encoding that distinction
here is what lets a checker resolve an ambiguity a reviewer cannot
resolve by eye — and it is why ``min_rows`` is a per-entry property
rather than one global rule.
"""

from __future__ import annotations

import dataclasses
import enum
import json

#: Day of M+1 from which a marketplace's monthly fee reports for month M
#: are reliably downloadable. Requests before this return no data (or an
#: empty file); the boundary is upstream's monthly billing cycle, not
#: something this project controls.
#:
#: Measured across four consecutive months on a live account: requests on
#: days 3, 6 and 7 of M+1 all returned no data, and a request on day 29
#: of M+1 returned the full month. The marketplace's own documentation
#: puts publication at days 13-14, which is consistent with both ends of
#: that bracket. Deliberately conservative: a run that requests on day 15
#: and finds nothing records a gap and completes, so being one day late
#: costs a caveat, while being early costs a whole month of wrong numbers.
FEE_PUBLISHED_FROM_DAY = 15


class DeliverableKind(enum.StrEnum):
    """How to read an empty file for this deliverable.

    ``ACCRUAL`` charges accumulate against something held all month, so
    zero rows for a month that had inventory means "not published yet".
    ``EVENT`` rows are discrete occurrences, so zero rows is a legal
    answer and only a *missing* file is a gap. ``LEDGER`` is a
    transaction record that always has rows if the store traded at all.
    """

    ACCRUAL = 'accrual'
    EVENT = 'event'
    LEDGER = 'ledger'


@dataclasses.dataclass(frozen=True)
class Deliverable:
    """One expected file.

    ``relpath`` is relative to the task workspace root. ``requires`` is a
    capability key (``'amazon.fba'``) that must not be declared false;
    ``None`` means unconditional. ``published_from_day`` is the day of the
    month *after* ``month`` from which the source is expected to carry
    data — a gap before that day is upstream latency, not a mistake.
    """

    relpath: str
    month: str
    kind: DeliverableKind
    requires: str | None = None
    min_rows: int = 1
    published_from_day: int | None = None

    @property
    def optional(self) -> bool:
        """True when absence is acceptable rather than a gap.

        An EVENT file may legitimately have no rows, but it must still be
        present — a missing file and an empty one are different claims,
        and only the empty one is evidence that the question was asked.
        """
        return False


def month_before(month: str) -> str:
    """``'2026-08'`` → ``'2026-07'``. Crosses the year boundary."""
    year, mon = (int(p) for p in month.split('-'))
    if mon == 1:
        return f'{year - 1:04d}-12'
    return f'{year:04d}-{mon - 1:02d}'


def _mon_abbr(month: str) -> str:
    """``'2026-07'`` → ``'Jul'`` (the marketplace's filename token)."""
    names = (
        'Jan',
        'Feb',
        'Mar',
        'Apr',
        'May',
        'Jun',
        'Jul',
        'Aug',
        'Sep',
        'Oct',
        'Nov',
        'Dec',
    )
    return names[int(month.split('-')[1]) - 1]


def _json_field(raw, default):
    """Parse a JSON text column, falling back to *default*."""
    if not raw:
        return default
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def store_capabilities(store) -> dict[str, bool]:
    """Declared capabilities for *store*, flattened to dotted keys.

    ``{'amazon': {'fba': true, 'ads': false}}`` becomes
    ``{'amazon.fba': True, 'amazon.ads': False}``. A key that is absent
    is *undeclared*, which is not the same as false: undeclared makes the
    dependent deliverables optional, declared-false removes them
    entirely. Only an explicit ``true`` makes absence a gap.
    """
    raw = _json_field(getattr(store, 'capabilities', None), {})
    flat: dict[str, bool] = {}
    for platform, caps in raw.items():
        if not isinstance(caps, dict):
            continue
        for cap, value in caps.items():
            if isinstance(value, bool):
                flat[f'{platform}.{cap}'] = value
    return flat


def _platform_countries(store) -> dict[str, list[str]]:
    """``{'amazon': ['SA', 'AE'], 'noon': ['SA']}``, lowercased codes."""
    raw = _json_field(getattr(store, 'platform_countries', None), {})
    out: dict[str, list[str]] = {}
    for platform, countries in raw.items():
        if isinstance(countries, list):
            out[str(platform).lower()] = [
                str(c).lower() for c in countries if str(c).strip()
            ]
    return out


def derive_manifest(
    store,
    slug: str,
    month: str,
    *,
    fee_month: str | None = None,
) -> list[Deliverable]:
    """Expected files for *store* covering *month*.

    *month* is the reporting month (``'2026-07'``) — everything that
    publishes promptly. *fee_month* is the older month whose fee reports
    have become available in the meantime; pass it to include the
    backfill entries a monthly run also owes. Passing ``fee_month ==
    month`` is legal and simply means "expect this month's fees too".

    Returns entries in a stable order so a diff between two runs reads
    cleanly.
    """
    caps = store_capabilities(store)
    per_platform = _platform_countries(store)
    out: list[Deliverable] = []

    def allowed(capability: str) -> bool:
        """False only when the capability is declared absent."""
        return caps.get(capability, True)

    mm = month.split('-')[1]
    mon = _mon_abbr(month)

    for country in per_platform.get('amazon', []):
        folder = f'reports_{mm}_{country}_{slug}'
        out.append(
            Deliverable(
                relpath=f'{folder}/{month.split("-")[0]}{mon}'
                'MonthlyTransaction.csv',
                month=month,
                kind=DeliverableKind.LEDGER,
            )
        )
        if allowed('amazon.ads'):
            out.append(
                Deliverable(
                    relpath=f'{folder}/'
                    f'Sponsored_Products_Advertised_product_report_{mon}.csv',
                    month=month,
                    kind=DeliverableKind.EVENT,
                    requires='amazon.ads',
                    min_rows=0,
                )
            )
        if allowed('amazon.fba'):
            out.append(
                Deliverable(
                    relpath=f'{folder}/return.csv',
                    month=month,
                    kind=DeliverableKind.EVENT,
                    requires='amazon.fba',
                    min_rows=0,
                )
            )

    for country in per_platform.get('noon', []):
        folder = f'reports_noon_{mm}_{slug}'
        out.append(
            Deliverable(
                relpath=f'{folder}/noon_financeweb_transactionviewreport.csv',
                month=month,
                kind=DeliverableKind.LEDGER,
            )
        )
        if allowed('noon.ads'):
            out.append(
                Deliverable(
                    relpath=f'{folder}/ads_overview_{country}_{month}.xlsx',
                    month=month,
                    kind=DeliverableKind.EVENT,
                    requires='noon.ads',
                    min_rows=0,
                )
            )

    if fee_month:
        out.extend(_fee_deliverables(slug, fee_month, per_platform, allowed))

    # Stable, and de-duplicated: the noon transaction view is one file for
    # every country, so a two-country store derives it twice above.
    seen: set[str] = set()
    unique: list[Deliverable] = []
    for entry in sorted(out, key=lambda d: (d.month, d.relpath)):
        if entry.relpath in seen:
            continue
        seen.add(entry.relpath)
        unique.append(entry)
    return unique


def _fee_deliverables(
    slug: str,
    fee_month: str,
    per_platform: dict[str, list[str]],
    allowed,
) -> list[Deliverable]:
    """The late-publishing fee reports for *fee_month*.

    These are the entries whose absence early in M+1 is expected. They
    carry ``published_from_day`` so a checker can tell "upstream has not
    posted this yet" from "somebody skipped it", which is the whole
    reason the two months are separate arguments.
    """
    out: list[Deliverable] = []
    mm = fee_month.split('-')[1]

    if allowed('amazon.fba'):
        for country in per_platform.get('amazon', []):
            out.append(
                Deliverable(
                    relpath=f'reports_{mm}_{country}_{slug}/storage.csv',
                    month=fee_month,
                    kind=DeliverableKind.ACCRUAL,
                    requires='amazon.fba',
                    published_from_day=FEE_PUBLISHED_FROM_DAY,
                )
            )

    if allowed('noon.fbn'):
        folder = f'reports_noon_{mm}_{slug}'
        # Storage accrues against held stock; removals are events. Same
        # page, same generator, different reading of an empty file.
        accrual = ('monthly_storage', 'longterm_storage', 'nonsaleable_storage')
        for country in per_platform.get('noon', []):
            for stem in accrual:
                out.append(
                    Deliverable(
                        relpath=f'{folder}/{stem}_{country}_{fee_month}.csv',
                        month=fee_month,
                        kind=DeliverableKind.ACCRUAL,
                        requires='noon.fbn',
                        published_from_day=FEE_PUBLISHED_FROM_DAY,
                    )
                )
            out.append(
                Deliverable(
                    relpath=f'{folder}/rtv_removal_{country}_{fee_month}.csv',
                    month=fee_month,
                    kind=DeliverableKind.EVENT,
                    requires='noon.fbn',
                    min_rows=0,
                    published_from_day=FEE_PUBLISHED_FROM_DAY,
                )
            )
    return out
