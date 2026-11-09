"""What each roaster's configured exclusions drop, and the real coffees they must not touch.

Titles come from the live catalogs (checked against the stored data before each keyword was
added), so a keyword that starts catching a real coffee fails here.
"""

from pathlib import Path
from typing import Any

import pytest

from beantracker.adapters.shopify import ShopifyAdapter
from beantracker.config import load_roasters
from beantracker.http.client import PoliteClient

ROASTERS = load_roasters(Path("config/roasters.yaml"))


def listed(slug: str, title: str) -> bool:
    adapter = ShopifyAdapter(ROASTERS[slug], PoliteClient("t"))
    item: dict[str, Any] = {
        "id": 1,
        "title": title,
        "handle": "some-handle",
        "product_type": "Coffee",
        "tags": [],
        "body_html": "",
        "variants": [{"id": 9, "title": "250g", "price": "30.00", "grams": 250, "available": True}],
    }
    return adapter._parse(item) is not None


@pytest.mark.parametrize(
    "title",
    [
        "Roaster's Choice Subscription",
        "Stickers!",
        "2025 Christmas Advent Calendar",
        "2021 WBrC Bundle",
        "Hydrangea Coffee - Online Gift Card",
    ],
)
def test_hydrangea_does_not_list_its_non_coffee_products(title: str) -> None:
    assert not listed("hydrangea", title)


@pytest.mark.parametrize(
    "title",
    [
        "Gesha - Natural - Condor Ridge Ranch, FRINJ Coffee",
        "Finca Carmen Geisha",
        "Landrace - Natural - Danche",
        "Espresso Club Mystery Coffee",  # still a coffee, only not a named bean
        "Laurina - Natural - Finca El Turpial",
    ],
)
def test_hydrangea_still_lists_its_real_coffees(title: str) -> None:
    assert listed("hydrangea", title)
