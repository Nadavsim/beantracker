"""DAK Coffee Roasters (WooCommerce block theme, server-rendered HTML).

The /shop/ listing cards carry name, price, stock state and an attribute overlay (origin,
region, process, varietal, tasting notes). Weight is only on the product page, inside the
size selector's data-wp-context JSON, so each coffee costs one extra (rate-limited) request.

Known limitation: the listing shows only the default size's price, so one variant is emitted
per coffee. Other sizes need WooCommerce's Store API; deferred until it is worth it.
All CSS selectors live in this module so a markup change is a one-file fix.
"""

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from beantracker.config import RoasterConfig
from beantracker.domain.raw import RawProduct, RawVariant
from beantracker.http.client import PoliteClient

MAX_PAGES = 20


@dataclass
class ListingCard:
    external_id: str
    title: str
    url: str
    price: str
    in_stock: bool
    attributes: dict[str, str] = field(default_factory=dict)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def parse_listing(html: str, base_url: str, category: str = "coffee") -> list[ListingCard]:
    """Cards of the given WooCommerce category (default: coffee; skips merch and food)."""
    tree = LexborHTMLParser(html)
    cards: list[ListingCard] = []
    for li in tree.css("li.product"):
        classes = (li.attributes.get("class") or "").split()
        if f"product_cat-{category}" not in classes:
            continue
        link = li.css_first(".wp-block-post-title a")
        price = li.css_first(".woocommerce-Price-amount")
        if link is None or price is None or not link.attributes.get("href"):
            continue
        post_id = next((c.removeprefix("post-") for c in classes if c.startswith("post-")), "")
        attrs: dict[str, str] = {}
        for row in li.css(".ag-row"):
            label, value = row.css_first(".ag-label"), row.css_first(".ag-value")
            if label is not None and value is not None:
                key = _clean(label.text()).lower().replace(" ", "_")
                attrs[key] = _clean(value.text())
        cards.append(
            ListingCard(
                external_id=post_id,
                title=_clean(link.text()),
                url=urljoin(base_url, link.attributes["href"] or ""),
                price=_clean(price.text()),
                in_stock="outofstock" not in classes,
                attributes=attrs,
            )
        )
    return cards


def next_page_url(html: str, base_url: str) -> str | None:
    link = LexborHTMLParser(html).css_first("a.wp-block-query-pagination-next")
    href = link.attributes.get("href") if link is not None else None
    return urljoin(base_url, href) if href else None


def parse_default_weight(html: str) -> str | None:
    """Label of the pre-selected size (e.g. '250g') from the Weight selector, if present."""
    for node in LexborHTMLParser(html).css("[data-wp-context]"):
        raw = node.attributes.get("data-wp-context") or ""
        if "variationAttributeOptions" not in raw:
            continue
        try:
            ctx = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if str(ctx.get("name", "")).lower() != "weight":
            continue
        options = ctx.get("variationAttributeOptions", [])
        chosen = next((o for o in options if o.get("selected")), options[0] if options else None)
        return str(chosen["label"]) if chosen else None
    return None


class DakAdapter:
    def __init__(self, config: RoasterConfig, client: PoliteClient) -> None:
        self.slug = config.slug
        self._base = config.base_url.rstrip("/")
        self._client = client

    async def fetch(self) -> list[RawProduct]:
        products: list[RawProduct] = []
        url: str | None = f"{self._base}/shop/"
        for _ in range(MAX_PAGES):
            if url is None:
                break
            html = await self._client.get_text(url)
            for card in parse_listing(html, self._base):
                weight = parse_default_weight(await self._client.get_text(card.url))
                products.append(self._to_raw(card, weight))
            url = next_page_url(html, self._base)
        return products

    @staticmethod
    def _to_raw(card: ListingCard, weight_label: str | None) -> RawProduct:
        return RawProduct(
            external_id=card.external_id,
            title=card.title,
            url=card.url,
            variants=[
                RawVariant(
                    external_id=f"{card.external_id}-default",
                    title=weight_label or "",
                    price=card.price,
                    grams=None,
                    available=card.in_stock,
                )
            ],
            attributes=card.attributes,
        )
