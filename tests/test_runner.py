import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from beantracker import repo
from beantracker.config import RoasterConfig
from beantracker.domain.raw import RawProduct, RawVariant
from beantracker.models.orm import Coffee, Offer, OfferSnapshot, RunStatus, ScrapeRun
from beantracker.runner import run_scrape

pytestmark = pytest.mark.integration

CONFIG = RoasterConfig(
    slug="fake", name="Fake Roasters", base_url="https://fake.test", adapter="fake", currency="USD"
)


def product(pid: str, price: str = "25.00", available: bool = True) -> RawProduct:
    return RawProduct(
        external_id=pid,
        title=f"Lot {pid} - Colombia Washed",
        url=f"https://fake.test/products/{pid}",
        variants=[
            RawVariant(external_id=f"{pid}-a", title="250g", price=price, available=available),
            RawVariant(external_id=f"{pid}-b", title="1kg", price="90.00", available=available),
        ],
    )


class FakeAdapter:
    slug = "fake"

    def __init__(self, products: list[RawProduct] | Exception) -> None:
        self.products = products

    async def fetch(self) -> list[RawProduct]:
        if isinstance(self.products, Exception):
            raise self.products
        return self.products


def count(s: Session, model: type) -> int:
    return s.scalar(select(func.count()).select_from(model)) or 0


async def test_first_run_stores_everything_as_baseline(sessions: sessionmaker[Session]) -> None:
    result = await run_scrape(sessions, FakeAdapter([product("1"), product("2")]), CONFIG)
    assert result.status == RunStatus.ok
    assert (result.product_count, result.new_coffees, result.snapshots) == (2, 2, 4)
    assert result.baseline is True
    with sessions() as s:
        assert (count(s, Coffee), count(s, Offer), count(s, OfferSnapshot)) == (2, 4, 4)


async def test_second_run_adds_snapshots_not_coffees(sessions: sessionmaker[Session]) -> None:
    adapter = FakeAdapter([product("1"), product("2")])
    await run_scrape(sessions, adapter, CONFIG)
    second = await run_scrape(sessions, adapter, CONFIG)
    assert second.new_coffees == 0
    assert second.baseline is False
    with sessions() as s:
        assert (count(s, Coffee), count(s, Offer), count(s, OfferSnapshot)) == (2, 4, 8)
        per_offer = s.scalars(
            select(func.count()).select_from(OfferSnapshot).group_by(OfferSnapshot.offer_id)
        ).all()
        assert set(per_offer) == {2}


async def test_price_and_stock_history_is_kept(sessions: sessionmaker[Session]) -> None:
    await run_scrape(sessions, FakeAdapter([product("1", "25.00")]), CONFIG)
    await run_scrape(sessions, FakeAdapter([product("1", "27.50", available=False)]), CONFIG)
    with sessions() as s:
        offer = s.scalars(select(Offer).where(Offer.variant_external_id == "1-a")).one()
        history = s.execute(
            select(OfferSnapshot.price_minor, OfferSnapshot.in_stock)
            .where(OfferSnapshot.offer_id == offer.id)
            .order_by(OfferSnapshot.run_id)
        ).all()
        assert [tuple(r) for r in history] == [(2500, True), (2750, False)]


async def test_new_lot_detection(sessions: sessionmaker[Session]) -> None:
    await run_scrape(sessions, FakeAdapter([product("1")]), CONFIG)
    third = await run_scrape(sessions, FakeAdapter([product("1"), product("2")]), CONFIG)
    assert third.new_coffees == 1
    assert third.run_id is not None
    with sessions() as s:
        assert [c.external_id for c in repo.new_coffees(s, third.run_id)] == ["2"]


async def test_zero_products_is_a_failed_run(sessions: sessionmaker[Session]) -> None:
    result = await run_scrape(sessions, FakeAdapter([]), CONFIG)
    assert result.status == RunStatus.failed
    assert result.flags == ["zero_products"]


async def test_adapter_exception_is_recorded_not_raised(sessions: sessionmaker[Session]) -> None:
    result = await run_scrape(sessions, FakeAdapter(RuntimeError("boom")), CONFIG)
    assert result.status == RunStatus.failed
    assert result.error == "RuntimeError: boom"
    with sessions() as s:
        run = s.get_one(ScrapeRun, result.run_id)
        assert run.status == RunStatus.failed
        assert run.error == "RuntimeError: boom"
        assert run.finished_at is not None


async def test_count_drop_warns_and_does_not_lower_baseline(
    sessions: sessionmaker[Session],
) -> None:
    ten = [product(str(i)) for i in range(10)]
    await run_scrape(sessions, FakeAdapter(ten), CONFIG)
    dropped = await run_scrape(sessions, FakeAdapter(ten[:3]), CONFIG)
    assert dropped.status == RunStatus.warning
    assert dropped.flags == ["count_drop"]
    # The warning run (3 products) must not become the baseline: still compared with 10.
    again = await run_scrape(sessions, FakeAdapter(ten[:3]), CONFIG)
    assert again.status == RunStatus.warning
    with sessions() as s:
        assert repo.previous_ok_count(s, 1, again.run_id or 0) == 10
