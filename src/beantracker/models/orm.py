"""Snapshot-based schema.

coffee   = stable identity of a product (new lot == first_seen_run_id is the current run)
offer    = a sellable size of a coffee
offer_snapshot = append-only observation of price/stock per scrape run
"""

import enum
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class RunStatus(enum.StrEnum):
    running = "running"
    ok = "ok"
    warning = "warning"
    failed = "failed"


class Roaster(Base):
    __tablename__ = "roaster"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    base_url: Mapped[str] = mapped_column(String(256))
    adapter: Mapped[str] = mapped_column(String(64))


class ScrapeRun(Base):
    __tablename__ = "scrape_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    roaster_id: Mapped[int] = mapped_column(ForeignKey("roaster.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, name="run_status"), default=RunStatus.running
    )
    product_count: Mapped[int] = mapped_column(Integer, default=0)
    missing_field_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    health_flags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    error: Mapped[str | None] = mapped_column(String)


class Coffee(Base):
    __tablename__ = "coffee"
    __table_args__ = (UniqueConstraint("roaster_id", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    roaster_id: Mapped[int] = mapped_column(ForeignKey("roaster.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(256))
    url: Mapped[str] = mapped_column(String(512))
    origin_country: Mapped[str | None] = mapped_column(String(64))
    origin_region: Mapped[str | None] = mapped_column(String(128))
    process: Mapped[str | None] = mapped_column(String(64))
    varietal: Mapped[str | None] = mapped_column(String(128))
    roast_level: Mapped[str | None] = mapped_column(String(32))
    tasting_notes: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    first_seen_run_id: Mapped[int] = mapped_column(ForeignKey("scrape_run.id"))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Kept so normalization can be re-run without re-scraping.
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    offers: Mapped[list["Offer"]] = relationship(back_populates="coffee")


class Offer(Base):
    __tablename__ = "offer"
    __table_args__ = (UniqueConstraint("coffee_id", "variant_external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    coffee_id: Mapped[int] = mapped_column(ForeignKey("coffee.id"), index=True)
    variant_external_id: Mapped[str] = mapped_column(String(128))
    weight_g: Mapped[int | None] = mapped_column(Integer)

    coffee: Mapped[Coffee] = relationship(back_populates="offers")


class OfferSnapshot(Base):
    __tablename__ = "offer_snapshot"
    __table_args__ = (UniqueConstraint("offer_id", "run_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    offer_id: Mapped[int] = mapped_column(ForeignKey("offer.id"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("scrape_run.id"), index=True)
    # Integer minor units (agorot/cents) avoid float rounding in price history.
    price_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    price_per_100g: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    in_stock: Mapped[bool] = mapped_column(Boolean)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
