import asyncio
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console
from typer.testing import CliRunner

from beantracker.adapters.registry import ADAPTERS
from beantracker.cli import _bean_table, app
from beantracker.config import RoasterConfig
from beantracker.domain.bean import Bean
from beantracker.domain.raw import RawProduct
from beantracker.http.client import PoliteClient
from beantracker.models.orm import RunStatus
from beantracker.runner import RunResult

runner = CliRunner()


def write_two(path: Path) -> Path:
    path.write_text(
        "roasters:\n"
        "  - {slug: one, name: One, base_url: 'https://one.test', adapter: fake, currency: USD}\n"
        "  - {slug: two, name: Two, base_url: 'https://two.test', adapter: fake, currency: USD}\n",
        encoding="utf-8",
    )
    return path


class QuietAdapter:
    slug = "fake"

    def __init__(self, config: RoasterConfig, client: PoliteClient) -> None:
        pass

    async def fetch(self) -> list[RawProduct]:
        return []


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setitem(ADAPTERS, "fake", QuietAdapter)
    return write_two(tmp_path / "r.yaml")


def test_roasters_run_concurrently_up_to_the_limit(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    async def slow_run(sessions: Any, adapter: Any, config: RoasterConfig, **_: Any) -> RunResult:
        events.append(f"start {config.slug}")
        await asyncio.sleep(0.05)
        events.append(f"end {config.slug}")
        return RunResult(1, RunStatus.ok)

    monkeypatch.setattr("beantracker.cli.run_scrape", slow_run)
    assert runner.invoke(app, ["scrape-all", "--config", str(config_file)]).exit_code == 0
    assert events[:2] == ["start one", "start two"]  # both in flight before either ends

    events.clear()
    args = ["scrape-all", "--config", str(config_file), "--concurrency", "1"]
    assert runner.invoke(app, args).exit_code == 0
    assert events == ["start one", "end one", "start two", "end two"]


def test_concurrent_clients_share_one_throttle(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clients: list[PoliteClient] = []

    class Spy(QuietAdapter):
        def __init__(self, config: RoasterConfig, client: PoliteClient) -> None:
            clients.append(client)

    monkeypatch.setitem(ADAPTERS, "fake", Spy)

    async def fake_run(sessions: Any, adapter: Any, config: RoasterConfig, **_: Any) -> RunResult:
        return RunResult(1, RunStatus.ok)

    monkeypatch.setattr("beantracker.cli.run_scrape", fake_run)
    runner.invoke(app, ["scrape-all", "--config", str(config_file)])
    assert len(clients) == 2
    # One throttle, so two roasters on the same host still get spaced-out requests.
    assert clients[0]._throttle is clients[1]._throttle


def test_concurrency_must_be_positive(config_file: Path) -> None:
    result = runner.invoke(app, ["scrape-all", "--config", str(config_file), "--concurrency", "0"])
    assert result.exit_code == 2


def bean(name: str) -> Bean:
    return Bean(
        roaster="nomena",
        external_id="1",
        variant_external_id="1",
        name=name,
        url="https://x.test",
        weight_g=250,
        price_minor=5800,
        currency="ILS",
        in_stock=True,
        scraped_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def render(beans: list[Bean], width: int) -> list[str]:
    buf = StringIO()
    Console(file=buf, width=width).print(_bean_table("t", beans, roaster_column=True))
    return buf.getvalue().splitlines()


def test_catalog_rows_never_wrap_in_a_narrow_terminal() -> None:
    long_name = "אתיופיה " * 12
    beans = [bean(long_name), bean(f"  {long_name}  "), bean("Short")]
    # Every bean adds exactly one line: nothing wrapped onto a second row.
    assert len(render(beans, 80)) == len(render([], 80)) + len(beans)
    assert any("…" in line for line in render(beans, 80))


def test_only_the_name_gives_way_when_space_is_short() -> None:
    lines = "\n".join(render([bean("A very long coffee name " * 6)], 80))
    assert "250 g" in lines  # the size column keeps its full width
    assert "58.00 ILS" in lines  # and so does the price, with the currency's own decimals
