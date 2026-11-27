from datetime import UTC, datetime
import uuid

from sqlalchemy import Boolean, String, Text, event, text
from sqlalchemy.orm import Mapped, mapped_column, object_session

from app.models.base import Base
from app.store_slugs import new_store_slug


class Store(Base):
    __tablename__ = 'stores'

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    # The store's folder name: ``stores/<slug>/``, ``store-data/<slug>/``,
    # its browser-use wrapper, profile and session names. Set on insert
    # (below) and by a rename (routers/stores.py), unique — never derived
    # from the name at runtime. See app/store_slugs.py. Nullable in the
    # DDL only so that an older version, if rolled back to, can still
    # insert rows (the boot migration gives those a slug on the next
    # upgrade).
    slug: Mapped[str | None] = mapped_column(
        String, nullable=True, unique=True, index=True
    )
    browser_backend: Mapped[str] = mapped_column(
        String, nullable=False, default='chrome'
    )
    browser_config: Mapped[str] = mapped_column(
        Text, nullable=False, default='{}'
    )
    ziniao_account_id: Mapped[str | None] = mapped_column(String, nullable=True)
    browser_oauth: Mapped[str | None] = mapped_column(String, nullable=True)
    platforms: Mapped[str] = mapped_column(Text, nullable=False, default='[]')
    countries: Mapped[str] = mapped_column(Text, nullable=False, default='[]')
    platform_countries: Mapped[str] = mapped_column(
        Text, nullable=False, default='{}'
    )
    # Per-platform capability flags, e.g.
    # ``{"amazon": {"fba": true, "ads": false}, "noon": {"fbn": true}}``.
    # Distinct from ``platform_countries``, which says *where* a store
    # sells, not *what* it can produce: a store may sell on a marketplace
    # while having no FBA business and no ads account, and demanding those
    # reports of it failed a real scheduled run. Read via
    # ``app.deliverables.manifest.store_capabilities``, where an absent key
    # means "undeclared" (deliverables optional) rather than false.
    capabilities: Mapped[str] = mapped_column(
        Text, nullable=False, default='{}'
    )
    config: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Binding to an external Amazon Ads service, as ``{marketplace:
    # store_key}``. One store commonly carries several: an Ads profile is
    # per (advertiser, marketplace), so a store advertising on two
    # marketplaces has two keys and a report has to name which one.
    #
    # The keys are the service's identifiers and are written only by
    # ``ads_client.sync_stores``; nothing here derives them from
    # ``countries`` or ``platform_countries``, which record where a store
    # SELLS rather than where it advertises.
    ads_store_keys: Mapped[str] = mapped_column(
        Text, nullable=False, default='{}'
    )
    ads_authorized: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )
    updated_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )


@event.listens_for(Store, 'before_insert')
def _assign_slug(_mapper, connection, target: Store) -> None:
    """Every store row gets its slug here — the one place one is made.

    Taken slugs are the stored ones plus those of stores pending in the
    same flush (their INSERTs have not run yet).
    """
    if target.id is None:
        target.id = str(uuid.uuid4())
    if target.slug:
        return
    taken = {
        row[0]
        for row in connection.execute(
            text('SELECT slug FROM stores WHERE slug IS NOT NULL')
        )
    }
    session = object_session(target)
    if session is not None:
        taken |= {
            obj.slug
            for obj in session.new
            if isinstance(obj, Store) and obj.slug and obj is not target
        }
    target.slug = new_store_slug(target.name, target.id, taken)
