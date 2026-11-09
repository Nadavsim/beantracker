from datetime import UTC, datetime

from beantracker.config import HealthThresholds
from beantracker.domain.bean import Bean
from beantracker.health import (
    COUNT_DROP,
    MISSING_FIELDS,
    ZERO_PRODUCTS,
    baseline_count,
    baseline_coverage,
    evaluate,
    field_coverage,
    missing_field_rate,
)
from beantracker.models.orm import RunStatus

T = HealthThresholds(max_drop_pct=40, max_missing_rate=0.3)


def bean(weight: int | None = 250) -> Bean:
    return Bean(
        roaster="r",
        external_id="1",
        variant_external_id="1",
        name="Coffee",
        url="https://x.test",
        weight_g=weight,
        price_minor=2500,
        currency="USD",
        in_stock=True,
        scraped_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_healthy_run() -> None:
    report = evaluate(20, 20, [bean()] * 20, T)
    assert report.status == RunStatus.ok
    assert report.flags == []


def test_zero_products_fails() -> None:
    report = evaluate(0, 20, [], T)
    assert report.status == RunStatus.failed
    assert report.flags == [ZERO_PRODUCTS]


def test_zero_products_on_first_run_still_fails() -> None:
    assert evaluate(0, None, [], T).status == RunStatus.failed


def test_big_drop_warns() -> None:
    report = evaluate(10, 20, [bean()] * 10, T)  # 50% drop > 40%
    assert report.status == RunStatus.warning
    assert report.flags == [COUNT_DROP]


def test_drop_at_threshold_is_fine() -> None:
    assert evaluate(12, 20, [bean()] * 12, T).status == RunStatus.ok  # exactly 40%


def test_first_run_has_no_baseline_to_drop_from() -> None:
    assert evaluate(5, None, [bean()] * 5, T).status == RunStatus.ok


def test_growth_is_not_a_drop() -> None:
    assert evaluate(40, 20, [bean()] * 40, T).status == RunStatus.ok


def test_missing_fields_warns() -> None:
    beans = [bean(), bean(None), bean(None), bean(None)]  # 75% lack a weight
    report = evaluate(4, 4, beans, T)
    assert report.status == RunStatus.warning
    assert report.flags == [MISSING_FIELDS]
    assert report.missing_field_rate == 0.75


def test_flags_combine() -> None:
    report = evaluate(5, 20, [bean(None)] * 5, T)
    assert report.flags == [COUNT_DROP, MISSING_FIELDS]


def test_baseline_count_is_the_median_of_recent_ok_runs() -> None:
    assert baseline_count([]) is None
    assert baseline_count([10]) == 10
    assert baseline_count([273, 390]) == 331.5
    assert baseline_count([100, 100, 300, 100, 100]) == 100  # one spike does not move it


def test_drop_is_measured_against_a_fractional_baseline() -> None:
    # 191 vs a baseline of 331.5 is a 42% drop; vs the latest value (273) it was only 30%.
    assert evaluate(191, 331.5, [bean()] * 191, T).flags == [COUNT_DROP]
    assert evaluate(191, 273, [bean()] * 191, T).flags == []


def bean_with(**fields: object) -> Bean:
    return bean().model_copy(update=fields)


def test_field_coverage_is_the_share_of_sizes_with_each_field() -> None:
    beans = [
        bean_with(origin_country="Kenya", tasting_notes=["plum"]),
        bean_with(origin_country="Kenya"),
        bean_with(),
        bean_with(),
    ]
    cov = field_coverage(beans)
    assert cov["origin_country"] == 0.5
    assert cov["tasting_notes"] == 0.25
    assert cov["process"] == 0.0
    assert field_coverage([]) == {}


def test_baseline_coverage_is_the_per_field_median() -> None:
    runs = [{"origin_country": 0.9}, {"origin_country": 0.8}, {"origin_country": 0.1, "x": 0.5}]
    assert baseline_coverage(runs) == {"origin_country": 0.8, "x": 0.5}
    assert baseline_coverage([]) == {}


def test_a_field_that_stops_being_found_is_flagged() -> None:
    usual = {"origin_country": 0.9, "process": 0.8}
    beans = [bean_with(process="Washed")] * 10  # origin vanished, process unchanged
    report = evaluate(10, 10, beans, T, usual)
    assert report.status == RunStatus.warning
    assert report.flags == ["coverage_drop:origin_country"]
    assert report.coverage["origin_country"] == 0.0


def test_a_small_wobble_is_not_a_drop() -> None:
    usual = {"origin_country": 0.9}
    beans = [bean_with(origin_country="Kenya")] * 7 + [bean()] * 3  # 0.7: down 0.2 <= 0.3
    assert evaluate(10, 10, beans, T, usual).flags == []


def test_a_rarely_filled_field_is_never_a_signal() -> None:
    usual = {"roast_level": 0.1}  # below min_baseline_coverage (0.2)
    assert evaluate(10, 10, [bean()] * 10, T, usual).flags == []


def test_no_coverage_history_means_no_coverage_check() -> None:
    assert evaluate(10, 10, [bean()] * 10, T, None).flags == []
    assert evaluate(10, 10, [bean()] * 10, T, {}).flags == []


def test_nothing_measured_is_none_not_zero_percent() -> None:
    assert missing_field_rate([]) is None
    assert missing_field_rate([bean()]) == 0.0
    assert evaluate(0, None, [], T).missing_field_rate is None
