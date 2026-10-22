from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from beantracker import repo
from beantracker.adapters.base import RoasterAdapter
from beantracker.config import RoasterConfig
from beantracker.domain.bean import Bean
from beantracker.domain.raw import RawProduct
from beantracker.health import HealthReport, evaluate
from beantracker.models.orm import RunStatus, ScrapeRun
from beantracker.normalize.pipeline import normalize

Collected = list[tuple[RawProduct, list[Bean]]]


@dataclass
class RunResult:
    run_id: int | None
    status: RunStatus
    flags: list[str] = field(default_factory=list)
    product_count: int = 0
    bean_count: int = 0
    new_coffees: int = 0
    snapshots: int = 0
    baseline: bool = False  # first healthy run: every coffee is "new", so alerts should skip it
    error: str | None = None


async def collect(adapter: RoasterAdapter, config: RoasterConfig) -> Collected:
    """Fetch and normalize without touching the database (used by `scrape --no-store`)."""
    now = datetime.now(UTC)
    products = await adapter.fetch()
    return [(p, normalize(p, config.slug, config.currency, now)) for p in products]


async def run_scrape(
    sessions: sessionmaker[Session], adapter: RoasterAdapter, config: RoasterConfig
) -> RunResult:
    """One scrape run: record it, fetch, health-check, persist snapshots.

    The run row is committed before fetching so a crash leaves a visible 'running' record
    instead of silence. Fetch errors are recorded as a failed run, not raised.
    """
    with sessions.begin() as session:
        roaster = repo.upsert_roaster(session, config)
        run = repo.start_run(session, roaster)
        run_id, roaster_id = run.id, roaster.id

    try:
        items = await collect(adapter, config)
    except Exception as exc:  # any adapter failure (network, robots, parse) is a failed run
        error = f"{type(exc).__name__}: {exc}"
        with sessions.begin() as session:
            repo.finish_run(
                session,
                session.get_one(ScrapeRun, run_id),
                HealthReport(RunStatus.failed, [], 0.0),
                0,
                error,
            )
        return RunResult(run_id, RunStatus.failed, error=error)

    beans = [b for _, bs in items for b in bs]
    with sessions.begin() as session:
        run = session.get_one(ScrapeRun, run_id)
        previous = repo.previous_ok_count(session, roaster_id, run_id)
        report = evaluate(len(items), previous, beans, config.health)
        stats = repo.save_products(session, run, items)
        repo.finish_run(session, run, report, len(items))
    return RunResult(
        run_id,
        report.status,
        report.flags,
        product_count=len(items),
        bean_count=len(beans),
        new_coffees=stats.new_coffees,
        snapshots=stats.snapshots,
        baseline=previous is None and report.status == RunStatus.ok,
    )
