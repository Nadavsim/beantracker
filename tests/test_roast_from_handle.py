import json
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from rich.console import Console
from typer.testing import CliRunner

from beantracker.adapters.registry import validate_options
from beantracker.adapters.shopify import ShopifyAdapter
from beantracker.cli import _bean_table, app
from beantracker.config import RoasterConfig, load_roasters
from beantracker.domain.bean import Bean
from beantracker.http.client import PoliteClient
from beantracker.normalize.pipeline import normalize

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 9, tzinfo=UTC)
runner = CliRunner()


def config(**options: Any) -> RoasterConfig:
    return RoasterConfig(
        slug="n",
        name="N",
        base_url="https://n.test",
        adapter="shopify",
        currency="ILS",
        options=options,
    )


def adapter(**options: Any) -> ShopifyAdapter:
    return ShopifyAdapter(config(**options), PoliteClient("t"))


def item(handle: str, body: str = "") -> dict[str, Any]:
    return {
        "id": 1,
        "title": "Brazil Alta Catucai",
        "handle": handle,
        "product_type": "coffee",
        "tags": [],
        "body_html": body,
        "variants": [{"id": 9, "title": "250g", "price": "50.00", "grams": 0, "available": True}],
    }


def roast_of(a: ShopifyAdapter, handle: str, body: str = "") -> str | None:
    product = a._parse(item(handle, body))
    assert product is not None
    (bean,) = normalize(product, "n", "ILS", NOW)
    return bean.roast_level


def test_a_matching_handle_suffix_sets_the_roast() -> None:
    a = adapter(roast_from_handle={"-light": "light"})
    assert roast_of(a, "brazil-alta-catucai-light") == "light"
    product = a._parse(item("brazil-alta-catucai-light"))
    assert product is not None
    assert product.attributes == {"roast_level": "light"}


def test_other_handles_are_left_alone() -> None:
    a = adapter(roast_from_handle={"-light": "light"})
    assert roast_of(a, "brazil-alta-catucai") is None
    assert roast_of(a, "lighthouse") is None  # a suffix, not just the letters "light"
    assert roast_of(a, "colombia-light-copy") is None  # not at the end of the handle


def test_without_the_option_nothing_changes() -> None:
    assert roast_of(adapter(), "brazil-alta-catucai-light") is None


def test_matching_ignores_case() -> None:
    a = adapter(roast_from_handle={"-Light": "light"})
    assert roast_of(a, "BRAZIL-ALTA-LIGHT") == "light"


def test_the_longest_matching_suffix_wins() -> None:
    a = adapter(roast_from_handle={"-light": "light", "-medium-light": "medium"})
    assert roast_of(a, "x-medium-light") == "medium"
    assert roast_of(a, "x-light") == "light"


def test_the_roasters_convention_beats_guessing_from_prose() -> None:
    a = adapter(roast_from_handle={"-light": "light"})
    assert roast_of(a, "x-light", body="<p>a dark roast with chocolate</p>") == "light"
    # and without the convention, the prose is still used
    assert roast_of(a, "x", body="<p>a dark roast with chocolate</p>") == "dark"


def test_a_stored_payload_reparses_to_the_same_roast() -> None:
    a = adapter(roast_from_handle={"-light": "light"})
    product = a._parse(item("x-light"))
    assert product is not None
    again = a._parse(product.raw)  # the slim stored form keeps the handle
    assert again is not None
    assert again.attributes == product.attributes


def test_the_real_nomena_feed_sorts_into_light_and_the_rest() -> None:
    a = adapter(roast_from_handle={"-light": "light"})
    items = json.loads((FIXTURES / "nomena" / "products_page1.json").read_text(encoding="utf-8"))[
        "products"
    ]
    for raw in items:
        product = a._parse(raw)
        assert product is not None
        expected = {"roast_level": "light"} if raw["handle"].endswith("-light") else {}
        assert product.attributes == expected, raw["handle"]


def test_a_typo_in_an_option_name_is_rejected() -> None:
    with pytest.raises(ValidationError, match="exclude_product_type"):
        adapter(exclude_product_type=["gear"])  # missing the trailing s


def test_an_unknown_roast_level_is_rejected() -> None:
    with pytest.raises(ValidationError):
        adapter(roast_from_handle={"-light": "pale"})


def test_the_shipped_config_is_valid() -> None:
    roasters = load_roasters(Path("config/roasters.yaml"))
    for roaster in roasters.values():
        validate_options(roaster)  # raises on any typo
    assert roasters["nomena"].options == {"roast_from_handle": {"-light": "light"}}


def test_the_cli_rejects_a_bad_option_before_scraping(tmp_path: Path) -> None:
    cfg = tmp_path / "r.yaml"
    cfg.write_text(
        "roasters:\n"
        "  - slug: n\n    name: N\n    base_url: https://n.test\n    adapter: shopify\n"
        "    currency: ILS\n    options:\n      roast_from_handel: {'-light': light}\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["scrape", "n", "--no-store", "--config", str(cfg)])
    assert result.exit_code == 2
    assert "Invalid options" in result.output
    assert "roast_from_handel" in result.output


def test_the_catalog_table_shows_the_roast() -> None:
    bean = Bean(
        roaster="nomena",
        external_id="1",
        variant_external_id="1",
        name="Brazil Alta Catucai",
        url="https://n.test",
        weight_g=250,
        price_minor=6200,
        currency="ILS",
        roast_level="light",
        in_stock=True,
        scraped_at=NOW,
    )
    buf = StringIO()
    Console(file=buf, width=100).print(_bean_table("t", [bean], roaster_column=True))
    out = buf.getvalue()
    assert "Roast" in out
    assert "light" in out


def _september_item(title: str) -> dict[str, Any]:
    return item("some-handle") | {"title": title}


def test_september_does_not_list_its_seasoning_as_a_bean() -> None:
    september = load_roasters(Path("config/roasters.yaml"))["september"]
    a = ShopifyAdapter(september, PoliteClient("t"))
    assert a._parse(_september_item("Seasoning Coffee")) is None
    # Real coffees, including one with a similar-looking word, are untouched.
    assert a._parse(_september_item("Seasonal Blend - Colombia")) is not None
    assert a._parse(_september_item("Muchagara AB")) is not None
