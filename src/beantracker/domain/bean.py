from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class Bean(BaseModel):
    """One sellable size of one coffee, normalized. Matches the catalog shape in the spec."""

    roaster: str
    external_id: str
    variant_external_id: str
    name: str
    url: str
    origin_country: str | None = None
    origin_region: str | None = None
    process: str | None = None
    varietal: str | None = None
    roast_level: str | None = None
    tasting_notes: list[str] = Field(default_factory=list)
    weight_g: int | None = None
    price_minor: int
    currency: str
    price_per_100g: Decimal | None = None
    in_stock: bool
    scraped_at: datetime
