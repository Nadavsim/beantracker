from datetime import UTC, datetime
from pathlib import Path

import respx

from beantracker.adapters.dak import DakAdapter, parse_default_weight, parse_listing
from beantracker.config import RoasterConfig
from beantracker.http.client import PoliteClient
from beantracker.http.throttle import DomainThrottle
from beantracker.normalize.pipeline import normalize

FIXTURES = Path(__file__).parent / "fixtures" / "dak"
BASE = "https://www.dakcoffeeroasters.com"
SHOP = (FIXTURES / "shop.html").read_text(encoding="utf-8")
PRODUCT = (FIXTURES / "product_jazz_fruits.html").read_text(encoding="utf-8")


def test_listing_keeps_only_coffee_category() -> None:
    cards = parse_listing(SHOP, BASE)
    assert len(cards) == 24  # 35 cards on the page; merch and food are filtered out
    assert all(c.url.startswith(f"{BASE}/product/") for c in cards)


def test_listing_card_fields() -> None:
    first = parse_listing(SHOP, BASE)[0]
    assert first.title == "Berry Blues"
    assert first.price == "€24,95"
    assert first.in_stock is True
    assert first.attributes["origin"] == "Ethiopia"
    assert first.attributes["region"] == "Oromia"
    assert first.attributes["process"] == "Anaerobic Natural"
    assert first.attributes["tasting_notes"] == "Cashew Butter, Grape Soda, Myrtle Liquor"


def test_default_weight_is_the_preselected_option() -> None:
    assert parse_default_weight(PRODUCT) == "250g"
    assert parse_default_weight("<html></html>") is None


@respx.mock
async def test_fetch_end_to_end_into_beans() -> None:
    respx.get(f"{BASE}/robots.txt").respond(404)
    respx.get(f"{BASE}/shop/").respond(200, text=SHOP)
    respx.get(url__startswith=f"{BASE}/product/").respond(200, text=PRODUCT)

    async def no_sleep(_: float) -> None:
        return None

    client = PoliteClient("t", throttle=DomainThrottle(0, sleep=no_sleep))
    cfg = RoasterConfig(slug="dak", name="DAK", base_url=BASE, adapter="dak", currency="EUR")
    products = await DakAdapter(cfg, client).fetch()
    await client.aclose()

    assert len(products) == 24
    beans = normalize(products[0], "dak", "EUR", datetime(2026, 10, 22, tzinfo=UTC))
    (bean,) = beans
    assert (bean.price_minor, bean.currency, bean.weight_g) == (2495, "EUR", 250)
    assert bean.origin_country == "Ethiopia"
    assert bean.origin_region == "Oromia"
    assert bean.process == "Anaerobic Natural"
    assert bean.varietal == "Heirloom"
    assert bean.tasting_notes == ["Cashew Butter", "Grape Soda", "Myrtle Liquor"]
