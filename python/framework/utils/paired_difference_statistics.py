"""
Statistics over a series of paired differences — one value per day, live minus backtest.

What a comparison of two runs over the same days reports beside its mean: the spread of the
differences (the tracking error), a 90 % interval on their mean, and whether the days depend on
one another. The interval assumes independent days; a position held across several days breaks
that, which the lag-1 autocorrelation and its Ljung-Box test show — where the test rejects, the
interval is too narrow.

The quantiles come from two short tables rather than a statistics library: a comparison runs
over about thirty days, and the tables cover every case it can meet.
"""

import statistics
from typing import Optional, Sequence, Tuple

# The 95 % quantile of Student's t by degrees of freedom: the half-width factor of a two-sided
# 90 % interval. Between two tabulated degrees the smaller one's value is taken, which widens the
# interval rather than narrowing it.
_T_QUANTILE_95 = {
    1: 6.314, 2: 2.920, 3: 2.353, 4: 2.132, 5: 2.015, 6: 1.943, 7: 1.895, 8: 1.860, 9: 1.833,
    10: 1.812, 11: 1.796, 12: 1.782, 13: 1.771, 14: 1.761, 15: 1.753, 16: 1.746, 17: 1.740,
    18: 1.734, 19: 1.729, 20: 1.725, 21: 1.721, 22: 1.717, 23: 1.714, 24: 1.711, 25: 1.708,
    26: 1.706, 27: 1.703, 28: 1.701, 29: 1.699, 30: 1.697, 40: 1.684, 60: 1.671, 120: 1.658,
}

# The 95 % quantile of the chi-square distribution with one degree of freedom: a lag-1 Ljung-Box
# statistic above it rejects independence at the 5 % level.
LJUNG_BOX_LAG_ONE_CRITICAL_5PCT = 3.841


def mean_of(differences: Sequence[float]) -> Optional[float]:
    """
    The mean of the differences.

    Args:
        differences: One value per day

    Returns:
        The mean; None where there is no value
    """
    return statistics.fmean(differences) if differences else None


def tracking_error_of(differences: Sequence[float]) -> Optional[float]:
    """
    The sample standard deviation of the differences — the tracking error.

    Args:
        differences: One value per day

    Returns:
        The standard deviation; None below two values
    """
    return statistics.stdev(differences) if len(differences) >= 2 else None


def ninety_percent_interval(differences: Sequence[float]) -> Optional[Tuple[float, float]]:
    """
    A two-sided 90 % interval on the mean of paired differences — the paired-t interval.

    Args:
        differences: One value per day

    Returns:
        (lower, upper); None below two values
    """
    if len(differences) < 2:
        return None
    centre = statistics.fmean(differences)
    half_width = (_t_quantile_95(len(differences) - 1) * statistics.stdev(differences)
                  / len(differences) ** 0.5)
    return centre - half_width, centre + half_width


def lag_one_autocorrelation(differences: Sequence[float]) -> Optional[float]:
    """
    The sample autocorrelation of the differences at lag 1.

    Args:
        differences: One value per day, in day order

    Returns:
        The autocorrelation; None below three values, and where every value is the same
    """
    if len(differences) < 3:
        return None
    centre = statistics.fmean(differences)
    deviations = [value - centre for value in differences]
    variation = sum(deviation * deviation for deviation in deviations)
    if variation == 0.0:
        return None
    return sum(later * earlier for earlier, later in zip(deviations, deviations[1:])) / variation


def ljung_box_lag_one(differences: Sequence[float]) -> Optional[float]:
    """
    The Ljung-Box statistic at lag 1: n (n + 2) r1² / (n − 1).

    Args:
        differences: One value per day, in day order

    Returns:
        The statistic, to be held against `LJUNG_BOX_LAG_ONE_CRITICAL_5PCT`; None where the
        autocorrelation is undefined
    """
    autocorrelation = lag_one_autocorrelation(differences)
    if autocorrelation is None:
        return None
    count = len(differences)
    return count * (count + 2) * autocorrelation * autocorrelation / (count - 1)


def _t_quantile_95(degrees_of_freedom: int) -> float:
    """
    The 95 % quantile of Student's t for some degrees of freedom, from the table.

    Args:
        degrees_of_freedom: At least 1

    Returns:
        The tabulated value at the largest tabulated degree not above the one asked for
    """
    tabulated = max(degree for degree in _T_QUANTILE_95 if degree <= degrees_of_freedom)
    return _T_QUANTILE_95[tabulated]
