import statistics
from dataclasses import dataclass

from beantracker.config import HealthThresholds
from beantracker.domain.bean import Bean
from beantracker.models.orm import RunStatus

ZERO_PRODUCTS = "zero_products"
COUNT_DROP = "count_drop"
MISSING_FIELDS = "missing_fields"
# Informational: an operator declared this run the new normal (`scrape --reset-baseline`).
# It never changes a run's status; it marks where the baseline window restarts.
BASELINE_RESET = "baseline_reset"


def baseline_count(recent_ok_counts: list[int]) -> float | None:
    """Expected product count: the median of recent healthy runs, None without history.

    Comparing with only the latest ok run lets a slow decline slip through, because the
    baseline drifts down with it (30% per run never trips a 40% limit). A median of a few
    runs holds the older level in view, and unlike a maximum a single odd high run does
    not inflate it.
    """
    return statistics.median(recent_ok_counts) if recent_ok_counts else None


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
    baseline: float | None,
    beans: list[Bean],
    thresholds: HealthThresholds,
) -> HealthReport:
    """Judge a finished scrape against the recent healthy baseline (see baseline_count).

    Silent breakage is the main scraper failure mode (a redesign yields an empty or partial
    page, not an exception), so these checks compare against history instead of trusting
    the absence of errors. Zero products is a failure; the others are warnings.
    """
    rate = missing_field_rate(beans)
    flags: list[str] = []
    if product_count == 0:
        flags.append(ZERO_PRODUCTS)
    elif baseline and baseline > 0:
        drop_pct = (baseline - product_count) / baseline * 100
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
