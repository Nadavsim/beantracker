from typing import Any

from pydantic import BaseModel, Field


class RawVariant(BaseModel):
    """A sellable variant exactly as the site presents it (strings not yet parsed)."""

    external_id: str
    title: str
    price: str
    grams: int | None = None  # as reported by the site; often 0 or wrong
    available: bool


class RawProduct(BaseModel):
    """Adapter output. Adapters fetch and parse structure only; normalization lives elsewhere."""

    external_id: str
    title: str
    url: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    product_type: str = ""
    variants: list[RawVariant]
    # Site-specific structured attributes (e.g. DAK's origin/process block).
    attributes: dict[str, str] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)
