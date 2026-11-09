"""La Cabra and The Barn: two Shopify roasters added by configuration alone.

The fixtures are real products captured from their live feeds (coffees that must be kept next
to the non-coffee items that must not be), so a change to the shipped config that starts
dropping a coffee, or listing a kettle, fails here.
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from beantracker.adapters.shopify import ShopifyAdapter
from beantracker.config import load_roasters
from beantracker.domain.bean import Bean
from beantracker.domain.raw import RawProduct
from beantracker.http.client import PoliteClient
from beantracker.normalize.pipeline import normalize

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 9, tzinfo=UTC)
ROASTERS = load_roasters(Path("config/roasters.yaml"))


def read(slug: str) -> tuple[list[RawProduct], list[str]]:
    """(products kept, titles dropped) after the roaster's configured filtering."""
    adapter = ShopifyAdapter(ROASTERS[slug], PoliteClient("t"))
    items = json.loads((FIXTURES / slug / "products_selected.json").read_text(encoding="utf-8"))
    kept: list[RawProduct] = []
    dropped: list[str] = []
    for item in items["products"]:
        product = adapter._parse(item)
        (kept if product is not None else dropped).append(product or item["title"])  # type: ignore[arg-type]
    return kept, dropped


def beans(slug: str) -> dict[str, list[Bean]]:
    cfg = ROASTERS[slug]
    kept, _ = read(slug)
    return {p.title: normalize(p, slug, cfg.currency, NOW) for p in kept}


def first(slug: str, title: str) -> Bean:
    return beans(slug)[title][0]


# --- La Cabra (DKK) ---------------------------------------------------------------------


def test_la_cabra_lists_its_coffees_and_drops_everything_else() -> None:
    kept, dropped = read("lacabra")
    assert sorted(p.title for p in kept) == sorted(
        ["Esmeralda", "Chevas Natural", "Haji Bule", "Karimikui", "Las Flores de Cafe", "Rituals"]
        + ["Profiles"]
    )
    assert sorted(dropped) == sorted(
        [
            "Gift Card",  # gift card
            "V60 Dripper - Wood",  # equipment
            "La Cabra T-shirt - Black",  # apparel
            "Jose Guevara - Single Serve",  # single-serve bags, not beans
            "K.H. Würtz Cup - Justicia",  # a cup
            "Rare Set",  # coffee bundle
            "Ground Chocolate - Bejofo",  # chocolate
            "Profiles Subscription - Gift",  # subscription gift card
            "Heven Glass",  # EQUIPMENT, upper case: matching ignores case
        ]
    )


def test_la_cabra_prices_are_in_danish_kroner() -> None:
    assert {b.currency for bs in beans("lacabra").values() for b in bs} == {"DKK"}
    assert ROASTERS["lacabra"].currency == "DKK"


def test_a_multi_pack_is_weighed_as_the_sum_of_its_bags() -> None:
    # "Rituals" sells 2 x 250g: priced per 100 g it must be 329 DKK / 500 g, not / 250 g.
    rituals = {b.weight_g: b for b in beans("lacabra")["Rituals"]}
    assert 500 in rituals
    assert rituals[500].price_minor == 32900
    assert str(rituals[500].price_per_100g) == "65.80"


def test_la_cabra_origins_from_its_short_prose() -> None:
    assert first("lacabra", "Esmeralda").origin_country == "Panama"  # named in the text
    chevas = first("lacabra", "Chevas Natural")
    assert (chevas.origin_country, chevas.origin_region) == ("Panama", "Boquete")  # a known place
    assert first("lacabra", "Chevas Natural").process == "Natural"  # from the title
    haji = first("lacabra", "Haji Bule")
    assert (haji.origin_country, haji.origin_region) == ("Ethiopia", "Guji")
    flores = first("lacabra", "Las Flores de Cafe")
    assert (flores.origin_country, flores.origin_region) == ("Guatemala", "Huehuetenango")


def test_blends_and_unnamed_origins_stay_empty() -> None:
    assert first("lacabra", "Profiles").origin_country is None  # rotating lots
    assert first("lacabra", "Rituals").origin_country is None
    assert first("lacabra", "Karimikui").origin_country is None  # only a station name


# --- The Barn (EUR) ----------------------------------------------------------------------


def test_the_barn_lists_its_coffees_and_drops_everything_else() -> None:
    kept, dropped = read("thebarn")
    assert sorted(p.title for p in kept) == sorted(
        [
            "SANTA TERESA",
            "CABALLERO BLACK EDITION",
            "LOS PIRINEOS ANAEROBIC",
            "ATLAS COFFEE",
            "ANNIVERSARY COFFEE",
            "IVAN DOS SANTOS",
            "HIMALAYA MARAGOGYPE",
            "LA COLINA WASHED",
        ]
    )
    assert sorted(dropped) == sorted(
        [
            "HIMALAYA TWIN BOX",  # coffee bundle
            "ROASTERS CHOICE SUBSCRIPTION",
            "INSPIRATION COFFEE ADVENT CALENDAR",  # no product type: dropped by keyword
            "MATCHA BOWL",
            "GUAVA BANANA SAMPLE 35g",  # priced 0.00
            "TOTE BAG: LOVE IN A CUP",
            "FELLOW STAGG EKG KETTLE",
            "ACAIA LUNAR SCALE",
            "SHOPPER BAG",  # merchandise
            "DIGITAL GIFT CARD",  # vouchers
            "WORLD ATLAS OF COFFEE",  # book
            "GENESIS COFFEE & KINTO BUNDLE",  # a coffee sold with a cup
        ]
    )


def test_the_barn_prices_are_in_euros_and_weights_come_from_the_variant_title() -> None:
    santa = beans("thebarn")["SANTA TERESA"]
    assert {b.currency for b in santa} == {"EUR"}
    # The feed reports 260 g for the 250 g bag (packaging); the title wins.
    assert 250 in {b.weight_g for b in santa}
    assert 260 not in {b.weight_g for b in santa}


def test_every_n_x_m_pack_at_the_barn_is_weighed_as_n_times_m() -> None:
    checked = 0
    for slug_beans in beans("thebarn").values():
        for b in slug_beans:
            raw = next(
                v.title
                for p in read("thebarn")[0]
                if p.external_id == b.external_id
                for v in p.variants
                if v.external_id == b.variant_external_id
            )
            m = re.fullmatch(r"(\d+) x (\d+)g", raw)
            if m:
                assert b.weight_g == int(m.group(1)) * int(m.group(2)), raw
                checked += 1
    assert checked >= 2  # ATLAS COFFEE and ANNIVERSARY COFFEE both have one


def test_the_barn_origins_include_nationality_words() -> None:
    assert first("thebarn", "SANTA TERESA").origin_country == "Costa Rica"
    assert first("thebarn", "SANTA TERESA").origin_region == "Tarrazú"
    assert first("thebarn", "HIMALAYA MARAGOGYPE").origin_country == "El Salvador"
    # "our Guatemalan portfolio": the country is not written, only the nationality
    assert first("thebarn", "LA COLINA WASHED").origin_country == "Guatemala"


def test_a_core_blend_that_names_several_origins_gets_none() -> None:
    assert first("thebarn", "ATLAS COFFEE").origin_country is None


@pytest.mark.parametrize("slug", ["lacabra", "thebarn"])
def test_the_new_roasters_are_plain_configuration(slug: str) -> None:
    cfg = ROASTERS[slug]
    assert cfg.adapter == "shopify"  # no new code: a YAML entry and a currency
