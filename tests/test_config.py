from pathlib import Path

import pytest
from pydantic import ValidationError

from beantracker.config import RoasterConfig, load_roasters


def test_shipped_roaster_config_is_valid() -> None:
    roasters = load_roasters(Path("config/roasters.yaml"))
    assert {"nomena", "september", "hydrangea", "dak"} <= roasters.keys()
    assert roasters["nomena"].currency == "ILS"


def test_currency_is_required_for_each_roaster() -> None:
    with pytest.raises(ValidationError):
        RoasterConfig.model_validate(
            {"slug": "x", "name": "X", "base_url": "https://x.test", "adapter": "shopify"}
        )
