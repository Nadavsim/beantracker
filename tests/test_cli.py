from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from beantracker.adapters.registry import ADAPTERS
from beantracker.cli import _client, app
from beantracker.config import RoasterConfig
from beantracker.domain.raw import RawProduct, RawVariant
from beantracker.http.client import PoliteClient
from beantracker.models.orm import RunStatus
from beantracker.runner import RunResult

runner = CliRunner()

# What FakeAdapter returns this test: how many products, and an optional error to raise.
FAKE: dict[str, Any] = {"count": 1, "error": None, "calls": []}


def products(n: int) -> list[RawProduct]:
    return [
        RawProduct(
            external_id=str(i),
            title=f"Lot {i} - Colombia Washed",
            url=f"https://fake.test/p/{i}",
            variants=[RawVariant(external_id=f"{i}a", title="250g", price="25.00", available=True)],
        )
        for i in range(1, n + 1)
    ]


class FakeAdapter:
    slug = "fake"

    def __init__(self, config: RoasterConfig, client: PoliteClient) -> None:
        FAKE["calls"].append(config.slug)

    async def fetch(self) -> list[RawProduct]:
        if FAKE["error"] is not None:
            raise FAKE["error"]
        return products(FAKE["count"])


@pytest.fixture(autouse=True)
def _fake_state() -> Iterator[None]:
    FAKE.update(count=1, error=None, calls=[])
    yield


def write_config(path: Path, *entries: tuple[str, str]) -> Path:
    """entries are (slug, adapter name)."""
    lines = ["roasters:"]
    for slug, adapter in entries:
        lines += [
            f"  - slug: {slug}",
            f"    name: {slug.title()}",
            "    base_url: https://fake.test",
            f"    adapter: {adapter}",
            "    currency: USD",
        ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setitem(ADAPTERS, "fake", FakeAdapter)
    return write_config(tmp_path / "roasters.yaml", ("fake", "fake"))


def test_help_runs() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "scrape" in result.output


def test_unknown_roaster_exits_2(config_file: Path) -> None:
    result = runner.invoke(app, ["scrape", "nope", "--config", str(config_file)])
    assert result.exit_code == 2
    assert "Unknown roaster" in result.output


def test_missing_config_file_exits_2(tmp_path: Path) -> None:
    result = runner.invoke(app, ["scrape", "fake", "--config", str(tmp_path / "missing.yaml")])
    assert result.exit_code == 2
    assert "Config file not found" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_invalid_config_exits_2(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("roasters:\n  - slug: x\n", encoding="utf-8")  # name, base_url... missing
    result = runner.invoke(app, ["scrape", "x", "--config", str(bad)])
    assert result.exit_code == 2
    assert "Invalid config" in result.output


def test_unknown_adapter_exits_2_before_anything_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(ADAPTERS, "fake", FakeAdapter)
    cfg = write_config(tmp_path / "r.yaml", ("good", "fake"), ("typo", "shopfy"))
    result = runner.invoke(app, ["scrape-all", "--config", str(cfg)])
    assert result.exit_code == 2
    assert "shopfy" in result.output
    assert FAKE["calls"] == []  # the valid roaster was not scraped either


def test_reset_baseline_needs_a_stored_run(config_file: Path) -> None:
    result = runner.invoke(
        app, ["scrape", "fake", "--no-store", "--reset-baseline", "--config", str(config_file)]
    )
    assert result.exit_code == 2
    assert "--reset-baseline" in result.output


def test_cache_with_a_stored_run_is_refused(config_file: Path) -> None:
    result = runner.invoke(app, ["scrape", "fake", "--cache", "--config", str(config_file)])
    assert result.exit_code == 2
    assert "--no-store" in result.output
    assert FAKE["calls"] == []


def test_only_the_live_check_may_use_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))  # even a cache set in the environment
    stored = _client(cache=True, store=True)
    live = _client(cache=False, store=False)
    flagged = _client(cache=True, store=False)
    assert stored._cache_dir is None
    assert live._cache_dir == tmp_path
    assert flagged._cache_dir == Path(".http_cache")
    assert flagged._cache_ttl_s == 6 * 3600


def test_no_store_prints_beans_and_needs_no_database(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An unreachable DB proves --no-store never touches it.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:x@127.0.0.1:1/none")
    result = runner.invoke(app, ["scrape", "fake", "--no-store", "--config", str(config_file)])
    assert result.exit_code == 0, result.output
    assert "Lot 1" in result.output
    assert "not stored" in result.output
    assert "250 g" in result.output


def test_scrape_all_keeps_going_after_one_roaster_blows_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(ADAPTERS, "fake", FakeAdapter)
    cfg = write_config(tmp_path / "r.yaml", ("first", "fake"), ("second", "fake"))
    attempted: list[str] = []

    async def fake_run(sessions: Any, adapter: Any, config: RoasterConfig, **_: Any) -> RunResult:
        attempted.append(config.slug)
        if config.slug == "first":
            raise RuntimeError("database unreachable")
        return RunResult(2, RunStatus.ok, product_count=1)

    monkeypatch.setattr("beantracker.cli.run_scrape", fake_run)
    result = runner.invoke(app, ["scrape-all", "--config", str(cfg)])
    assert attempted == ["first", "second"]
    assert "database unreachable" in result.output
    assert result.exit_code == 1  # one roaster failed, and the exit code says so


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


@pytest.mark.integration
def test_failed_run_exits_1(config_file: Path, sessions: sessionmaker[Session]) -> None:
    FAKE["error"] = RuntimeError("site down")
    result = runner.invoke(app, ["scrape", "fake", "--config", str(config_file)])
    assert result.exit_code == 1
    assert "site down" in result.output


@pytest.mark.integration
def test_warning_exits_0_unless_strict(config_file: Path, sessions: sessionmaker[Session]) -> None:
    args = ["scrape", "fake", "--config", str(config_file)]
    FAKE["count"] = 10
    assert runner.invoke(app, args).exit_code == 0
    FAKE["count"] = 3  # 70% drop: a warning run
    plain = runner.invoke(app, args)
    assert "warning" in plain.output
    assert plain.exit_code == 0
    assert runner.invoke(app, [*args, "--strict"]).exit_code == 1


@pytest.mark.integration
def test_reset_baseline_flag_accepts_the_new_size(
    config_file: Path, sessions: sessionmaker[Session]
) -> None:
    args = ["scrape", "fake", "--config", str(config_file)]
    FAKE["count"] = 10
    runner.invoke(app, args)
    FAKE["count"] = 3
    assert "count_drop" in runner.invoke(app, args).output

    accepted = runner.invoke(app, [*args, "--reset-baseline"])
    assert accepted.exit_code == 0
    assert "baseline_reset" in accepted.output
    again = runner.invoke(app, [*args, "--strict"])
    assert again.exit_code == 0, again.output  # 3 products is now the normal size


@pytest.mark.integration
def test_catalog_command_lists_stored_sizes(
    config_file: Path, sessions: sessionmaker[Session]
) -> None:
    args = ["scrape", "fake", "--config", str(config_file)]
    FAKE["count"] = 2
    runner.invoke(app, args)
    listing = runner.invoke(app, ["catalog", "--roaster", "fake"])
    assert listing.exit_code == 0, listing.output
    assert "Lot 1" in listing.output
    assert "2 sizes" in listing.output
    assert "0 sizes" in runner.invoke(app, ["catalog", "--origin", "Kenya"]).output
    assert "delisted" in runner.invoke(app, args).output  # the run summary reports it
