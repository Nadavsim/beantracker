from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from beantracker import repo
from beantracker.adapters.base import RoasterAdapter
from beantracker.config import RoasterConfig
from beantracker.domain.bean import Bean
from beantracker.domain.raw import RawProduct
from beantracker.health import (
    BASELINE_RESET,
    HealthReport,
    baseline_count,
    baseline_coverage,
    evaluate,
)
from beantracker.models.orm import RunStatus, ScrapeRun
from beantracker.normalize.pipeline import normalize

Collected = list[tuple[RawProduct, list[Bean]]]

# A run still 'running' after this long is presumed dead (process killed): the next run for
# the roaster closes it out. Well above any real scrape (DAK takes about a minute).
STALE_RUN_AFTER = timedelta(hours=2)


@dataclass
class RunResult:
    run_id: int | None
    status: RunStatus
    flags: list[str] = field(default_factory=list)
    product_count: int = 0
    bean_count: int = 0
    new_coffees: int = 0
    delisted: int = 0  # on the site in the previous ok run, absent from this one
    snapshots: int = 0
    baseline: bool = False  # first healthy run: every coffee is "new", so alerts should skip it
    error: str | None = None


async def collect(adapter: RoasterAdapter, config: RoasterConfig) -> Collected:
    """Fetch and normalize without touching the database (used by `scrape --no-store`)."""
    now = datetime.now(UTC)
    products = await adapter.fetch()
    return [(p, normalize(p, config.slug, config.currency, now)) for p in products]


def _record_failure(
    sessions: sessionmaker[Session], run_id: int, error: str, product_count: int
) -> bool:
    """Mark the run failed in a fresh transaction. Best effort: this runs inside error
    handling, so a database outage here must not mask the original error. The stale-run
    sweep on the next run is the backstop for a row left 'running'."""
    try:
        with sessions.begin() as session:
            repo.finish_run(
                session,
                session.get_one(ScrapeRun, run_id),
                HealthReport(RunStatus.failed, [], 0.0),
                product_count,
                error[:2000],
            )
    except Exception:
        return False
    return True


def _failed(
    sessions: sessionmaker[Session], run_id: int, error: str, product_count: int
) -> RunResult:
    error = error[:2000]
    if not _record_failure(sessions, run_id, error, product_count):
        error += " (could not record this failure in the database)"
    return RunResult(run_id, RunStatus.failed, product_count=product_count, error=error)


async def run_scrape(
    sessions: sessionmaker[Session],
    adapter: RoasterAdapter,
    config: RoasterConfig,
    *,
    reset_baseline: bool = False,
) -> RunResult:
    """One scrape run: record it, fetch, health-check, persist snapshots.

    The run row is committed before fetching so a crash leaves a visible 'running' record
    instead of silence. Fetch and save errors are recorded as a failed run, not raised.
    An interruption (Ctrl+C, task cancellation) is recorded the same way and then re-raised,
    so the caller still stops. `reset_baseline` skips the count-drop check and restarts the
    baseline window from this run, for when a roaster's catalog really did change size.
    """
    with sessions.begin() as session:
        roaster = repo.upsert_roaster(session, config)
        repo.fail_stale_runs(session, roaster.id, STALE_RUN_AFTER)
        run = repo.start_run(session, roaster)
        run_id, roaster_id = run.id, roaster.id

    try:
        items = await collect(adapter, config)
    except Exception as exc:  # any adapter failure (network, robots, parse) is a failed run
        return _failed(sessions, run_id, f"{type(exc).__name__}: {exc}", 0)
    except BaseException as exc:  # Ctrl+C, cancellation: record it, then keep unwinding
        _record_failure(sessions, run_id, f"Interrupted: {type(exc).__name__}", 0)
        raise

    beans = [b for _, bs in items for b in bs]
    try:
        with sessions.begin() as session:
            run = session.get_one(ScrapeRun, run_id)
            history = repo.recent_ok_runs(session, roaster_id, run_id, config.health.baseline_runs)
            baseline = None if reset_baseline else baseline_count([c for c, _ in history])
            usual_coverage = (
                None if reset_baseline else baseline_coverage([v for _, v in history if v])
            )
            report = evaluate(len(items), baseline, beans, config.health, usual_coverage)
            if reset_baseline:
                report = replace(report, flags=[*report.flags, BASELINE_RESET])
            stats = repo.save_products(session, run, items)
            repo.finish_run(session, run, report, len(items))
            # Only a healthy run can say what has gone: a partial one proves nothing.
            delisted = (
                len(repo.delisted_coffees(session, roaster_id, run_id))
                if report.status == RunStatus.ok
                else 0
            )
    except Exception as exc:
        # The save transaction rolled back; record the failure in a fresh one.
        return _failed(sessions, run_id, f"{type(exc).__name__}: {exc}", len(items))
    except BaseException as exc:
        _record_failure(sessions, run_id, f"Interrupted: {type(exc).__name__}", len(items))
        raise
    return RunResult(
        run_id,
        report.status,
        report.flags,
        product_count=len(items),
        bean_count=len(beans),
        new_coffees=stats.new_coffees,
        delisted=delisted,
        snapshots=stats.snapshots,
        baseline=baseline is None and not reset_baseline and report.status == RunStatus.ok,
    )
