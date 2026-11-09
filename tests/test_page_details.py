import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pydantic import ValidationError

from beantracker.adapters.pages import parse_parameter_list
from beantracker.adapters.shopify import ShopifyAdapter
from beantracker.config import RoasterConfig, load_roasters
from beantracker.domain.raw import RawProduct, RawVariant
from beantracker.http.client import PoliteClient
from beantracker.http.throttle import DomainThrottle
from beantracker.normalize.attributes import parse_roast, parse_roast_label
from beantracker.normalize.origin import clean_region
from beantracker.normalize.pipeline import normalize

FIXTURES = Path(__file__).parent / "fixtures" / "september"
NOW = datetime(2026, 11, 9, tzinfo=UTC)
BASE = "https://september.test"


def page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


BUTTERCREAM = page("product_buttercream.html")
NEON = page("product_neon_sorbet.html")
CLOUDBURST = page("product_cloudburst.html")


# --- reading the page ---------------------------------------------------------------------


def test_the_spec_block_of_a_real_page_is_read_as_written() -> None:
    d = parse_parameter_list(BUTTERCREAM)
    assert d["origin"] == "Colombia"
    assert d["region"] == "Cauca, Colombia"
    assert d["varietal"] == "Castillo"
    assert d["process"] == "Advanced Anaerobic Washed with Thermal Shock"
    assert d["roast"] == "Medium - Light"
    assert d["altitude"] == "1930 MASL"
    assert d["producer"] == "Diego Bermudez"
    assert d["farm"] == "Finca El Paraiso"


def test_a_page_without_a_roast_level_just_has_no_roast_key() -> None:
    d = parse_parameter_list(NEON)
    assert d["origin"] == "Colombia"
    assert d["region"] == "Piendamó, Cauca, Colombia"
    assert d["varietal"] == "Pink Bourbon"
    assert "roast" not in d


def test_cloudburst_page() -> None:
    d = parse_parameter_list(CLOUDBURST)
    assert (d["origin"], d["region"], d["process"], d["roast"]) == (
        "Colombia",
        "Huila",
        "Washed",
        "Medium",
    )


@pytest.mark.parametrize(
    "html",
    [
        "",
        "<html><body><p>A hat.</p></body></html>",  # merchandise: no list at all
        '<ul class="parameter-list"><li>no label here</li></ul>',
        '<ul class="parameter-list"><li><span class="par-name">Country</span>: </li></ul>',
    ],
)
def test_pages_without_usable_details_give_nothing(html: str) -> None:
    assert parse_parameter_list(html) == {}


def test_the_first_value_of_a_repeated_label_wins() -> None:
    html = (
        '<ul class="parameter-list">'
        '<li><span class="par-name">Country</span>: Kenya</li>'
        '<li><span class="par-name">Country</span>: Rwanda</li></ul>'
    )
    assert parse_parameter_list(html)["origin"] == "Kenya"


# --- turning what the page says into catalog values ------------------------------------------


@pytest.mark.parametrize(
    ("label", "roast"),
    [
        ("Medium - Light", "medium-light"),
        ("Light - Medium", "medium-light"),
        ("Light", "light"),
        ("Extra-Light", "light"),
        ("Medium", "medium"),
        ("Medium - Dark", "medium-dark"),
        ("Dark", "dark"),
        ("Filter", None),
        ("", None),
    ],
)
def test_roast_level_labels(label: str, roast: str | None) -> None:
    assert parse_roast_label(label) == roast


def test_medium_light_roast_in_prose_needs_the_word_roast() -> None:
    assert parse_roast("a medium-light roast with sweetness") == "medium-light"
    assert parse_roast("a medium light body and bright acidity") is None


@pytest.mark.parametrize(
    ("value", "country", "region"),
    [
        ("Cauca, Colombia", "Colombia", "Cauca"),
        ("Piendamó, Cauca, Colombia", "Colombia", "Cauca"),
        ("Huila", "Colombia", "Huila"),
        ("Narino, Colombia", "Colombia", "Nariño"),  # canonical spelling for a known region
        ("Oromia", "Ethiopia", "Oromia"),
        ("Colombia", "Colombia", None),  # nothing left once the country is dropped
        ("", "Colombia", None),
        (None, "Colombia", None),
        ("Somewhere", None, "Somewhere"),
    ],
)
def test_clean_region(value: str | None, country: str | None, region: str | None) -> None:
    assert clean_region(value, country) == region


def beans_from(html: str, title: str) -> Any:
    product = RawProduct(
        external_id="1",
        title=title,
        url="https://september.test/p",
        variants=[RawVariant(external_id="a", title="250g", price="30.00", available=True)],
        attributes=parse_parameter_list(html),
    )
    (bean,) = normalize(product, "september", "USD", NOW)
    return bean


def test_buttercream_becomes_a_complete_bean() -> None:
    b = beans_from(BUTTERCREAM, "Buttercream")  # the title names no country at all
    assert (b.origin_country, b.origin_region) == ("Colombia", "Cauca")
    assert b.process == "Anaerobic Washed"
    assert b.varietal == "Castillo"
    assert b.roast_level == "medium-light"


def test_neon_sorbet_keeps_unknowns_empty() -> None:
    b = beans_from(NEON, "Neon Sorbet")
    assert (b.origin_country, b.origin_region) == ("Colombia", "Cauca")
    assert b.varietal == "Pink Bourbon"
    assert b.process is None  # "Inoculation process with sugarcane decaffeination" names none
    assert b.roast_level is None


def test_cloudburst() -> None:
    b = beans_from(CLOUDBURST, "Cloudburst - Milk Espresso")
    assert (b.origin_country, b.origin_region, b.process, b.roast_level) == (
        "Colombia",
        "Huila",
        "Washed",
        "medium",
    )


# --- the adapter ----------------------------------------------------------------------------


async def _no_sleep(_: float) -> None:
    return None


def config(**options: Any) -> RoasterConfig:
    return RoasterConfig(
        slug="s", name="S", base_url=BASE, adapter="shopify", currency="USD", options=options
    )


def product_json(pid: int, title: str, handle: str, available: bool = True) -> dict[str, Any]:
    return {
        "id": pid,
        "title": title,
        "handle": handle,
        "product_type": "Coffee",
        "tags": [],
        "body_html": "",
        "variants": [
            {
                "id": pid * 10,
                "title": "250G",
                "price": "30.00",
                "grams": 250,
                "available": available,
            }
        ],
    }


FEED = [
    product_json(1, "Golden Hour", "golden-hour"),
    product_json(2, "Old Lot", "old-lot", available=False),
    product_json(3, "Gift Card", "gift-card"),
    product_json(4, "Crewneck", "crewneck"),
]
DETAILS = {"details_from_pages": {"parser": "parameter_list", "only": "in_stock"}}


def mock_site(**pages: httpx.Response) -> dict[str, respx.Route]:
    respx.get(f"{BASE}/robots.txt").respond(404)
    respx.get(f"{BASE}/products.json", params={"limit": "250", "page": "1"}).respond(
        200, json={"products": FEED}
    )
    return {h: respx.get(f"{BASE}/products/{h}").mock(return_value=r) for h, r in pages.items()}


def fetcher(**options: Any) -> tuple[ShopifyAdapter, PoliteClient]:
    client = PoliteClient("t", throttle=DomainThrottle(0, sleep=_no_sleep), max_retries=0)
    return ShopifyAdapter(config(**options), client), client


@respx.mock
async def test_only_products_in_stock_get_their_page_read() -> None:
    routes = mock_site(
        **{
            "golden-hour": httpx.Response(200, text=BUTTERCREAM),
            "old-lot": httpx.Response(200, text=NEON),
            "crewneck": httpx.Response(200, text="<html>a sweatshirt</html>"),
            "gift-card": httpx.Response(200, text="<html></html>"),
        }
    )
    adapter, client = fetcher(exclude_title_keywords=["gift card"], **DETAILS)
    products = {p.title: p for p in await adapter.fetch()}
    await client.aclose()

    assert set(products) == {"Golden Hour", "Old Lot", "Crewneck"}  # the gift card is excluded
    assert products["Golden Hour"].attributes["origin"] == "Colombia"
    assert products["Old Lot"].attributes == {}  # sold out: not read
    assert products["Crewneck"].attributes == {}  # read, but a page without a spec block
    assert routes["golden-hour"].call_count == 1
    assert routes["crewneck"].call_count == 1
    assert routes["old-lot"].call_count == 0
    assert routes["gift-card"].call_count == 0  # excluded products cost no request


@respx.mock
async def test_only_all_reads_sold_out_products_too() -> None:
    routes = mock_site(
        **{
            "golden-hour": httpx.Response(200, text=BUTTERCREAM),
            "old-lot": httpx.Response(200, text=NEON),
            "gift-card": httpx.Response(200, text=""),
            "crewneck": httpx.Response(200, text=""),
        }
    )
    adapter, client = fetcher(details_from_pages={"parser": "parameter_list", "only": "all"})
    products = {p.title: p for p in await adapter.fetch()}
    await client.aclose()
    assert products["Old Lot"].attributes["origin"] == "Colombia"
    assert routes["old-lot"].call_count == 1


@respx.mock
async def test_without_the_option_no_product_page_is_requested() -> None:
    routes = mock_site(**{"golden-hour": httpx.Response(200, text=BUTTERCREAM)})
    adapter, client = fetcher()
    await adapter.fetch()
    await client.aclose()
    assert routes["golden-hour"].call_count == 0


@respx.mock
async def test_one_unreadable_page_costs_that_product_its_details_not_the_run() -> None:
    mock_site(
        **{
            "golden-hour": httpx.Response(404),
            "crewneck": httpx.Response(200, text=CLOUDBURST),
            "gift-card": httpx.Response(200, text="<html></html>"),
        }
    )
    adapter, client = fetcher(**DETAILS)
    products = {p.title: p for p in await adapter.fetch()}
    await client.aclose()
    assert products["Golden Hour"].attributes == {}  # kept, without details
    assert products["Crewneck"].attributes["origin"] == "Colombia"


@respx.mock
async def test_every_page_failing_is_an_error_not_a_quiet_degradation() -> None:
    mock_site(
        **{
            "golden-hour": httpx.Response(500),
            "crewneck": httpx.Response(500),
            "gift-card": httpx.Response(500),
        }
    )
    adapter, client = fetcher(**DETAILS)
    with pytest.raises(RuntimeError, match="any of 3 products"):
        await adapter.fetch()
    await client.aclose()


@respx.mock
async def test_robots_forbidding_product_pages_is_that_same_error() -> None:
    respx.get(f"{BASE}/robots.txt").respond(text="User-agent: *\nDisallow: /products/")
    respx.get(f"{BASE}/products.json", params={"limit": "250", "page": "1"}).respond(
        200, json={"products": FEED}
    )
    adapter, client = fetcher(**DETAILS)
    with pytest.raises(RuntimeError):
        await adapter.fetch()
    await client.aclose()


@respx.mock
async def test_the_handle_convention_beats_the_pages_roast_level() -> None:
    feed = [product_json(1, "Golden Hour Light", "golden-hour-light")]
    respx.get(f"{BASE}/robots.txt").respond(404)
    respx.get(f"{BASE}/products.json", params={"limit": "250", "page": "1"}).respond(
        200, json={"products": feed}
    )
    respx.get(f"{BASE}/products/golden-hour-light").respond(text=BUTTERCREAM)  # "Medium - Light"
    adapter, client = fetcher(roast_from_handle={"-light": "light"}, **DETAILS)
    (product,) = await adapter.fetch()
    await client.aclose()
    (bean,) = normalize(product, "s", "USD", NOW)
    assert bean.roast_level == "light"  # not "medium-light"


@respx.mock
async def test_page_details_survive_a_re_parse_of_the_stored_payload() -> None:
    mock_site(**{"golden-hour": httpx.Response(200, text=BUTTERCREAM)})
    adapter, client = fetcher(exclude_title_keywords=["gift card", "crewneck"], **DETAILS)
    products = {p.title: p for p in await adapter.fetch()}
    await client.aclose()
    golden = products["Golden Hour"]
    assert golden.raw["page_details"]["origin"] == "Colombia"
    again = adapter._parse(json.loads(json.dumps(golden.raw)))  # as read back from the database
    assert again is not None
    assert again.attributes == golden.attributes


# --- configuration ----------------------------------------------------------------------------


def test_the_option_rejects_mistakes() -> None:
    with pytest.raises(ValidationError):
        fetcher(details_from_pages={"parser": "nope"})
    with pytest.raises(ValidationError):
        fetcher(details_from_pages={"parser": "parameter_list", "only": "sometimes"})
    with pytest.raises(ValidationError):
        fetcher(details_from_pages={"parser": "parameter_list", "ony": "in_stock"})  # typo


def test_september_reads_its_in_stock_pages() -> None:
    september = load_roasters(Path("config/roasters.yaml"))["september"]
    assert september.options["details_from_pages"] == {
        "parser": "parameter_list",
        "only": "in_stock",
    }
