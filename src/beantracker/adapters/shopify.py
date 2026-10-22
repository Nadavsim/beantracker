import json
from decimal import Decimal, InvalidOperation
from typing import Any

from beantracker.config import RoasterConfig
from beantracker.domain.raw import RawProduct, RawVariant
from beantracker.http.client import PoliteClient

PAGE_SIZE = 250
MAX_PAGES = 40  # safety stop against a misbehaving paginator


class ShopifyAdapter:
    """Generic adapter over Shopify's public /products.json.

    Per-roaster behavior comes only from config.options:
      exclude_product_types: [str]       product_type values to drop (case-insensitive)
      exclude_title_keywords: [str]      drop products whose title contains any of these
      min_price: number (default 3)      drop products whose variants all cost less than this;
                                         removes free/cheap add-ons (milk, syrup) that sit in
                                         the same catalog as beans
    """

    def __init__(self, config: RoasterConfig, client: PoliteClient) -> None:
        self.slug = config.slug
        self._base = config.base_url.rstrip("/")
        self._client = client
        opts = config.options
        self._exclude_types = {t.lower() for t in opts.get("exclude_product_types", [])}
        self._exclude_words = [w.lower() for w in opts.get("exclude_title_keywords", [])]
        self._min_price = Decimal(str(opts.get("min_price", 3)))

    async def fetch(self) -> list[RawProduct]:
        products: list[RawProduct] = []
        for page in range(1, MAX_PAGES + 1):
            body = await self._client.get_text(
                f"{self._base}/products.json?limit={PAGE_SIZE}&page={page}"
            )
            items: list[dict[str, Any]] = json.loads(body)["products"]
            if not items:
                break
            products.extend(p for item in items if (p := self._parse(item)) is not None)
            if len(items) < PAGE_SIZE:
                break
        return products

    def _parse(self, item: dict[str, Any]) -> RawProduct | None:
        title = str(item["title"])
        ptype = str(item.get("product_type") or "")
        if ptype.lower() in self._exclude_types:
            return None
        if any(w in title.lower() for w in self._exclude_words):
            return None
        variants = [
            RawVariant(
                external_id=str(v["id"]),
                title=str(v.get("title") or ""),
                price=str(v["price"]),
                grams=v.get("grams"),
                available=bool(v.get("available")),
            )
            for v in item.get("variants", [])
        ]
        if not variants or all(_price(v.price) < self._min_price for v in variants):
            return None
        tags = item.get("tags", [])
        if isinstance(tags, str):  # the storefront API sometimes returns a comma string
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        return RawProduct(
            external_id=str(item["id"]),
            title=title,
            url=f"{self._base}/products/{item['handle']}",
            description=str(item.get("body_html") or ""),
            tags=tags,
            product_type=ptype,
            variants=variants,
            raw=item,
        )


def _price(raw: str) -> Decimal:
    try:
        return Decimal(raw)
    except InvalidOperation:
        return Decimal(0)
