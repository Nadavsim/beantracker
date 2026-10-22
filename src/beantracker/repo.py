"""Persistence. The only module that writes the snapshot tables.

Identity vs observation: `coffee` / `offer` rows are upserted by natural key, while
`offer_snapshot` is append-only per run. "New lot" is therefore a query
(coffee.first_seen_run_id == run.id) and price history is the snapshots of an offer.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from beantracker.config import RoasterConfig
from beantracker.domain.bean import Bean
from beantracker.domain.raw import RawProduct
from beantracker.health import HealthReport
from beantracker.models.orm import Coffee, Offer, OfferSnapshot, Roaster, RunStatus, ScrapeRun


@dataclass
class SaveStats:
    new_coffees: int = 0
    snapshots: int = 0


def upsert_roaster(session: Session, config: RoasterConfig) -> Roaster:
    roaster = session.scalar(select(Roaster).where(Roaster.slug == config.slug))
    if roaster is None:
        roaster = Roaster(slug=config.slug, name="", base_url="", adapter="")
        session.add(roaster)
    roaster.name, roaster.base_url, roaster.adapter = config.name, config.base_url, config.adapter
    session.flush()
    return roaster


def start_run(session: Session, roaster: Roaster) -> ScrapeRun:
    run = ScrapeRun(roaster_id=roaster.id, status=RunStatus.running)
    session.add(run)
    session.flush()
    return run


def previous_ok_count(session: Session, roaster_id: int, before_run_id: int) -> int | None:
    """Product count of the latest *ok* run. Warning runs are excluded so one bad scrape
    cannot lower the baseline and mask the next one."""
    return session.scalar(
        select(ScrapeRun.product_count)
        .where(
            ScrapeRun.roaster_id == roaster_id,
            ScrapeRun.status == RunStatus.ok,
            ScrapeRun.id < before_run_id,
        )
        .order_by(ScrapeRun.id.desc())
        .limit(1)
    )


def save_products(
    session: Session, run: ScrapeRun, items: list[tuple[RawProduct, list[Bean]]]
) -> SaveStats:
    stats = SaveStats()
    now = datetime.now(UTC)
    for product, beans in items:
        if not beans:
            continue
        head = beans[0]
        coffee = session.scalar(
            select(Coffee).where(
                Coffee.roaster_id == run.roaster_id, Coffee.external_id == product.external_id
            )
        )
        if coffee is None:
            coffee = Coffee(
                roaster_id=run.roaster_id,
                external_id=product.external_id,
                first_seen_run_id=run.id,
                first_seen_at=now,
            )
            session.add(coffee)
            stats.new_coffees += 1
        coffee.name, coffee.url = head.name, head.url
        coffee.origin_country, coffee.origin_region = head.origin_country, head.origin_region
        coffee.process, coffee.varietal, coffee.roast_level = (
            head.process,
            head.varietal,
            head.roast_level,
        )
        coffee.tasting_notes = head.tasting_notes
        coffee.raw = product.raw  # kept so normalization can be re-run without re-scraping
        session.flush()

        offers = {
            o.variant_external_id: o
            for o in session.scalars(select(Offer).where(Offer.coffee_id == coffee.id))
        }
        for bean in beans:
            offer = offers.get(bean.variant_external_id)
            if offer is None:
                offer = Offer(coffee_id=coffee.id, variant_external_id=bean.variant_external_id)
                session.add(offer)
            offer.weight_g = bean.weight_g
            session.flush()
            session.add(
                OfferSnapshot(
                    offer_id=offer.id,
                    run_id=run.id,
                    price_minor=bean.price_minor,
                    currency=bean.currency,
                    price_per_100g=bean.price_per_100g,
                    in_stock=bean.in_stock,
                    scraped_at=bean.scraped_at,
                )
            )
            stats.snapshots += 1
    session.flush()
    return stats


def finish_run(
    session: Session,
    run: ScrapeRun,
    report: HealthReport,
    product_count: int,
    error: str | None = None,
) -> None:
    run.finished_at = datetime.now(UTC)
    run.status = report.status
    run.product_count = product_count
    run.missing_field_rate = Decimal(str(round(report.missing_field_rate, 4)))
    run.health_flags = report.flags
    run.error = error
    session.flush()


def new_coffees(session: Session, run_id: int) -> list[Coffee]:
    """Coffees first seen in this run: the data model's answer to 'what is new?'."""
    return list(session.scalars(select(Coffee).where(Coffee.first_seen_run_id == run_id)))


def recent_runs(session: Session, limit: int = 20) -> list[tuple[ScrapeRun, str]]:
    rows = session.execute(
        select(ScrapeRun, Roaster.slug)
        .join(Roaster, Roaster.id == ScrapeRun.roaster_id)
        .order_by(ScrapeRun.id.desc())
        .limit(limit)
    )
    return [(run, slug) for run, slug in rows]
