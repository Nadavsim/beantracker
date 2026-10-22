import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from beantracker.adapters.shopify import ShopifyAdapter
from beantracker.config import RoasterConfig
from beantracker.domain.raw import RawProduct, RawVariant
from beantracker.http.client import PoliteClient
from beantracker.normalize.attributes import parse_process, parse_roast, split_notes
from beantracker.normalize.origin import find_country, parse_origin
from beantracker.normalize.pipeline import normalize
from beantracker.normalize.price import price_per_100g, to_minor
from beantracker.normalize.text import labelled_fields
from beantracker.normalize.weight import parse_weight_g

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 22, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "grams"),
    [
        ("250g", 250),
        ("250G", 250),
        ("340 g", 340),
        ("8oz (227g)", 227),  # explicit grams beat unit conversion
        ("8oz (227g) - 10/18", 227),
        ("12 oz", 340),
        ("12oz", 340),
        ("5LB", 2268),
        ("2 lbs", 907),
        ("1kg", 1000),
        ("1 kg", 1000),
        ("0.5 kg", 500),
        ("פולים שלמים / 250 גרם", 250),
        ("פולים שלמים / 1 קילו", 1000),
        ("Default Title", None),
        ("", None),
        ("Whole bean, 2 pack", None),
    ],
)
def test_parse_weight(text: str, grams: int | None) -> None:
    assert parse_weight_g(text) == grams


@pytest.mark.parametrize(
    ("price", "currency", "minor"),
    [
        ("27.50", "USD", 2750),
        ("$27.50", "USD", 2750),
        ("58.00", "ILS", 5800),
        ("₪58", "ILS", 5800),
        ("1,234.50", "USD", 123450),
        ("27,50", "EUR", 2750),
        ("1,234", "NOK", 123400),
        ("3000", "JPY", 3000),  # zero-decimal currency
        ("227.00", "NOK", 22700),
        ("free", "USD", None),
    ],
)
def test_to_minor(price: str, currency: str, minor: int | None) -> None:
    assert to_minor(price, currency) == minor


def test_price_per_100g() -> None:
    assert price_per_100g(2750, "USD", 114) == Decimal("24.12")
    assert price_per_100g(5800, "ILS", 250) == Decimal("23.20")
    assert price_per_100g(2750, "USD", None) is None
    assert price_per_100g(2750, "USD", 0) is None


@pytest.mark.parametrize(
    ("text", "country"),
    [
        ("Sugar Dust - Colombia Geisha", "Colombia"),
        ("חדש! אתיופיה אורגה | מחוז גוג׳י", "Ethiopia"),
        ("Costa Rica Tarrazu", "Costa Rica"),
        ("Ethiopia and Kenya blend", None),  # multi-origin: do not invent one
        ("Panama Geisha", "Panama"),
        ("Peruvian roast", None),  # whole-word match only
        ("Gesha - Natural", None),
    ],
)
def test_find_country(text: str, country: str | None) -> None:
    assert find_country(text) == country


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        ("Finca El Turpial, Nariño, Colombia", ("Colombia", "Nariño")),
        ("Chelbesa, Gedeb, Ethiopia", ("Ethiopia", "Gedeb")),
        ("Brazil", ("Brazil", None)),
        ("Huila Colombia", ("Colombia", None)),
    ],
)
def test_parse_origin_labelled(origin: str, expected: tuple[str, str | None]) -> None:
    assert parse_origin(origin) == expected


def test_parse_origin_from_title_finds_region() -> None:
    assert parse_origin(None, "Huila - Colombia Washed") == ("Colombia", "Huila")
    assert parse_origin(None, "Guji Natural") == ("Ethiopia", "Guji")


@pytest.mark.parametrize(
    ("text", "process"),
    [
        ("Natural", "Natural"),
        ("Natural Danche Washing Station", "Natural"),
        ("Anaerobic Washed", "Anaerobic Washed"),
        ("Yellow Bourbon - Anaerobic Natural - Farm", "Anaerobic Natural"),
        ("Honey process", "Honey"),
        ("אתיופיה | עיבוד נטורל", "Natural"),
        ("פינק בורבון שטוף", "Washed"),
        ("Washed and Cold Room Dried", "Washed"),
        ("Moonlight Espresso", None),
    ],
)
def test_parse_process(text: str, process: str | None) -> None:
    assert parse_process(text) == process


@pytest.mark.parametrize(
    ("text", "roast"),
    [
        ("Kii AA - Kenya (Extra-Light)", "light"),
        ("[Rested/Ultralight] Landrace", "light"),
        ("light roast", "light"),
        ("קליה בינונית-כהה", "medium-dark"),
        ("קלייה בינונית | מתאים לאספרסו", "medium"),
        ("a light body and bright acidity", None),
        ("Muchagara AB", None),
    ],
)
def test_parse_roast(text: str, roast: str | None) -> None:
    assert parse_roast(text) == roast


def test_labelled_fields_and_notes() -> None:
    text = (
        "Tastes Like: Blue Raspberry Candy, Passion Fruit "
        "Origin: Finca X, Nariño, Colombia Variety: Laurina"
    )
    fields = labelled_fields(text)
    assert fields["origin"] == "Finca X, Nariño, Colombia"
    assert fields["variety"] == "Laurina"
    assert split_notes(fields["tastes like"]) == ["Blue Raspberry Candy", "Passion Fruit"]


def test_labelled_value_does_not_swallow_trailing_prose() -> None:
    text = "Variety: 74110, 74165 Importer: Crop to Cup. " + "Musa's coffee is lovely. " * 80
    fields = labelled_fields(text)
    assert fields["variety"] == "74110, 74165 Importer: Crop to Cup"[: len(fields["variety"])]
    assert len(fields["variety"]) <= 120


def _fixture_products(slug: str, base: str, currency: str) -> list[RawProduct]:
    # Parse via the adapter's own _parse so tests exercise real Shopify payloads.
    cfg = RoasterConfig(slug=slug, name=slug, base_url=base, adapter="shopify", currency=currency)
    adapter = ShopifyAdapter(cfg, PoliteClient("t"))
    items = json.loads((FIXTURES / slug / "products_page1.json").read_text(encoding="utf-8"))
    return [p for i in items["products"] if (p := adapter._parse(i)) is not None]


def test_hydrangea_real_payload() -> None:
    products = _fixture_products("hydrangea", "https://hydrangea.coffee", "USD")
    beans = normalize(products[0], "hydrangea", "USD", NOW)
    small, large = beans
    assert (small.weight_g, large.weight_g) == (114, 227)  # title beats the bogus grams (134/247)
    assert small.price_minor == 2750
    assert small.origin_country == "Colombia"
    assert small.origin_region == "Nariño"
    assert small.process == "Natural"
    assert small.varietal == "Laurina"
    assert small.tasting_notes[:2] == ["Blue Raspberry Candy", "Passion Fruit"]
    assert small.price_per_100g == Decimal("24.12")


def test_nomena_hebrew_ils_and_grind_variants_collapse() -> None:
    products = _fixture_products("nomena", "https://nomenaroasters.com", "ILS")
    beans = normalize(products[0], "nomena", "ILS", NOW)
    # ~20 grind variants collapse to the two sizes.
    assert [(b.weight_g, b.price_minor) for b in beans] == [(250, 5800), (1000, 23200)]
    assert beans[0].currency == "ILS"
    assert beans[0].origin_country == "Ethiopia"
    assert beans[0].origin_region == "Guji"
    assert beans[0].process == "Natural"


def test_september_title_weight_beats_wrong_grams() -> None:
    products = _fixture_products("september", "https://september.coffee", "USD")
    moonlight = next(p for p in products if p.title.startswith("Moonlight"))
    sizes = {b.weight_g: b for b in normalize(moonlight, "september", "USD", NOW)}
    assert set(sizes) == {250, 2268}  # '5LB' variant reports grams=250 but is 2268 g
    assert sizes[2268].in_stock is False
    assert sizes[250].origin_country == "Nicaragua"


def test_unparseable_price_variant_is_skipped() -> None:
    product = RawProduct(
        external_id="1",
        title="X Colombia",
        url="https://x.test/p",
        variants=[RawVariant(external_id="a", title="250g", price="n/a", available=True)],
    )
    assert normalize(product, "x", "USD", NOW) == []
