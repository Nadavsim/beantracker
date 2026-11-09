import json
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from beantracker.config import RoasterConfig
from beantracker.domain.raw import RawProduct, RawVariant
from beantracker.http.client import PoliteClient

Roast = Literal["light", "medium", "medium-dark", "dark"]  # what normalize/attributes emits

PAGE_SIZE = 250
MAX_PAGES = 40  # safety stop against a misbehaving paginator


class ShopifyOptions(BaseModel):
    """The per-roaster `options:` a Shopify entry in roasters.yaml may carry.

    Unknown keys are rejected: a typo such as `exclude_product_type` must fail loudly instead
    of quietly doing nothing.
    """

    model_config = ConfigDict(extra="forbid")

    # product_type values to drop (case-insensitive)
    exclude_product_types: list[str] = Field(default_factory=list)
    # drop products whose title contains any of these
    exclude_title_keywords: list[str] = Field(default_factory=list)
    # drop products whose variants all cost less than this; removes free/cheap add-ons (milk,
    # syrup) that sit in the same catalog as beans
    min_price: Decimal = Decimal(3)
    # roast level implied by the end of a product's handle, e.g. {"-light": "light"} for a
    # roaster whose "<bean>-light" listing is the lighter roast of "<bean>". The site's own
    # convention, so it is stated per roaster rather than guessed; the longest suffix wins.
    roast_from_handle: dict[str, Roast] = Field(default_factory=dict)


class ShopifyAdapter:
    """Generic adapter over Shopify's public /products.json; behaviour per ShopifyOptions."""

    def __init__(self, config: RoasterConfig, client: PoliteClient) -> None:
        self.slug = config.slug
        self._base = config.base_url.rstrip("/")
        self._client = client
        opts = ShopifyOptions.model_validate(config.options)
        self._exclude_types = {t.lower() for t in opts.exclude_product_types}
        self._exclude_words = [w.lower() for w in opts.exclude_title_keywords]
        self._min_price = opts.min_price
        # Longest suffix first, so "-extra-light" is not shadowed by "-light".
        self._roast_suffixes = sorted(
            ((s.lower(), roast) for s, roast in opts.roast_from_handle.items()),
            key=lambda pair: -len(pair[0]),
        )

    def _roast_from_handle(self, handle: str) -> str | None:
        handle = handle.lower()
        return next(
            (roast for suffix, roast in self._roast_suffixes if handle.endswith(suffix)), None
        )

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
        roast = self._roast_from_handle(str(item["handle"]))
        return RawProduct(
            external_id=str(item["id"]),
            title=title,
            url=f"{self._base}/products/{item['handle']}",
            description=str(item.get("body_html") or ""),
            tags=tags,
            product_type=ptype,
            variants=variants,
            attributes={"roast_level": roast} if roast else {},
            raw=_slim(item),
        )


# What `_parse` reads, plus a few timestamps. Everything else in a Shopify product (images,
# option lists, dozens of per-variant fields) is bulk that normalization never looks at; it
# made up 44-79% of the stored payload. Because `_parse` accepts this slim form, a stored
# payload can be re-parsed and re-normalized without re-scraping.
_KEEP = (
    "id",
    "title",
    "handle",
    "body_html",
    "product_type",
    "tags",
    "vendor",
    "created_at",
    "updated_at",
    "published_at",
)
_VARIANT_KEEP = ("id", "title", "price", "grams", "available", "sku")


def _slim(item: dict[str, Any]) -> dict[str, Any]:
    slim = {k: item[k] for k in _KEEP if k in item}
    slim["variants"] = [
        {k: v[k] for k in _VARIANT_KEEP if k in v} for v in item.get("variants", [])
    ]
    return slim


def _price(raw: str) -> Decimal:
    try:
        return Decimal(raw)
    except InvalidOperation:
        return Decimal(0)
