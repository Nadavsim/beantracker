from collections.abc import Callable

from pydantic import BaseModel

from beantracker.adapters.base import RoasterAdapter
from beantracker.adapters.dak import DakAdapter
from beantracker.adapters.shopify import ShopifyAdapter, ShopifyOptions
from beantracker.config import RoasterConfig
from beantracker.http.client import PoliteClient

AdapterFactory = Callable[[RoasterConfig, PoliteClient], RoasterAdapter]

# Custom adapters register here; `adapter:` in roasters.yaml selects one by name.
ADAPTERS: dict[str, AdapterFactory] = {"shopify": ShopifyAdapter, "dak": DakAdapter}


# Adapters whose `options:` have a schema. Checked before any scraping starts, so a typo in
# roasters.yaml is a clear error and not an option that silently does nothing.
OPTION_MODELS: dict[str, type[BaseModel]] = {"shopify": ShopifyOptions}


def validate_options(config: RoasterConfig) -> None:
    """Raise pydantic.ValidationError if the roaster's options do not fit its adapter."""
    model = OPTION_MODELS.get(config.adapter)
    if model is not None:
        model.model_validate(config.options)


def build_adapter(config: RoasterConfig, client: PoliteClient) -> RoasterAdapter:
    try:
        factory = ADAPTERS[config.adapter]
    except KeyError:
        raise ValueError(f"Unknown adapter {config.adapter!r} for roaster {config.slug}") from None
    return factory(config, client)
