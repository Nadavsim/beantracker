from pathlib import Path

import httpx
import pytest
import respx

from beantracker.adapters.shopify import ShopifyAdapter
from beantracker.config import RoasterConfig
from beantracker.http.client import PoliteClient
from beantracker.http.throttle import DomainThrottle

FIXTURES = Path(__file__).parent / "fixtures"


async def _no_sleep(_: float) -> None:
    return None


def adapter_for(slug: str, base: str, **options: object) -> tuple[ShopifyAdapter, PoliteClient]:
    cfg = RoasterConfig(
        slug=slug, name=slug, base_url=base, adapter="shopify", currency="USD", options=options
    )
    client = PoliteClient("test-agent", throttle=DomainThrottle(0, sleep=_no_sleep))
    return ShopifyAdapter(cfg, client), client


def mock_site(base: str, slug: str) -> None:
    respx.get(f"{base}/robots.txt").respond(404)
    page1 = (FIXTURES / slug / "products_page1.json").read_text(encoding="utf-8")
    respx.get(f"{base}/products.json", params={"limit": "250", "page": "1"}).respond(
        200, text=page1
    )


@respx.mock
async def test_fetch_parses_products_and_variants() -> None:
    mock_site("https://hydrangea.coffee", "hydrangea")
    adapter, client = adapter_for("hydrangea", "https://hydrangea.coffee")
    products = await adapter.fetch()
    first = products[0]
    assert first.title == "Laurina - Natural - Finca El Turpial"
    assert first.url == "https://hydrangea.coffee/products/" + first.raw["handle"]
    assert [v.title for v in first.variants] == ["4oz (114g)", "8oz (227g)"]
    assert first.variants[0].price == "27.50"
    await client.aclose()


@respx.mock
async def test_cheap_addons_are_dropped() -> None:
    mock_site("https://september.coffee", "september")
    adapter, client = adapter_for("september", "https://september.coffee")
    titles = {p.title for p in await adapter.fetch()}
    assert "Milk" not in titles
    assert "Sweetner" not in titles
    assert "Moonlight - Nicaragua Espresso" in titles
    await client.aclose()


@respx.mock
async def test_exclusion_options() -> None:
    mock_site("https://hydrangea.coffee", "hydrangea")
    adapter, client = adapter_for(
        "hydrangea", "https://hydrangea.coffee", exclude_product_types=["rested coffee"]
    )
    products = await adapter.fetch()
    assert products
    assert all(p.product_type.lower() != "rested coffee" for p in products)
    await client.aclose()


@respx.mock
async def test_paginates_until_short_page() -> None:
    base = "https://nomenaroasters.com"
    respx.get(f"{base}/robots.txt").respond(404)
    one = (FIXTURES / "nomena" / "products_page1.json").read_text(encoding="utf-8")
    route = respx.get(f"{base}/products.json").mock(return_value=httpx.Response(200, text=one))
    adapter, client = adapter_for("nomena", base)
    products = await adapter.fetch()
    assert route.call_count == 1  # 12 items < page size => no second request
    assert len(products) == 12
    await client.aclose()


def test_unknown_adapter_name_is_an_error() -> None:
    from beantracker.adapters.registry import build_adapter

    cfg = RoasterConfig(
        slug="x", name="x", base_url="https://x.test", adapter="nope", currency="USD"
    )
    with pytest.raises(ValueError, match="Unknown adapter"):
        build_adapter(cfg, PoliteClient("t"))
