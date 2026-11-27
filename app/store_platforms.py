"""A store's platforms: ``platform_countries`` is the one source.

``Store.platforms`` / ``Store.countries`` are kept as its flat view. An
unknown platform is an empty list, never a guess.
"""

import json


def parse_platform_countries(raw: str | None) -> dict[str, list[str]]:
    """``Store.platform_countries`` as a dict; ``{}`` when unreadable."""
    try:
        data = json.loads(raw or '{}')
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(p): [str(c) for c in cs] if isinstance(cs, list) else []
        for p, cs in data.items()
    }


def flat_lists(pc: dict[str, list[str]]) -> tuple[list[str], list[str]]:
    """The platforms and countries a ``platform_countries`` names."""
    return list(pc), sorted({c for cs in pc.values() for c in cs})


def merge_declared(
    current: dict[str, list[str]],
    platforms: list[str] | None,
    countries: list[str] | None,
    platform_countries: dict[str, list[str]] | None,
) -> dict[str, list[str]]:
    """``platform_countries`` after a create/update payload.

    ``platform_countries`` replaces ``current``; ``platforms`` then sets
    which platforms the store is on; ``countries`` is attached when it
    names exactly one platform.
    """
    pc = dict(platform_countries if platform_countries is not None else current)
    if platforms is not None:
        pc = {p: list(pc.get(p, [])) for p in platforms if p}
    if countries is not None and len(pc) == 1:
        (only,) = pc
        pc[only] = sorted(set(pc[only]) | set(countries))
    return pc


def write(store, pc: dict[str, list[str]]) -> None:
    """Store ``pc`` and its flat view on ``store``."""
    platforms, countries = flat_lists(pc)
    store.platform_countries = json.dumps(pc)
    store.platforms = json.dumps(platforms)
    store.countries = json.dumps(countries)


def migrate_defaulted_platforms(conn) -> None:
    """Rebuild every store's flat lists from ``platform_countries``.

    A store with no ``platform_countries`` whose list is the old
    ``["amazon"]`` default gets empty lists; any other list it has is
    moved into ``platform_countries``. Idempotent.
    """
    rows = conn.exec_driver_sql(
        'SELECT id, platforms, countries, platform_countries FROM stores'
    ).fetchall()
    for store_id, raw_p, raw_c, raw_pc in rows:
        pc = parse_platform_countries(raw_pc)
        if not pc:
            platforms = _list(raw_p)
            if platforms != ['amazon']:
                pc = merge_declared({}, platforms, _list(raw_c), None)
        new_p, new_c = flat_lists(pc)
        new = (json.dumps(new_p), json.dumps(new_c), json.dumps(pc))
        if (raw_p, raw_c, raw_pc) != new:
            conn.exec_driver_sql(
                'UPDATE stores SET platforms = ?, countries = ?, '
                'platform_countries = ? WHERE id = ?',
                (*new, store_id),
            )


def _list(raw: str | None) -> list[str]:
    try:
        data = json.loads(raw or '[]')
    except (ValueError, TypeError):
        return []
    return [str(x) for x in data] if isinstance(data, list) else []
