"""
FiniexTestingIDE - Spread Estimate Report

Prints a spread measurement: per year, origin class and quote regime the estimate from the
trades, the textbook reference beside it, and — where the archive recorded a quote — the quoted
and the effective spread the estimate is checked against. Renders only; every figure is computed
by the measurement.
"""

from typing import Dict, List, Optional

from python.framework.types.spread_estimate_types import (
    SpreadDayEstimate,
    SpreadMeasurement,
)


def print_spread_measurement(measurement: SpreadMeasurement,
                             year: Optional[int] = None) -> None:
    """
    Print a measurement: the yearly table, or one year's days.

    Args:
        measurement: The measurement
        year: Print this year's days instead of the yearly table
    """
    lines = (_day_lines(measurement, year) if year is not None
             else _year_lines(measurement))
    print('\n'.join(lines))


def print_spread_cache_built(broker_type: str, symbol: str,
                             estimates: List[SpreadDayEstimate]) -> None:
    """
    Print what one symbol's cache now holds.

    Args:
        broker_type: The broker
        symbol: The symbol
        estimates: Its day estimates
    """
    if not estimates:
        print(f'\n📏 {broker_type}/{symbol}: every file recorded a quote — nothing to cache')
        return
    print(f'\n📏 {broker_type}/{symbol}: {len(estimates):,} day(s) cached, '
          f'{estimates[0].day.isoformat()} → {estimates[-1].day.isoformat()}')


def print_spread_cache_build_all(stats: Dict[str, int]) -> None:
    """
    Print the outcome of building every symbol's cache.

    Args:
        stats: {generated, skipped, failed}
    """
    print(f"\n📏 Spread estimate cache: {stats['generated']} generated, "
          f"{stats['skipped']} skipped (still valid), {stats['failed']} failed")


def _year_lines(measurement: SpreadMeasurement) -> List[str]:
    """
    The yearly table and the validation line.

    Args:
        measurement: The measurement

    Returns:
        Report lines
    """
    lines = [
        f"\n{'=' * 60}",
        f'📏 SPREAD ESTIMATE: {measurement.broker_type}/{measurement.symbol}',
        f"{'=' * 60}",
        'From the trades: consecutive opposite-side trades within 1 s, bursts collapsed to their',
        'first fill. In basis points of the price; "mean" is capped at the 99th percentile — the',
        'expected cost, half of it per fill.',
        '',
        f"   {'year':<6}{'origin':<12}{'quote':<10}{'pairs':>11}{'median':>8}{'mean':>8}"
        f"{'p90':>8}{'roll':>8}{'quoted':>8}{'effective':>11}",
    ]
    for row in measurement.rows:
        lines.append(
            f'   {row.year:<6}{row.origin_class:<12}{row.regime.value:<10}{row.pairs:>11,}'
            f'{_bps(row.estimate_median_bps):>8}{_bps(row.estimate_mean_bps):>8}'
            f'{_bps(row.estimate_p90_bps):>8}{_bps(row.roll_median_bps):>8}'
            f'{_bps(row.quoted_mean_bps):>8}{_bps(row.effective_mean_bps):>11}'
        )
    lines.append('')
    lines.append('   quoted / effective: the mean spread the archive recorded at the trades — the '
                 'truth the estimate is checked against')
    lines.append('   roll: the median of the daily Roll estimates, the textbook reference — not '
                 'used for the estimate')
    return lines


def _day_lines(measurement: SpreadMeasurement, year: int) -> List[str]:
    """
    One year's daily estimates.

    Args:
        measurement: The measurement
        year: The year

    Returns:
        Report lines
    """
    days: List[SpreadDayEstimate] = [
        estimate for estimate in measurement.day_estimates if estimate.day.year == year]
    lines = [
        f'\n📏 SPREAD ESTIMATE PER DAY: {measurement.broker_type}/{measurement.symbol} {year}',
        f"   {'day':<12}{'pairs':>9}{'median':>9}{'mean':>9}{'p90':>9}",
    ]
    for estimate in days:
        lines.append(f'   {estimate.day.isoformat():<12}{estimate.pairs:>9,}'
                     f'{_bps(estimate.spread_median_bps):>9}{_bps(estimate.spread_mean_bps):>9}'
                     f'{_bps(estimate.spread_p90_bps):>9}')
    if not days:
        lines.append('   no day with an opposite-side pair in this year')
    return lines


def _bps(value: Optional[float]) -> str:
    """
    A basis-point figure for the table.

    Args:
        value: The figure, None where it does not exist

    Returns:
        The rendered figure
    """
    return '—' if value is None else f'{value:.2f}'
