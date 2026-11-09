import asyncio
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, NoReturn

import typer
import yaml
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table
from sqlalchemy.orm import Session, sessionmaker

from beantracker import repo
from beantracker.adapters.base import RoasterAdapter
from beantracker.adapters.registry import ADAPTERS, build_adapter
from beantracker.config import RoasterConfig, Settings, load_roasters
from beantracker.db import make_engine, make_session_factory
from beantracker.http.client import PoliteClient
from beantracker.models.orm import RunStatus
from beantracker.runner import RunResult, collect, run_scrape

# Exit codes are the contract for cron and CI:
#   0 every run ok (warnings too, unless --strict)    1 a run failed (or warned, with --strict)
#   2 the command itself was wrong: unknown slug, unreadable or invalid config, unknown adapter
EXIT_RUN_FAILED = 1
EXIT_USAGE = 2

app = typer.Typer(help="Specialty coffee bean tracker.", no_args_is_help=True)
db_app = typer.Typer(help="Database commands.", no_args_is_help=True)
runs_app = typer.Typer(help="Scrape run history.", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(runs_app, name="runs")

console = Console()

ConfigOpt = Annotated[Path, typer.Option("--config", help="Roaster config file.")]
CacheOpt = Annotated[bool, typer.Option("--cache", help="Cache responses on disk (dev only).")]
StrictOpt = Annotated[
    bool, typer.Option("--strict", help="Exit 1 for warning runs too, not only failed ones.")
]


def _usage_error(message: str) -> NoReturn:
    console.print(f"[red]{message}")
    raise typer.Exit(EXIT_USAGE)


def _load_config(path: Path) -> dict[str, RoasterConfig]:
    try:
        return load_roasters(path)
    except FileNotFoundError:
        _usage_error(f"Config file not found: {path}")
    except (yaml.YAMLError, ValidationError, KeyError, TypeError) as exc:
        _usage_error(f"Invalid config {path}: {exc}")


def _check_adapters(roasters: Iterable[RoasterConfig], path: Path) -> None:
    """Refuse to start when a config names an adapter that does not exist, before any
    scraping, so a typo is a clear usage error and not a traceback halfway through."""
    unknown = sorted({r.adapter for r in roasters} - ADAPTERS.keys())
    if unknown:
        _usage_error(
            f"Unknown adapter(s) {', '.join(unknown)} in {path}. "
            f"Known: {', '.join(sorted(ADAPTERS))}"
        )


def _client(cache: bool) -> PoliteClient:
    settings = Settings()
    cache_dir = Path(".http_cache") if cache else settings.cache_dir
    return PoliteClient(settings.user_agent, settings.request_delay_s, cache_dir)


def _exit_for(results: list[RunResult], strict: bool) -> None:
    unhealthy = {RunStatus.failed, RunStatus.warning} if strict else {RunStatus.failed}
    if any(r.status in unhealthy for r in results):
        raise typer.Exit(EXIT_RUN_FAILED)


@app.callback()
def main() -> None:
    """Root command group."""


@app.command()
def scrape(
    slug: str,
    no_store: Annotated[
        bool, typer.Option("--no-store", help="Fetch live and print only; write nothing.")
    ] = False,
    reset_baseline: Annotated[
        bool,
        typer.Option(
            "--reset-baseline",
            help="Accept this run as the new normal: skip the count-drop check and restart "
            "the baseline from it. Use after a roaster really changed its catalog size.",
        ),
    ] = False,
    config: ConfigOpt = Path("config/roasters.yaml"),
    cache: CacheOpt = False,
    strict: StrictOpt = False,
) -> None:
    """Scrape one roaster. With --no-store this is the manual live check."""
    if no_store and reset_baseline:
        _usage_error("--reset-baseline needs a stored run; drop --no-store.")
    roasters = _load_config(config)
    if slug not in roasters:
        _usage_error(f"Unknown roaster {slug!r}. Known: {', '.join(sorted(roasters))}")
    cfg = roasters[slug]
    _check_adapters([cfg], config)

    engine = None if no_store else make_engine()
    try:
        sessions = make_session_factory(engine) if engine is not None else None
        result = asyncio.run(_scrape_one(cfg, cache, sessions, reset_baseline))
    finally:
        if engine is not None:
            engine.dispose()
    _exit_for([result] if result is not None else [], strict)


@app.command("scrape-all")
def scrape_all(
    config: ConfigOpt = Path("config/roasters.yaml"),
    cache: CacheOpt = False,
    strict: StrictOpt = False,
) -> None:
    """Scrape every configured roaster and store the results.

    Each roaster is isolated: an unexpected error in one is reported and the rest still run.
    """
    roasters = _load_config(config)
    _check_adapters(roasters.values(), config)
    engine = make_engine()
    sessions = make_session_factory(engine)
    results: list[RunResult] = []
    try:
        for cfg in roasters.values():
            try:
                result = asyncio.run(_scrape_one(cfg, cache, sessions, False))
            except Exception as exc:  # not KeyboardInterrupt: Ctrl+C must still stop everything
                error = f"{type(exc).__name__}: {exc}"
                console.print(f"[bold]{cfg.slug}[/bold]: [red]failed[/red] before it could run")
                console.print(f"  error: {error}")
                result = RunResult(None, RunStatus.failed, error=error)
            if result is not None:
                results.append(result)
    finally:
        engine.dispose()
    _exit_for(results, strict)


async def _scrape_one(
    cfg: RoasterConfig,
    cache: bool,
    sessions: sessionmaker[Session] | None,
    reset_baseline: bool,
) -> RunResult | None:
    """`sessions=None` is the live check: print what would be stored, store nothing."""
    client = _client(cache)
    try:
        adapter = build_adapter(cfg, client)
        if sessions is None:
            await _print_live(cfg, adapter)
            return None
        result = await run_scrape(sessions, adapter, cfg, reset_baseline=reset_baseline)
        _print_result(cfg, result)
        return result
    finally:
        await client.aclose()


async def _print_live(cfg: RoasterConfig, adapter: RoasterAdapter) -> None:
    items = await collect(adapter, cfg)
    table = Table(title=f"{cfg.name}: {len(items)} products (not stored)")
    for col in ("Name", "Size", "Price", "/100g", "Stock", "Origin", "Process"):
        table.add_column(col)
    for _, beans in items:
        for b in beans:
            table.add_row(
                b.name[:48],
                f"{b.weight_g} g" if b.weight_g else "?",
                f"{b.price_minor / 100:.2f} {b.currency}",
                f"{b.price_per_100g}" if b.price_per_100g else "?",
                "yes" if b.in_stock else "no",
                ", ".join(x for x in (b.origin_country, b.origin_region) if x) or "?",
                b.process or "?",
            )
    console.print(table)


def _print_result(cfg: RoasterConfig, r: RunResult) -> None:
    colour = {"ok": "green", "warning": "yellow", "failed": "red"}.get(r.status.value, "white")
    console.print(f"[bold]{cfg.slug}[/bold] run {r.run_id}: [{colour}]{r.status.value}[/{colour}]")
    if r.error:
        console.print(f"  error: {r.error}")
    console.print(
        f"  {r.product_count} products, {r.bean_count} sizes, "
        f"{r.snapshots} snapshots, {r.new_coffees} new"
        + (" (first run: baseline)" if r.baseline else "")
    )
    if r.flags:
        console.print(f"  health flags: {', '.join(r.flags)}")


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Apply Alembic migrations to the configured database."""
    command.upgrade(Config("alembic.ini"), "head")


@runs_app.command("list")
def runs_list(limit: int = 15) -> None:
    """Show recent scrape runs with their health status."""
    engine = make_engine()
    table = Table(title="Recent runs")
    for col in ("Run", "Roaster", "Started", "Status", "Products", "Missing", "Flags"):
        table.add_column(col)
    try:
        with make_session_factory(engine)() as session:
            for run, slug in repo.recent_runs(session, limit):
                table.add_row(
                    str(run.id),
                    slug,
                    run.started_at.strftime("%Y-%m-%d %H:%M"),
                    run.status.value,
                    str(run.product_count),
                    f"{float(run.missing_field_rate or 0):.0%}",
                    ", ".join(run.health_flags) or run.error or "",
                )
    finally:
        engine.dispose()
    console.print(table)
