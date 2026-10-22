from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from beantracker.adapters.registry import ADAPTERS
from beantracker.cli import app
from beantracker.config import RoasterConfig
from beantracker.domain.raw import RawProduct, RawVariant
from beantracker.http.client import PoliteClient

runner = CliRunner()

PRODUCTS = [
    RawProduct(
        external_id="1",
        title="Lot One - Colombia Washed",
        url="https://fake.test/p/1",
        variants=[RawVariant(external_id="a", title="250g", price="25.00", available=True)],
    )
]


class FakeAdapter:
    slug = "fake"

    def __init__(self, config: RoasterConfig, client: PoliteClient) -> None:
        pass

    async def fetch(self) -> list[RawProduct]:
        return PRODUCTS


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setitem(ADAPTERS, "fake", FakeAdapter)
    path = tmp_path / "roasters.yaml"
    path.write_text(
        "roasters:\n"
        "  - slug: fake\n    name: Fake\n    base_url: https://fake.test\n"
        "    adapter: fake\n    currency: USD\n",
        encoding="utf-8",
    )
    return path


def test_help_runs() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "scrape" in result.output


def test_unknown_roaster_exits_2(config_file: Path) -> None:
    result = runner.invoke(app, ["scrape", "nope", "--config", str(config_file)])
    assert result.exit_code == 2
    assert "Unknown roaster" in result.output


def test_no_store_prints_beans_and_needs_no_database(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An unreachable DB proves --no-store never touches it.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:x@127.0.0.1:1/none")
    result = runner.invoke(app, ["scrape", "fake", "--no-store", "--config", str(config_file)])
    assert result.exit_code == 0, result.output
    assert "Lot One" in result.output
    assert "not stored" in result.output
    assert "250 g" in result.output


@pytest.mark.integration
def test_scrape_stores_and_runs_list_shows_it(
    config_file: Path, sessions: sessionmaker[Session]
) -> None:
    result = runner.invoke(app, ["scrape", "fake", "--config", str(config_file)])
    assert result.exit_code == 0, result.output
    assert "ok" in result.output
    assert "baseline" in result.output
    listing = runner.invoke(app, ["runs", "list"])
    assert listing.exit_code == 0
    assert "fake" in listing.output
