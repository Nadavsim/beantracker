from dataclasses import dataclass

from beantracker.config import HealthThresholds
from beantracker.domain.bean import Bean
from beantracker.models.orm import RunStatus

ZERO_PRODUCTS = "zero_products"
COUNT_DROP = "count_drop"
MISSING_FIELDS = "missing_fields"


@dataclass(frozen=True)
class HealthReport:
    status: RunStatus
    flags: list[str]
    missing_field_rate: float


def missing_field_rate(beans: list[Bean]) -> float:
    """Share of beans lacking a required field (price is always set by construction)."""
    if not beans:
        return 0.0
    bad = sum(1 for b in beans if not b.name or b.weight_g is None or b.price_minor <= 0)
    return bad / len(beans)


def evaluate(
    product_count: int,
    previous_ok_count: int | None,
    beans: list[Bean],
    thresholds: HealthThresholds,
) -> HealthReport:
    """Judge a finished scrape against the previous healthy run.

    Silent breakage is the main scraper failure mode (a redesign yields an empty or partial
    page, not an exception), so these checks compare against history instead of trusting
    the absence of errors. Zero products is a failure; the others are warnings.
    """
    rate = missing_field_rate(beans)
    flags: list[str] = []
    if product_count == 0:
        flags.append(ZERO_PRODUCTS)
    elif previous_ok_count and previous_ok_count > 0:
        drop_pct = (previous_ok_count - product_count) / previous_ok_count * 100
        if drop_pct > thresholds.max_drop_pct:
            flags.append(COUNT_DROP)
    if rate > thresholds.max_missing_rate:
        flags.append(MISSING_FIELDS)

    if ZERO_PRODUCTS in flags:
        status = RunStatus.failed
    elif flags:
        status = RunStatus.warning
    else:
        status = RunStatus.ok
    return HealthReport(status, flags, rate)
