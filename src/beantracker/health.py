import statistics
from dataclasses import dataclass, field

from beantracker.config import HealthThresholds
from beantracker.domain.bean import Bean
from beantracker.models.orm import RunStatus

ZERO_PRODUCTS = "zero_products"
COUNT_DROP = "count_drop"
MISSING_FIELDS = "missing_fields"
COVERAGE_DROP = "coverage_drop"  # always followed by ':<field>', e.g. coverage_drop:origin_country
# Informational: an operator declared this run the new normal (`scrape --reset-baseline`).
# It never changes a run's status; it marks where the baseline window restarts.
BASELINE_RESET = "baseline_reset"

# Fields that are optional per product but are expected for most coffees. A parser that
# silently stops finding them (a markup change, a renamed label) leaves products intact,
# so neither the count check nor the weight check notices.
DESCRIPTIVE_FIELDS = ("origin_country", "process", "roast_level", "varietal", "tasting_notes")


def baseline_count(recent_ok_counts: list[int]) -> float | None:
    """Expected product count: the median of recent healthy runs, None without history.

    Comparing with only the latest ok run lets a slow decline slip through, because the
    baseline drifts down with it (30% per run never trips a 40% limit). A median of a few
    runs holds the older level in view, and unlike a maximum a single odd high run does
    not inflate it.
    """
    return statistics.median(recent_ok_counts) if recent_ok_counts else None


def field_coverage(beans: list[Bean]) -> dict[str, float]:
    """Share of sizes that have each descriptive field filled in."""
    if not beans:
        return {}
    return {f: sum(bool(getattr(b, f)) for b in beans) / len(beans) for f in DESCRIPTIVE_FIELDS}


def baseline_coverage(recent: list[dict[str, float]]) -> dict[str, float]:
    """Per-field median coverage of recent healthy runs (runs without coverage are skipped)."""
    names = {f for coverage in recent for f in coverage}
    return {f: statistics.median(c[f] for c in recent if f in c) for f in names}


@dataclass(frozen=True)
class HealthReport:
    status: RunStatus
    flags: list[str]
    missing_field_rate: float
    coverage: dict[str, float] = field(default_factory=dict)


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
    baseline_cov: dict[str, float] | None = None,
) -> HealthReport:
    """Judge a finished scrape against the recent healthy baseline (see baseline_count).

    Silent breakage is the main scraper failure mode (a redesign yields an empty or partial
    page, not an exception), so these checks compare against history instead of trusting
    the absence of errors. Zero products is a failure; the others are warnings.
    """
    rate = missing_field_rate(beans)
    coverage = field_coverage(beans)
    flags: list[str] = []
    if product_count == 0:
        flags.append(ZERO_PRODUCTS)
    elif baseline and baseline > 0:
        drop_pct = (baseline - product_count) / baseline * 100
        if drop_pct > thresholds.max_drop_pct:
            flags.append(COUNT_DROP)
    if rate > thresholds.max_missing_rate:
        flags.append(MISSING_FIELDS)
    if baseline_cov and coverage:
        for name, usual in sorted(baseline_cov.items()):
            # Only fields that are normally well filled can "drop": a field that is rarely
            # present (say roast level) wobbling between 5% and 0% is not a signal.
            if (
                usual >= thresholds.min_baseline_coverage
                and usual - coverage.get(name, 0.0) > thresholds.max_coverage_drop
            ):
                flags.append(f"{COVERAGE_DROP}:{name}")

    if ZERO_PRODUCTS in flags:
        status = RunStatus.failed
    elif flags:
        status = RunStatus.warning
    else:
        status = RunStatus.ok
    return HealthReport(status, flags, rate, coverage)
