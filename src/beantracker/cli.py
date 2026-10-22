import asyncio
from pathlib import Path
from typing import Annotated

import typer
from alembic import command
from alembic.config import Config
from rich.console import Console
from rich.table import Table

from beantracker import repo
from beantracker.adapters.base import RoasterAdapter
from beantracker.adapters.registry import build_adapter
from beantracker.config import RoasterConfig, Settings, load_roasters
from beantracker.db import make_engine, make_session_factory
from beantracker.http.client import PoliteClient
from beantracker.models.orm import RunStatus
from beantracker.runner import RunResult, collect, run_scrape

app = typer.Typer(help="Specialty coffee bean tracker.", no_args_is_help=True)
db_app = typer.Typer(help="Database commands.", no_args_is_help=True)
runs_app = typer.Typer(help="Scrape run history.", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(runs_app, name="runs")

console = Console()

ConfigOpt = Annotated[Path, typer.Option("--config", help="Roaster config file.")]
CacheOpt = Annotated[bool, typer.Option("--cache", help="Cache responses on disk (dev only).")]


def _roaster(config_path: Path, slug: str) -> RoasterConfig:
    roasters = load_roasters(config_path)
    if slug not in roasters:
        console.print(f"[red]Unknown roaster {slug!r}. Known: {', '.join(sorted(roasters))}")
        raise typer.Exit(2)
    return roasters[slug]


def _client(cache: bool) -> PoliteClient:
    settings = Settings()
    cache_dir = Path(".http_cache") if cache else settings.cache_dir
    return PoliteClient(settings.user_agent, settings.request_delay_s, cache_dir)


@app.callback()
def main() -> None:
    """Root command group."""


@app.command()
def scrape(
    slug: str,
    no_store: Annotated[
        bool, typer.Option("--no-store", help="Fetch live and print only; write nothing.")
    ] = False,
    config: ConfigOpt = Path("config/roasters.yaml"),
    cache: CacheOpt = False,
) -> None:
    """Scrape one roaster. With --no-store this is the manual live check."""
    cfg = _roaster(config, slug)
    result = asyncio.run(_scrape_one(cfg, no_store, cache))
    if result is not None and result.status == RunStatus.failed:
        raise typer.Exit(1)


@app.command("scrape-all")
def scrape_all(config: ConfigOpt = Path("config/roasters.yaml"), cache: CacheOpt = False) -> None:
    """Scrape every configured roaster and store the results."""
    failed = False
    for cfg in load_roasters(config).values():
        result = asyncio.run(_scrape_one(cfg, False, cache))
        failed = failed or (result is not None and result.status == RunStatus.failed)
    if failed:
        raise typer.Exit(1)


async def _scrape_one(cfg: RoasterConfig, no_store: bool, cache: bool) -> RunResult | None:
    client = _client(cache)
    try:
        adapter = build_adapter(cfg, client)
        if no_store:
            await _print_live(cfg, adapter)
            return None
        sessions = make_session_factory(make_engine())
        result = await run_scrape(sessions, adapter, cfg)
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
    sessions = make_session_factory(make_engine())
    table = Table(title="Recent runs")
    for col in ("Run", "Roaster", "Started", "Status", "Products", "Missing", "Flags"):
        table.add_column(col)
    with sessions() as session:
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
    console.print(table)
