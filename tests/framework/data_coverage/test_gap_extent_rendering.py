"""
Gap Extent Rendering Tests.

How long a gap was, expressed in the units the reader thinks in: hours always, calendar
days once hours stop being readable, and — only on a market that closes — the trading time
actually lost. The last one is the §37 gate in the caller: MarketCalendar counts Mon-Fri
without knowing the market type, so a 24/7 market must never be asked for a trading week.
"""

import pandas as pd
import pytest

from python.framework.discoveries.data_coverage.data_coverage_report import (
    DataCoverageReport)
from python.framework.types.coverage_report_types import Gap
from python.framework.utils.market_calendar import GapCategory
from python.framework.utils.time_utils import format_duration


def _gap(start, hours):
    """Build a gap of a given length.

    Args:
        start: Gap start (ISO string, UTC)
        hours: Duration in hours

    Returns:
        Gap ready for rendering
    """
    gap_start = pd.Timestamp(start, tz='UTC')
    seconds = hours * 3600
    return Gap(
        gap_seconds=seconds,
        category=GapCategory.LARGE,
        reason='test',
        gap_start=gap_start,
        gap_end=gap_start + pd.Timedelta(hours=hours),
        # duration_human comes off gap_seconds, so nothing else to set
    )


@pytest.fixture
def forex_report():
    """A report for a market that closes on weekends."""
    return DataCoverageReport(symbol='EURUSD', broker_type='mt5')


@pytest.fixture
def crypto_report():
    """A report for a 24/7 market."""
    return DataCoverageReport(symbol='BTCUSD', broker_type='kraken_spot')


class TestGapExtent:
    """Hours always, calendar days once they help."""

    def test_short_gap_shows_hours_only(self, crypto_report):
        extent = crypto_report._gap_extent(_gap('2026-07-29T15:33:00+00:00', 5.17))

        assert extent == f"{format_duration(5.17 * 3600)} (5.17h)"

    def test_long_gap_adds_calendar_days(self, crypto_report):
        extent = crypto_report._gap_extent(_gap('2026-07-29T15:33:00+00:00', 118.0))

        assert '118.00h' in extent
        assert '4.9d' in extent

    def test_day_scale_starts_at_a_full_day(self, crypto_report):
        """Just below the threshold stays hours-only, just above gains the day figure."""
        assert 'd)' not in crypto_report._gap_extent(
            _gap('2026-07-29T00:00:00+00:00', 23.9))
        assert 'd)' in crypto_report._gap_extent(
            _gap('2026-07-29T00:00:00+00:00', 24.0))


class TestTradingTimeLost:
    """The figure that matters on a market that closes — and must not exist elsewhere."""

    def test_crypto_never_reports_trading_days(self, crypto_report):
        """A 24/7 market has no trading week; calendar days are the whole truth."""
        assert crypto_report._trading_time_lost(
            _gap('2026-07-29T00:00:00+00:00', 336.0)) == ''

    def test_full_trading_week(self, forex_report):
        """Sunday 22:00 to Friday 20:00 — Mon through Fri, the archive's real case."""
        lost = forex_report._trading_time_lost(_gap('2025-09-28T22:00:00+00:00', 118.0))

        assert lost == '5 trading days (1 full trading week)'

    def test_two_full_trading_weeks(self, forex_report):
        lost = forex_report._trading_time_lost(_gap('2025-09-28T22:00:00+00:00', 334.0))

        assert lost == '10 trading days (2 full trading weeks)'

    def test_partial_weeks_are_fractional(self, forex_report):
        """Beyond a week but not a whole one — stated as a fraction, not rounded away."""
        # Sunday 22:00 + 190 h ends on the following Monday: Mon-Fri plus that Monday
        lost = forex_report._trading_time_lost(_gap('2025-09-28T22:00:00+00:00', 190.0))

        assert lost == '6 trading days (1.2 trading weeks)'

    def test_under_a_week_is_days_only(self, forex_report):
        lost = forex_report._trading_time_lost(_gap('2025-09-29T00:00:00+00:00', 48.0))

        assert lost == '3 trading days'

    def test_short_gap_reports_nothing(self, forex_report):
        """
        The trading-day count is calendar-day based, so a two-hour Wednesday gap
        would read as '1 trading day'. Below a full day the figure is meaningless.
        """
        assert forex_report._trading_time_lost(
            _gap('2025-10-15T15:37:00+00:00', 2.07)) == ''
