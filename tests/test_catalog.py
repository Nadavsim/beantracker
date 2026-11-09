import pytest
from sqlalchemy.orm import Session, sessionmaker

from beantracker import repo
from beantracker.config import RoasterConfig
from beantracker.domain.raw import RawProduct, RawVariant
from beantracker.runner import run_scrape

pytestmark = pytest.mark.integration

CONFIG = RoasterConfig(
    slug="fake", name="Fake", base_url="https://fake.test", adapter="fake", currency="USD"
)


def coffee(
    pid: str, title: str, p250: str = "20.00", p1kg: str = "70.00", available: bool = True
) -> RawProduct:
    return RawProduct(
        external_id=pid,
        title=title,
        url=f"https://fake.test/p/{pid}",
        variants=[
            RawVariant(external_id=f"{pid}-a", title="250g", price=p250, available=available),
            RawVariant(external_id=f"{pid}-b", title="1kg", price=p1kg, available=available),
        ],
    )


class Adapter:
    slug = "fake"

    def __init__(self, products: list[RawProduct]) -> None:
        self.products = products

    async def fetch(self) -> list[RawProduct]:
        return self.products


ETHIOPIA = "Ethiopia Guji Natural"
COLOMBIA = "Colombia Huila Washed"
KENYA = "Kenya Nyeri Washed"


def lineup(**kw: object) -> list[RawProduct]:
    return [
        coffee("1", ETHIOPIA, **kw),  # type: ignore[arg-type]
        coffee("2", COLOMBIA, p250="30.00"),
        coffee("3", KENYA, available=False),
    ]


async def test_catalog_shows_the_latest_price_once_per_size(
    sessions: sessionmaker[Session],
) -> None:
    await run_scrape(sessions, Adapter(lineup()), CONFIG)
    await run_scrape(sessions, Adapter(lineup(p250="22.00")), CONFIG)
    with sessions() as s:
        beans = repo.current_beans(s)
    assert len(beans) == 6  # 3 coffees x 2 sizes, not one row per snapshot
    ethiopia_250 = next(b for b in beans if b.name == ETHIOPIA and b.weight_g == 250)
    assert ethiopia_250.price_minor == 2200  # the newer price, not the first run's 2000


async def test_catalog_is_ordered_by_price_per_100g(sessions: sessionmaker[Session]) -> None:
    await run_scrape(sessions, Adapter(lineup()), CONFIG)
    with sessions() as s:
        per100 = [b.price_per_100g for b in repo.current_beans(s)]
    assert all(p is not None for p in per100)
    assert per100 == sorted(per100)  # type: ignore[type-var]


async def test_catalog_filters(sessions: sessionmaker[Session]) -> None:
    await run_scrape(sessions, Adapter(lineup()), CONFIG)
    with sessions() as s:
        assert {b.name for b in repo.current_beans(s, country="ethiopia")} == {ETHIOPIA}
        assert {b.name for b in repo.current_beans(s, process="natural")} == {ETHIOPIA}
        assert {b.name for b in repo.current_beans(s, search="huila")} == {COLOMBIA}
        assert KENYA not in {b.name for b in repo.current_beans(s, in_stock=True)}
        assert len(repo.current_beans(s, roaster="fake", limit=2)) == 2
        assert repo.current_beans(s, roaster="other") == []


async def test_delisted_coffees_leave_the_catalog_unless_asked_for(
    sessions: sessionmaker[Session],
) -> None:
    await run_scrape(sessions, Adapter(lineup()), CONFIG)
    await run_scrape(sessions, Adapter(lineup()[:2]), CONFIG)  # Kenya is gone: 33% drop, ok
    with sessions() as s:
        assert KENYA not in {b.name for b in repo.current_beans(s)}
        assert KENYA in {b.name for b in repo.current_beans(s, include_delisted=True)}


async def test_a_coffee_first_seen_in_a_warning_run_waits_for_an_ok_run(
    sessions: sessionmaker[Session],
) -> None:
    ten = [coffee(str(i), f"Lot {i} Colombia") for i in range(10)]
    await run_scrape(sessions, Adapter(ten), CONFIG)
    warned = await run_scrape(
        sessions, Adapter([*ten[:2], coffee("99", "Brand New Brazil")]), CONFIG
    )
    assert warned.status.value == "warning"
    with sessions() as s:
        names = {b.name for b in repo.current_beans(s)}
        assert "Brand New Brazil" not in names  # a partial run proves nothing yet
        assert "Lot 5 Colombia" in names  # and the coffees it missed are not called delisted

    await run_scrape(sessions, Adapter([*ten, coffee("99", "Brand New Brazil")]), CONFIG)
    with sessions() as s:
        assert "Brand New Brazil" in {b.name for b in repo.current_beans(s)}
