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

Since #476 the check runs BEFORE the event at its instant is applied, the seal is stamped with the
boundary itself, and every trading day of the market calendar gets its period — a day the run
never heard from included, a closed day of a market that closes excluded.
"""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from python.framework.reporting import booking_period_recorder
from python.framework.reporting.booking_period_recorder import BookingPeriodRecorder
from python.framework.reporting.builders.booking_period_builder import PeriodSnapshot
from python.framework.types.config_types.market_config_types import DayAnchorConfig
from python.framework.types.run_results_types import PeriodCloseReason

# Crypto: the day flips at midnight UTC. Forex: at the 17:00 New York swap rollover, which is
# 21:00 UTC in summer and 22:00 in winter — the case a midnight assumption gets wrong.
_CRYPTO = DayAnchorConfig(local_time='00:00', timezone='UTC')
_FOREX = DayAnchorConfig(local_time='17:00', timezone='America/New_York')

_MON = datetime(2026, 9, 21, tzinfo=timezone.utc)


def _seal_source():
    """A snapshot callable with no records — the figures are the builder's concern, not this."""
    return [], PeriodSnapshot(currency='USD')


def _recorder(anchor: DayAnchorConfig = _CRYPTO) -> BookingPeriodRecorder:
    return BookingPeriodRecorder('unit', anchor, weekend_closure=anchor is _FOREX)


def _valued(equity: float):
    """A snapshot callable reading one account value and no records."""
    return lambda: ([], PeriodSnapshot(currency='USD', final_equity=equity))


def _ny(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """An instant given in New York local time, as UTC — where a forex day flips."""
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo('America/New_York')
                    ).astimezone(timezone.utc)


class TestWhereTheDayFlips:

    def test_a_tick_inside_the_day_seals_nothing(self):
        rec = _recorder()
        rec.check_boundary(_MON + timedelta(hours=1), _seal_source)
        rec.check_boundary(_MON + timedelta(hours=23), _seal_source)

        assert rec.get_highest_period_no() == 0

    def test_the_first_tick_past_midnight_seals_once(self):
        rec = _recorder()
        rec.check_boundary(_MON + timedelta(hours=1), _seal_source)
        rec.check_boundary(_MON + timedelta(days=1, seconds=1), _seal_source)
        rec.check_boundary(_MON + timedelta(days=1, hours=2), _seal_source)

        assert rec.get_highest_period_no() == 1

    def test_forex_flips_at_the_rollover_and_not_at_midnight(self):
        """
        21:00 UTC on 2026-09-21 is 17:00 New York — the new session. Midnight is three hours
        later and is NOT a boundary for this market, which is the whole reason the anchor exists.
        """
        rec = _recorder(_FOREX)
        rec.check_boundary(_MON + timedelta(hours=18), _seal_source)     # 18:00 UTC, still Sunday's
        assert rec.get_highest_period_no() == 0

        rec.check_boundary(_MON + timedelta(hours=21, minutes=1), _seal_source)
        assert rec.get_highest_period_no() == 1, 'the rollover did not seal'

        rec.check_boundary(_MON + timedelta(days=1), _seal_source)       # midnight after it
        assert rec.get_highest_period_no() == 1, 'midnight sealed a forex day'

    def test_a_quiet_stretch_across_two_boundaries_books_both_days(self):
        """
        The cache says when the day ENDS, never that a tick must arrive before then. A feed that
        goes silent for two days and returns seals the day that was running AND the day it never
        heard from — every trading day has its row, whichever event noticed the boundaries.
        """
        rec = _recorder()
        rec.check_boundary(_MON + timedelta(hours=1), _seal_source)
        rec.check_boundary(_MON + timedelta(days=2, hours=3), _seal_source)

        assert rec.get_highest_period_no() == 2

    def test_no_anchor_books_nothing(self):
        """An internal harness builds a scenario with no broker; it has no market and no day."""
        rec = BookingPeriodRecorder('unit', None)
        rec.check_boundary(_MON + timedelta(days=5), _seal_source)

        assert rec.get_highest_period_no() == 0


class TestTheSealIsStampedWithTheBoundary:
    """
    A day ends at its boundary, not when somebody noticed. A heartbeat 300 ms late and a backtest
    whose next tick comes seven seconds later stamp the same instant, and the next period opens
    there — so a live session and its backtest book the same span (#476).
    """

    def test_a_late_check_still_closes_the_day_at_midnight(self):
        rec = _recorder()
        rec.check_boundary(_MON + timedelta(hours=1), _seal_source)
        rec.check_boundary(_MON + timedelta(days=1, seconds=7), _seal_source)
        periods = rec.close(_MON + timedelta(days=1, hours=1), _seal_source)

        assert periods[0].closed_at == _MON + timedelta(days=1)
        assert periods[1].opened_at == _MON + timedelta(days=1)

    def test_the_seal_reads_the_account_before_the_crossing_event(self):
        """
        The check runs before the event is applied, so what the seal reads is what the previous
        event left — and the crossing event's value opens the new day's band.
        """
        rec = _recorder()
        rec.check_boundary(_MON + timedelta(hours=1), _valued(10_000.0))
        rec.observe_tick(10_000.0)
        rec.check_boundary(_MON + timedelta(days=1, seconds=1), _valued(10_000.0))
        rec.observe_tick(9_980.0)                  # the crossing tick gaps down
        periods = rec.close(_MON + timedelta(days=1, hours=1), _valued(9_980.0))

        assert periods[0].figures.final_equity == 10_000.0
        assert periods[0].period_max_drawdown == 0.0
        assert periods[1].period_max_drawdown == 20.0


class TestEveryTradingDayGetsItsPeriod:
    """
    Which days get a period is the market calendar's answer, asked the same way in both
    pipelines: every day for a market that never closes, only the days it trades for one that
    does. A trading day inside a data gap still gets its row — no tick, nothing realised, the
    valuation it was left at — so a metric over the rows can tell it from a quiet day.
    """

    def test_a_day_the_run_never_heard_from_has_its_row(self):
        rec = _recorder()
        rec.check_boundary(_MON + timedelta(hours=1), _valued(10_000.0))
        rec.observe_tick(10_000.0)
        rec.check_boundary(_MON + timedelta(days=2, hours=3), _valued(10_000.0))
        periods = rec.close(_MON + timedelta(days=2, hours=4), _valued(10_000.0))

        tuesday = periods[1]
        assert (tuesday.opened_at, tuesday.closed_at) == (
            _MON + timedelta(days=1), _MON + timedelta(days=2))
        assert tuesday.trading_day == date(2026, 9, 22)
        assert tuesday.tick_count == 0
        assert tuesday.trade_count == 0
        assert tuesday.period_opening_equity == tuesday.figures.final_equity == 10_000.0
        assert (tuesday.period_min_equity, tuesday.period_max_equity) == (10_000.0, 10_000.0)

    def test_a_forex_weekend_books_no_row_and_leaves_no_hole(self):
        """
        Thursday's session ends at the Friday close in New York; the next trading day is the
        session that opens Sunday evening. The period that books it opens at the Friday close —
        so nothing that happens over the weekend can fall between two periods.
        """
        rec = _recorder(_FOREX)
        rec.check_boundary(_ny(2026, 10, 22, 18), _seal_source)              # Thursday's session
        rec.check_boundary(_ny(2026, 10, 23, 17, 5), _seal_source)            # just past the close
        rec.check_boundary(_ny(2026, 10, 24, 12), _seal_source)               # Saturday
        rec.check_boundary(_ny(2026, 10, 25, 17, 5), _seal_source)            # Sunday's open
        periods = rec.close(_ny(2026, 10, 26, 10), _seal_source)

        assert [p.trading_day for p in periods] == [date(2026, 10, 22), date(2026, 10, 25)]
        assert periods[0].closed_at == periods[1].opened_at == _ny(2026, 10, 23, 17)

    def test_a_backtest_silent_over_the_weekend_books_the_same_rows(self):
        """No heartbeat between Friday's last tick and Sunday's first: the same two days."""
        rec = _recorder(_FOREX)
        rec.check_boundary(_ny(2026, 10, 22, 18), _seal_source)
        rec.check_boundary(_ny(2026, 10, 23, 16, 59), _seal_source)           # Friday's last tick
        rec.check_boundary(_ny(2026, 10, 25, 17, 5), _seal_source)            # Sunday's first
        periods = rec.close(_ny(2026, 10, 26, 10), _seal_source)

        assert [p.trading_day for p in periods] == [date(2026, 10, 22), date(2026, 10, 25)]
        assert periods[0].closed_at == _ny(2026, 10, 23, 17)

    def test_a_forex_session_started_on_saturday_books_sunday_s_session(self):
        rec = _recorder(_FOREX)
        rec.check_boundary(_ny(2026, 10, 24, 12), _seal_source)

        assert rec.get_current_day() == date(2026, 10, 25)
        rec.check_boundary(_ny(2026, 10, 26, 17, 1), _seal_source)
        assert rec.get_highest_period_no() == 1

    def test_a_forex_holiday_books_no_row(self):
        """The session opening 24 December trades for Christmas Day: it is not a trading day."""
        rec = _recorder(_FOREX)
        rec.check_boundary(_ny(2026, 12, 23, 18), _seal_source)               # Wednesday's session
        rec.check_boundary(_ny(2026, 12, 28, 9), _seal_source)                # Monday morning
        periods = rec.close(_ny(2026, 12, 28, 10), _seal_source)

        assert [p.trading_day for p in periods] == [date(2026, 12, 23), date(2026, 12, 27)]

    def test_a_market_that_never_closes_books_its_weekend(self):
        rec = _recorder()
        rec.check_boundary(datetime(2026, 10, 23, 12, tzinfo=timezone.utc), _seal_source)
        rec.check_boundary(datetime(2026, 10, 26, 1, tzinfo=timezone.utc), _seal_source)

        assert rec.get_highest_period_no() == 3               # Friday, Saturday, Sunday


class TestTheOrdinaryTickCostsOneComparison:
    """
    The hot-path half. `trading_day_of`, `boundary_opening` and `trade_date_of` are the timezone
    conversions; they may run when a period OPENS or when one is SEALED, and at no other time.
    """

    @staticmethod
    def _counted(monkeypatch):
        """Replace every timezone conversion in the recorder's namespace with a counting one."""
        calls = []
        for name in ('trading_day_of', 'boundary_opening', 'trade_date_of'):
            real = getattr(booking_period_recorder, name)

            def counting(first, anchor, _real=real):
                calls.append(first)
                return _real(first, anchor)

            monkeypatch.setattr(booking_period_recorder, name, counting)
        return calls

    def test_a_day_of_ticks_converts_only_when_it_opens(self, monkeypatch):
        calls = self._counted(monkeypatch)
        rec = _recorder(_FOREX)
        rec.check_boundary(_MON + timedelta(hours=1), _seal_source)
        opened = len(calls)

        # Up to 20:00 UTC — the session that opened Sunday evening in New York ends at 21:00
        for minute in range(1, 19 * 60, 30):
            rec.check_boundary(_MON + timedelta(hours=1, minutes=minute), _seal_source)

        # Every tick after the opening one is a comparison against the cached boundary
        assert len(calls) == opened, f'{len(calls) - opened} conversions inside one day'

    def test_crossing_a_boundary_converts_only_at_the_seal(self, monkeypatch):
        calls = self._counted(monkeypatch)
        rec = _recorder()
        rec.check_boundary(_MON + timedelta(hours=1), _seal_source)
        rec.check_boundary(_MON + timedelta(days=1, seconds=1), _seal_source)
        sealed = len(calls)

        for hour in range(2, 24, 2):
            rec.check_boundary(_MON + timedelta(days=1, hours=hour), _seal_source)

        assert len(calls) == sealed, f'{len(calls) - sealed} conversions after the seal'
        assert rec.get_highest_period_no() == 1


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
        rec = BookingPeriodRecorder('unit', _CRYPTO, log=said.append)
        rec.check_boundary(_MON + timedelta(hours=1), _seal_source)

        # A clock that went backwards: the close is asked for BEFORE the period opened.
        periods = rec.close(_MON, _seal_source)

        assert periods[0].closed_at == periods[0].opened_at
        assert any('BEFORE it opened' in line for line in said)

    def test_the_ordinary_seal_is_untouched(self):
        rec = BookingPeriodRecorder('unit', _CRYPTO, log=lambda _: None)
        rec.check_boundary(_MON + timedelta(hours=1), _seal_source)

        closed = _MON + timedelta(hours=5)
        periods = rec.close(closed, _seal_source)
        assert periods[0].closed_at == closed


class TestWhatAPeriodOpenedWith:
    """
    A period's opening equity is the previous period's close, read at the same instant — or a
    unit's first observed value for its first period. It is set from the value the seal READ, so
    it does not depend on the order around the boundary check: both pipelines observe first on a
    tick, while a heartbeat seal checks without observing at all.
    """

    @staticmethod
    def _source(equity: float):
        return lambda: ([], PeriodSnapshot(currency='USD', final_equity=equity))

    def test_observing_before_the_check_opens_at_the_value_the_seal_read(self):
        rec = _recorder()
        rec.observe_tick(10_000.0)
        rec.check_boundary(_MON + timedelta(hours=1), self._source(10_000.0))
        rec.observe_tick(10_050.0)
        rec.check_boundary(_MON + timedelta(days=1, seconds=1), self._source(10_050.0))
        rec.observe_tick(10_070.0)
        periods = rec.close(_MON + timedelta(days=1, hours=2), self._source(10_070.0))

        assert [p.period_opening_equity for p in periods] == [10_000.0, 10_050.0]

    def test_checking_before_observing_gives_the_same_openings(self):
        rec = _recorder()
        rec.check_boundary(_MON + timedelta(hours=1), self._source(10_000.0))
        rec.observe_tick(10_000.0)
        rec.check_boundary(_MON + timedelta(days=1, seconds=1), self._source(10_050.0))
        rec.observe_tick(10_050.0)
        periods = rec.close(_MON + timedelta(days=1, hours=2), self._source(10_070.0))

        assert [p.period_opening_equity for p in periods] == [10_000.0, 10_050.0]

    def test_the_band_of_the_next_period_starts_at_the_value_the_seal_read(self):
        """
        The day opens at 10,000.00, the first tick after the boundary values the account at
        9,980.00, then it recovers: the day fell 20.00 from its open, and its band says so. A band
        started at its first tick would read a decline of 0.00 (#476).
        """
        rec = _recorder()
        rec.check_boundary(_MON + timedelta(hours=1), self._source(10_000.0))
        rec.observe_tick(10_000.0)
        rec.check_boundary(_MON + timedelta(days=1, seconds=1), self._source(10_000.0))
        rec.observe_tick(9_980.0)
        rec.observe_tick(10_010.0)
        periods = rec.close(_MON + timedelta(days=1, hours=2), self._source(10_010.0))

        tuesday = periods[1]
        assert tuesday.period_opening_equity == 10_000.0
        assert (tuesday.period_min_equity, tuesday.period_max_equity) == (9_980.0, 10_010.0)
        assert tuesday.period_max_drawdown == 20.0

    def test_the_closing_value_lies_inside_its_own_band(self):
        """A fill a heartbeat resolved moves the account without a tick; the seal reads it."""
        rec = _recorder()
        rec.check_boundary(_MON + timedelta(hours=1), self._source(10_000.0))
        rec.observe_tick(10_000.0)
        rec.check_boundary(_MON + timedelta(days=1, seconds=1), self._source(9_960.0))
        periods = rec.close(_MON + timedelta(days=1, hours=2), self._source(9_960.0))

        monday = periods[0]
        assert monday.period_min_equity <= monday.figures.final_equity <= monday.period_max_equity
        assert monday.period_max_drawdown == 40.0

    def test_an_unread_seal_value_opens_the_next_period_with_nothing(self):
        rec = _recorder()
        rec.check_boundary(_MON + timedelta(hours=1), lambda: ([], PeriodSnapshot(currency='USD')))
        rec.check_boundary(_MON + timedelta(days=1, seconds=1),
                           lambda: ([], PeriodSnapshot(currency='USD')))
        rec.observe_tick(10_000.0)
        periods = rec.close(_MON + timedelta(days=1, hours=2), self._source(10_000.0))

        assert periods[0].figures.final_equity is None
        assert periods[1].period_opening_equity == 10_000.0

    def test_a_period_that_valued_nothing_has_no_band(self):
        """Spot, sealed by a heartbeat before the first tick priced the holdings (#476)."""
        rec = _recorder()
        rec.check_boundary(_MON + timedelta(hours=23), lambda: ([], PeriodSnapshot(currency='USD')))
        rec.check_boundary(_MON + timedelta(days=1, seconds=1),
                           lambda: ([], PeriodSnapshot(currency='USD')))
        periods = rec.close(_MON + timedelta(days=1, hours=1), self._source(10_000.0))

        first = periods[0]
        assert (first.period_min_equity, first.period_max_equity, first.period_max_drawdown) == (
            None, None, None)
        assert first.tick_count == 0

    def test_a_first_period_nothing_observed_in_opens_with_nothing(self):
        rec = _recorder()
        rec.check_boundary(_MON + timedelta(hours=1), self._source(10_000.0))
        periods = rec.close(_MON + timedelta(hours=2), self._source(10_000.0))

        assert periods[0].period_opening_equity is None


class TestEverySealIsHandedOn:
    """A live session renders each sealed period for its operator (#235) — handed over once."""

    def test_a_boundary_and_the_close_each_hand_over_their_period(self):
        handed = []
        rec = BookingPeriodRecorder('unit', _CRYPTO, on_seal=handed.append)
        rec.check_boundary(_MON + timedelta(hours=1), _seal_source)
        rec.check_boundary(_MON + timedelta(days=1, seconds=1), _seal_source)
        periods = rec.close(_MON + timedelta(days=1, hours=2), _seal_source)

        assert handed == periods
        assert [p.reason for p in handed] == [PeriodCloseReason.ANCHOR,
                                              PeriodCloseReason.SESSION_END]

    def test_the_listener_sees_a_recorder_that_is_consistent_again(self):
        """Called last: the period is filed and counted before anyone is told."""
        seen = []
        rec = BookingPeriodRecorder(
            'unit', _CRYPTO,
            on_seal=lambda period: seen.append((period.period_no, rec.get_highest_period_no())))
        rec.check_boundary(_MON + timedelta(hours=1), _seal_source)
        rec.check_boundary(_MON + timedelta(days=1, seconds=1), _seal_source)

        assert seen == [(1, 1)]

    def test_the_listener_reads_the_day_that_has_begun(self):
        """A daily loss limit rolling on the seal needs the new day, not the one that ended."""
        seen = []
        rec = BookingPeriodRecorder(
            'unit', _CRYPTO,
            on_seal=lambda period: seen.append((period.trading_day, rec.get_current_day())))
        rec.check_boundary(_MON + timedelta(hours=1), _seal_source)
        rec.check_boundary(_MON + timedelta(days=2, seconds=1), _seal_source)

        assert seen == [(date(2026, 9, 21), date(2026, 9, 22)),
                        (date(2026, 9, 22), date(2026, 9, 23))]


class TestTheTickCount:
    """How many ticks a period applied — zero is a day the market was not heard in at all."""

    def test_counts_every_tick_valued_or_not(self):
        rec = _recorder()
        rec.check_boundary(_MON + timedelta(hours=1), _seal_source)
        rec.observe_tick(10_000.0)
        rec.observe_tick(None)       # spot holdings not priced yet: the market was still heard
        periods = rec.close(_MON + timedelta(hours=2), _seal_source)

        assert periods[0].tick_count == 2

    def test_starts_again_with_each_period(self):
        rec = _recorder()
        rec.check_boundary(_MON + timedelta(hours=1), _seal_source)
        rec.observe_tick(10_000.0)
        rec.check_boundary(_MON + timedelta(days=1, seconds=1), _seal_source)
        periods = rec.close(_MON + timedelta(days=1, hours=2), _seal_source)

        assert [p.tick_count for p in periods] == [1, 0]
