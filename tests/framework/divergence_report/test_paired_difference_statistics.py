"""
Statistics over the days' differences of equity change (#512): the mean, the tracking error, a
90 % interval on the mean, and the lag-1 autocorrelation with its Ljung-Box statistic.

The values are held to the textbook tables and to examples worked by hand, so a reader can check
them without a statistics library.
"""

import pytest

from python.framework.utils.paired_difference_statistics import (
    LJUNG_BOX_LAG_ONE_CRITICAL_5PCT,
    lag_one_autocorrelation,
    ljung_box_lag_one,
    mean_of,
    ninety_percent_interval,
    tracking_error_of,
)

# Five days, worked by hand: mean 0.2, deviations −0.3 0.3 −0.1 0.1 0.0, sum of squares 0.2,
# standard deviation √(0.2 / 4) = 0.2236, t(0.95; 4) = 2.132
_DAYS = [-0.1, 0.5, 0.1, 0.3, 0.2]


def test_the_mean_and_the_tracking_error():
    assert mean_of(_DAYS) == pytest.approx(0.2)
    assert tracking_error_of(_DAYS) == pytest.approx(0.05 ** 0.5)


def test_the_interval_uses_the_t_quantile_of_its_degrees_of_freedom():
    low, high = ninety_percent_interval(_DAYS)
    half_width = 2.132 * 0.05 ** 0.5 / 5 ** 0.5
    assert (low, high) == pytest.approx((0.2 - half_width, 0.2 + half_width))


def test_between_two_tabulated_degrees_the_wider_interval_is_taken():
    days = [0.1 * (index % 3) for index in range(36)]
    low, high = ninety_percent_interval(days)
    half_width = 1.697 * tracking_error_of(days) / 36 ** 0.5   # 35 degrees: the value at 30
    assert high - low == pytest.approx(2 * half_width)


def test_the_lag_one_autocorrelation_and_its_ljung_box_statistic():
    # deviations −0.3 0.3 −0.1 0.1 0.0: products of neighbours −0.09 −0.03 −0.01 0.0 = −0.13
    assert lag_one_autocorrelation(_DAYS) == pytest.approx(-0.13 / 0.2)
    assert ljung_box_lag_one(_DAYS) == pytest.approx(5 * 7 * 0.65 ** 2 / 4)


def test_a_persistent_series_rejects_independence():
    trending = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    assert ljung_box_lag_one(trending) > LJUNG_BOX_LAG_ONE_CRITICAL_5PCT


def test_too_few_days_give_no_figure():
    assert mean_of([]) is None
    assert tracking_error_of([0.3]) is None
    assert ninety_percent_interval([0.3]) is None
    assert lag_one_autocorrelation([0.1, 0.2]) is None
    assert ljung_box_lag_one([0.1, 0.2]) is None


def test_a_series_without_variation_has_no_autocorrelation():
    assert lag_one_autocorrelation([0.2, 0.2, 0.2, 0.2]) is None
