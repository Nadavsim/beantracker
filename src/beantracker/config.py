from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://beantracker:beantracker@localhost:5432/beantracker"
    user_agent: str = "beantracker/0.1 (+https://github.com/Nadavsim/beantracker; portfolio project)"
    request_delay_s: float = 2.0
    cache_dir: Path | None = None  # dev-only response cache; off unless set


class HealthThresholds(BaseModel):
    max_drop_pct: float = 40.0
    max_missing_rate: float = 0.3


class RoasterConfig(BaseModel):
    """One entry of config/roasters.yaml. Adding a Shopify roaster is only a new entry."""

    slug: str
    name: str
    base_url: str
    adapter: Literal["shopify", "dak"] | str
    # Shopify's /products.json carries no currency, so it must be stated here.
    currency: str = Field(min_length=3, max_length=3)
    options: dict[str, Any] = Field(default_factory=dict)
    health: HealthThresholds = Field(default_factory=HealthThresholds)


def load_roasters(path: Path = Path("config/roasters.yaml")) -> dict[str, RoasterConfig]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    roasters = [RoasterConfig.model_validate(item) for item in raw["roasters"]]
    return {r.slug: r for r in roasters}
