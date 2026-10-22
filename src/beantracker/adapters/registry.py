from collections.abc import Callable

from beantracker.adapters.base import RoasterAdapter
from beantracker.adapters.dak import DakAdapter
from beantracker.adapters.shopify import ShopifyAdapter
from beantracker.config import RoasterConfig
from beantracker.http.client import PoliteClient

AdapterFactory = Callable[[RoasterConfig, PoliteClient], RoasterAdapter]

# Custom adapters register here; `adapter:` in roasters.yaml selects one by name.
ADAPTERS: dict[str, AdapterFactory] = {"shopify": ShopifyAdapter, "dak": DakAdapter}


def build_adapter(config: RoasterConfig, client: PoliteClient) -> RoasterAdapter:
    try:
        factory = ADAPTERS[config.adapter]
    except KeyError:
        raise ValueError(f"Unknown adapter {config.adapter!r} for roaster {config.slug}") from None
    return factory(config, client)
