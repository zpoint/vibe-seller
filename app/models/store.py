from datetime import UTC, datetime
import uuid

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Store(Base):
    __tablename__ = 'stores'

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    browser_backend: Mapped[str] = mapped_column(
        String, nullable=False, default='chrome'
    )
    browser_config: Mapped[str] = mapped_column(
        Text, nullable=False, default='{}'
    )
    ziniao_account_id: Mapped[str | None] = mapped_column(String, nullable=True)
    browser_oauth: Mapped[str | None] = mapped_column(String, nullable=True)
    platforms: Mapped[str] = mapped_column(
        Text, nullable=False, default='["amazon"]'
    )
    countries: Mapped[str] = mapped_column(
        Text, nullable=False, default='["US"]'
    )
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
