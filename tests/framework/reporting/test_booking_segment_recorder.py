"""
When a booking period ends — the boundary check that runs on every tick.

The recorder holds the state a period needs while it is still open, and `check_boundary` is
called from BOTH event sources on EVERY tick. That makes it a hot path, and the shape of these
tests follows from that: they pin the BEHAVIOUR (a day flip seals exactly once, at the market's
own boundary) and they pin the COST (the ordinary tick does not convert a timezone).

The cost half is not premature. Measured 2026-09-22, asking `trading_day_of` per tick costs
1.52 µs at UTC and 1.87 µs at America/New_York — 2.3-2.8 s over a 1.5-million-tick benchmark run
against a 23.4 s baseline, to re-derive a date that changes once a day. A cached boundary instant
brings the same check to 0.071 µs. A test that only checked the behaviour would let that
regression back in silently, because the behaviour is identical either way.
"""

from datetime import datetime, timedelta, timezone

from python.framework.reporting import booking_segment_recorder
from python.framework.reporting.booking_segment_recorder import BookingSegmentRecorder
from python.framework.reporting.builders.booking_segment_builder import SegmentSnapshot
from python.framework.types.config_types.market_config_types import DayAnchorConfig

# Crypto: the day flips at midnight UTC. Forex: at the 17:00 New York swap rollover, which is
# 21:00 UTC in summer and 22:00 in winter — the case a midnight assumption gets wrong.
_CRYPTO = DayAnchorConfig(local_time='00:00', timezone='UTC')
_FOREX = DayAnchorConfig(local_time='17:00', timezone='America/New_York')

_MON = datetime(2026, 9, 21, tzinfo=timezone.utc)


def _seal_source():
    """A snapshot callable with no records — the figures are the builder's concern, not this."""
    return [], SegmentSnapshot(currency='USD')


def _recorder(anchor: DayAnchorConfig = _CRYPTO) -> BookingSegmentRecorder:
    return BookingSegmentRecorder('unit', anchor)


class TestWhereTheDayFlips:

    def test_a_tick_inside_the_day_seals_nothing(self):
        rec = _recorder()
        rec.check_boundary(_MON + timedelta(hours=1), _seal_source)
        rec.check_boundary(_MON + timedelta(hours=23), _seal_source)

        assert rec.get_highest_segment_no() == 0

    def test_the_first_tick_past_midnight_seals_once(self):
        rec = _recorder()
        rec.check_boundary(_MON + timedelta(hours=1), _seal_source)
        rec.check_boundary(_MON + timedelta(days=1, seconds=1), _seal_source)
        rec.check_boundary(_MON + timedelta(days=1, hours=2), _seal_source)

        assert rec.get_highest_segment_no() == 1

    def test_forex_flips_at_the_rollover_and_not_at_midnight(self):
        """
        21:00 UTC on 2026-09-21 is 17:00 New York — the new session. Midnight is three hours
        later and is NOT a boundary for this market, which is the whole reason the anchor exists.
        """
        rec = _recorder(_FOREX)
        rec.check_boundary(_MON + timedelta(hours=18), _seal_source)     # 18:00 UTC, still Sunday's
        assert rec.get_highest_segment_no() == 0

        rec.check_boundary(_MON + timedelta(hours=21, minutes=1), _seal_source)
        assert rec.get_highest_segment_no() == 1, 'the rollover did not seal'

        rec.check_boundary(_MON + timedelta(days=1), _seal_source)       # midnight after it
        assert rec.get_highest_segment_no() == 1, 'midnight sealed a forex day'

    def test_a_quiet_stretch_across_a_boundary_still_seals(self):
        """
        The cache says when the day ENDS, never that a tick must arrive before then. A feed that
        goes silent for two days and returns still seals — once, on the tick that comes back.
        """
        rec = _recorder()
        rec.check_boundary(_MON + timedelta(hours=1), _seal_source)
        rec.check_boundary(_MON + timedelta(days=2, hours=3), _seal_source)

        assert rec.get_highest_segment_no() == 1

    def test_no_anchor_books_nothing(self):
        """An internal harness builds a scenario with no broker; it has no market and no day."""
        rec = BookingSegmentRecorder('unit', None)
        rec.check_boundary(_MON + timedelta(days=5), _seal_source)

        assert rec.get_highest_segment_no() == 0


class TestTheOrdinaryTickCostsOneComparison:
    """
    The hot-path half. `trading_day_of` is the timezone conversion; it may run when a period
    OPENS or when one is SEALED, and at no other time.
    """

    @staticmethod
    def _counted(monkeypatch):
        """Replace `trading_day_of` in the recorder's namespace with a counting wrapper."""
        calls = []
        real = booking_segment_recorder.trading_day_of

        def counting(instant, anchor):
            calls.append(instant)
            return real(instant, anchor)

        monkeypatch.setattr(booking_segment_recorder, 'trading_day_of', counting)
        return calls

    def test_a_day_of_ticks_converts_exactly_once(self, monkeypatch):
        calls = self._counted(monkeypatch)
        rec = _recorder()

        for hour in range(24):
            rec.check_boundary(_MON + timedelta(hours=hour), _seal_source)

        # One conversion to OPEN the period; the other twenty-three ticks are comparisons.
        assert len(calls) == 1, f'{len(calls)} timezone conversions for 24 ticks'

    def test_crossing_a_boundary_converts_once_more(self, monkeypatch):
        calls = self._counted(monkeypatch)
        rec = _recorder()

        for hour in range(0, 48, 2):
            rec.check_boundary(_MON + timedelta(hours=hour), _seal_source)

        # Open, then seal — two conversions across two days of ticks.
        assert len(calls) == 2, f'{len(calls)} timezone conversions across two days'
        assert rec.get_highest_segment_no() == 1


class TestAPeriodIsNeverFiledInsideOut:
    """
    Nothing downstream checks the order of a period's two instants — not the builder, not the
    ledger, not the renderer — so an inverted pair would travel as far as a Gantt bar drawn
    backwards. The canonical clock is clamped forward since #539, which is what makes this
    unreachable; the guard stays as the assertion that the clamp holds, and it SAYS so instead
    of repairing the stamp in silence.
    """

    def test_a_backwards_seal_is_clamped_and_announced(self):
        said = []
        rec = BookingSegmentRecorder('unit', _CRYPTO, log=said.append)
        rec.check_boundary(_MON + timedelta(hours=1), _seal_source)

        # A clock that went backwards: the close is asked for BEFORE the period opened.
        segments = rec.close(_MON, _seal_source)

        assert segments[0].closed_at == segments[0].opened_at
        assert any('BEFORE it opened' in line for line in said)

    def test_the_ordinary_seal_is_untouched(self):
        rec = BookingSegmentRecorder('unit', _CRYPTO, log=lambda _: None)
        rec.check_boundary(_MON + timedelta(hours=1), _seal_source)

        closed = _MON + timedelta(hours=5)
        segments = rec.close(closed, _seal_source)
        assert segments[0].closed_at == closed
