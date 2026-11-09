"""Persistence. The only module that writes the snapshot tables.

Identity vs observation: `coffee` / `offer` rows are upserted by natural key, while
`offer_snapshot` is append-only per run. "New lot" is therefore a query
(coffee.first_seen_run_id == run.id) and price history is the snapshots of an offer.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Select, func, select, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session, aliased

from beantracker.config import RoasterConfig
from beantracker.domain.bean import Bean
from beantracker.domain.raw import RawProduct
from beantracker.health import BASELINE_RESET, HealthReport
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


def recent_ok_runs(
    session: Session, roaster_id: int, before_run_id: int, limit: int
) -> list[tuple[int, dict[str, float] | None]]:
    """(product_count, field_coverage) of the latest `limit` *ok* runs, newest first.

    Warning runs are excluded so one bad scrape cannot lower the baseline and mask the
    next one. History stops at the latest run flagged `baseline_reset`: after an operator
    accepts a new normal, the older runs must no longer count. Runs from before coverage was
    recorded have None.
    """
    reset_id = session.scalar(
        select(func.max(ScrapeRun.id)).where(
            ScrapeRun.roaster_id == roaster_id,
            ScrapeRun.id < before_run_id,
            ScrapeRun.health_flags.contains([BASELINE_RESET]),
        )
    )
    rows = session.execute(
        select(ScrapeRun.product_count, ScrapeRun.field_coverage)
        .where(
            ScrapeRun.roaster_id == roaster_id,
            ScrapeRun.status == RunStatus.ok,
            ScrapeRun.id < before_run_id,
            ScrapeRun.id >= (reset_id or 0),
        )
        .order_by(ScrapeRun.id.desc())
        .limit(limit)
    )
    return [(count, coverage) for count, coverage in rows]


def recent_ok_counts(
    session: Session, roaster_id: int, before_run_id: int, limit: int
) -> list[int]:
    return [c for c, _ in recent_ok_runs(session, roaster_id, before_run_id, limit)]


def fail_stale_runs(session: Session, roaster_id: int, older_than: timedelta) -> None:
    """Close out runs still 'running' after `older_than`: their process died (kill, power
    loss) before it could record a result. Runs younger than the cutoff are left alone, as
    they may be a legitimately concurrent scrape."""
    session.execute(
        update(ScrapeRun)
        .where(
            ScrapeRun.roaster_id == roaster_id,
            ScrapeRun.status == RunStatus.running,
            ScrapeRun.started_at < datetime.now(UTC) - older_than,
        )
        .values(
            status=RunStatus.failed,
            finished_at=datetime.now(UTC),
            error="Stale: the process ended before this run finished",
        )
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
    rate = report.missing_field_rate
    run.missing_field_rate = None if rate is None else Decimal(str(round(rate, 4)))
    run.health_flags = report.flags
    run.field_coverage = report.coverage or None
    run.error = error
    session.flush()


def new_coffees(session: Session, run_id: int) -> list[Coffee]:
    """Coffees first seen in this run: the data model's answer to 'what is new?'."""
    return list(session.scalars(select(Coffee).where(Coffee.first_seen_run_id == run_id)))


def _coffee_ids_seen_in(run_id: int) -> Select[int]:
    return (
        select(Offer.coffee_id)
        .join(OfferSnapshot, OfferSnapshot.offer_id == Offer.id)
        .where(OfferSnapshot.run_id == run_id)
    )


def delisted_coffees(session: Session, roaster_id: int, run_id: int) -> list[Coffee]:
    """Coffees that were on the roaster's site in its previous ok run but not in this one.

    Meant for an ok run only: a warning run is partial by definition, and missing from it
    does not mean gone. The same absence from every later ok run keeps a coffee out of
    `current_beans`; this reports the moment it dropped out.
    """
    previous = session.scalar(
        select(func.max(ScrapeRun.id)).where(
            ScrapeRun.roaster_id == roaster_id,
            ScrapeRun.status == RunStatus.ok,
            ScrapeRun.id < run_id,
        )
    )
    if previous is None:
        return []
    return list(
        session.scalars(
            select(Coffee).where(
                Coffee.roaster_id == roaster_id,
                Coffee.id.in_(_coffee_ids_seen_in(previous)),
                Coffee.id.not_in(_coffee_ids_seen_in(run_id)),
            )
        )
    )


def current_beans(
    session: Session,
    *,
    roaster: str | None = None,
    country: str | None = None,
    process: str | None = None,
    search: str | None = None,
    in_stock: bool | None = None,
    include_delisted: bool = False,
    limit: int | None = None,
) -> list[Bean]:
    """The catalog as it stands now: the latest snapshot of every size, cheapest per 100 g first.

    "Now" means seen in the roaster's latest *ok* run. A size missing from it is delisted and
    hidden unless `include_delisted`; one that first appeared in a later warning run shows up
    after the next ok run, because a warning run is partial and proves nothing about absence.
    """
    # DISTINCT ON picks one row per offer: the one from the newest run.
    latest = aliased(
        OfferSnapshot,
        select(OfferSnapshot)
        .ext(distinct_on(OfferSnapshot.offer_id))
        .order_by(OfferSnapshot.offer_id, OfferSnapshot.run_id.desc())
        .subquery(),
    )
    query = (
        select(Coffee, Offer, latest, Roaster.slug)
        .join(Offer, Offer.coffee_id == Coffee.id)
        .join(latest, latest.offer_id == Offer.id)
        .join(Roaster, Roaster.id == Coffee.roaster_id)
        .order_by(latest.price_per_100g.asc().nulls_last(), Coffee.name, Offer.weight_g)
    )
    if not include_delisted:
        latest_ok = (
            select(func.max(ScrapeRun.id))
            .where(ScrapeRun.roaster_id == Coffee.roaster_id, ScrapeRun.status == RunStatus.ok)
            .scalar_subquery()
        )
        query = query.where(latest.run_id == latest_ok)
    if roaster:
        query = query.where(Roaster.slug == roaster)
    if country:
        query = query.where(Coffee.origin_country.ilike(country))
    if process:
        query = query.where(Coffee.process.ilike(f"%{process}%"))
    if search:
        query = query.where(Coffee.name.ilike(f"%{search}%"))
    if in_stock is not None:
        query = query.where(latest.in_stock == in_stock)
    if limit:
        query = query.limit(limit)
    return [
        Bean(
            roaster=slug,
            external_id=coffee.external_id,
            variant_external_id=offer.variant_external_id,
            name=coffee.name,
            url=coffee.url,
            origin_country=coffee.origin_country,
            origin_region=coffee.origin_region,
            process=coffee.process,
            varietal=coffee.varietal,
            roast_level=coffee.roast_level,
            tasting_notes=list(coffee.tasting_notes),
            weight_g=offer.weight_g,
            price_minor=snap.price_minor,
            currency=snap.currency,
            price_per_100g=snap.price_per_100g,
            in_stock=snap.in_stock,
            scraped_at=snap.scraped_at,
        )
        for coffee, offer, snap, slug in session.execute(query)
    ]


def recent_runs(session: Session, limit: int = 20) -> list[tuple[ScrapeRun, str]]:
    rows = session.execute(
        select(ScrapeRun, Roaster.slug)
        .join(Roaster, Roaster.id == ScrapeRun.roaster_id)
        .order_by(ScrapeRun.id.desc())
        .limit(limit)
    )
    return [(run, slug) for run, slug in rows]
