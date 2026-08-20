"""
Gap Classification Tests.

classify_gap() decides how every gap in the archive is presented — including whether it
carries a warning icon or an informational one. It classified untested until now, which is
how a forex assumption survived inside it: "outside trading hours" was applied to 24/7
markets too, softening real crypto gaps into notices.

MarketCalendar is forex-shaped by design (§37) — its weekend/holiday methods hard-code
Mon-Fri. Callers gate on the market's weekend_closure, and so must classify_gap itself.
"""

from datetime import datetime, timedelta, timezone

from python.framework.utils.market_calendar import GapCategory, MarketCalendar


def _classify(start, hours, weekend_closure, thresholds=None):
    """Classify a gap of a given length starting at a given moment.

    Args:
        start: Gap start (UTC-aware)
        hours: Gap duration in hours
        weekend_closure: True for markets that close on weekends
        thresholds: Optional threshold override

    Returns:
        Tuple of (GapCategory, reason string)
    """
    end = start + timedelta(hours=hours)
    return MarketCalendar.classify_gap(
        start, end, hours * 3600, thresholds, weekend_closure=weekend_closure)


# Wednesday inside forex trading hours, and the Saturday of the same week
WEDNESDAY = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
SATURDAY = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


class TestCategoryThresholds:
    """The size bands, independent of market type."""

    def test_below_five_seconds_is_seamless(self):
        category, _ = _classify(WEDNESDAY, 1 / 3600, weekend_closure=False)
        assert category == GapCategory.SEAMLESS

    def test_under_thirty_minutes_is_short(self):
        category, reason = _classify(WEDNESDAY, 0.25, weekend_closure=False)
        assert category == GapCategory.SHORT
        assert 'Short interruption' in reason

    def test_between_thresholds_is_moderate(self):
        category, _ = _classify(WEDNESDAY, 2.0, weekend_closure=False)
        assert category == GapCategory.MODERATE

    def test_above_four_hours_is_large(self):
        category, reason = _classify(WEDNESDAY, 5.17, weekend_closure=False)
        assert category == GapCategory.LARGE
        assert 'check data collection' in reason


class TestMarketTypeGating:
    """
    A 24/7 market has no closed hours, so nothing may soften a gap there.

    The regression this pins: is_market_open() answers by the forex calendar, so an
    ungated call turned a real Saturday gap on crypto into 'outside trading hours' —
    complete with the informational icon that tells the operator to ignore it.
    """

    def test_crypto_weekend_gap_is_not_softened(self):
        category, reason = _classify(SATURDAY, 2.0, weekend_closure=False)

        assert category == GapCategory.MODERATE
        assert 'trading hours' not in reason
        assert reason.startswith('⚠️')

    def test_crypto_weekday_gap_reads_the_same(self):
        """No 'during/outside' split on a market that never closes."""
        _, weekday = _classify(WEDNESDAY, 2.0, weekend_closure=False)
        _, weekend = _classify(SATURDAY, 2.0, weekend_closure=False)

        assert weekday == weekend

    def test_forex_gap_in_trading_hours_warns(self):
        _, reason = _classify(WEDNESDAY, 2.0, weekend_closure=True)

        assert 'during trading hours' in reason
        assert reason.startswith('⚠️')

    def test_forex_gap_outside_trading_hours_is_informational(self):
        """A forex market really is closed then — the softer icon is correct there."""
        category, reason = _classify(SATURDAY, 2.0, weekend_closure=True)

        assert category == GapCategory.MODERATE
        assert 'outside trading hours' in reason
        assert reason.startswith('ℹ️')


class TestWeekendRecognition:
    """The weekend branch itself stays gated on weekend_closure."""

    def test_forex_weekend_closure_is_expected(self):
        friday_close = datetime(2026, 7, 31, 21, 0, tzinfo=timezone.utc)
        category, _ = _classify(friday_close, 48.0, weekend_closure=True)

        assert category == GapCategory.WEEKEND

    def test_crypto_has_no_weekend_category(self):
        """48 h of silence on a 24/7 market is data loss, not a closure."""
        friday_close = datetime(2026, 7, 31, 21, 0, tzinfo=timezone.utc)
        category, _ = _classify(friday_close, 48.0, weekend_closure=False)

        assert category == GapCategory.LARGE
