from datetime import UTC, datetime

from beantracker.config import HealthThresholds
from beantracker.domain.bean import Bean
from beantracker.health import COUNT_DROP, MISSING_FIELDS, ZERO_PRODUCTS, evaluate
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
